/* ==========================================================================
 * pet.js — 大帅桌面宠物渲染器（v2：加载/动画性能优化）
 *
 * 用法（chat.html 集成）：
 *   1. <script src="pet.js"></script> 放在 chat.html 末尾（DOMContentLoaded 前）
 *   2. 在关键节点派发事件：
 *        window.dispatchEvent(new CustomEvent('pet:state', {detail:{state:'thinking'}}));
 *        window.dispatchEvent(new CustomEvent('pet:state', {detail:{state:'speaking'}}));
 *        window.dispatchEvent(new CustomEvent('pet:state', {detail:{state:'idle'}}));
 *
 * 维护（核心）：
 *   - 换视频：重跑 pet_process.py 覆盖 pages/pet/<state>.webp，然后把 ASSET_VERSION 加 1
 *     （webp 走长缓存，靠版本号刷新；不 bump 用户会看到旧素材）
 *   - 加情绪：PET_META 数组里加一行 + 放一个 webp，菜单自动多一项
 *   - 删情绪：PET_META 删一行即可
 *
 * v2 性能要点：
 *   - 去掉 Date.now() 防缓存（每次刷新都重下 8MB）→ 固定 ASSET_VERSION + 服务端长缓存
 *   - 挂载后空闲时段预加载全部情绪，切换零等待
 *   - 去掉 .pet-img 的 drop-shadow 滤镜（动画 WebP 每帧重绘滤镜，最贵的开销）
 *   - speaking 无独立素材：只加 CSS 动效，不再发 404 请求
 *   - 状态切换淡入，拖拽期间关闭过渡
 *
 * v3 画质要点（配合 pet_process.py v3，素材升级为 384px/q82 + 边缘去色边）：
 *   - .pet-img 显式 image-rendering:auto（双线性插值，浏览器各向异性过滤由引擎自动开启；
 *     锐利度主要靠素材分辨率 384px > 显示尺寸 160px × DPR，见下方 ASSET_PX 说明）
 *   - 小屏(mini)与常规显示尺寸不变，交互逻辑零改动
 *
 * v4 智能定位要点（不用手动挪位）：
 *   - 首次/切屏自动落在「顶栏与输入栏之间」安全区的右下角，绝不压输入栏/发送键
 *   - 浮层（设置/确认/附件菜单/灯箱）打开时自动让位，关闭后归位
 *   - 空闲一段时间会自己“漫步”：沿安全区右侧底部歇歇走走，像活物
 *   - 手动拖走仍尊重你的自定义位置；右键菜单可随时「回到智能位置」
 * ========================================================================== */
(function () {
  'use strict';

  /* ---------- 配置：换视频不动这里；加情绪加一行 ---------- */
  var ASSET_VERSION = '3';         // 重新生成 webp 后 bump，配合长缓存刷新
  /* 素材分辨率说明：pet_process.py 默认输出 384x384。
     桌宠容器 160px（2x/3x DPR 屏对应 320/480 物理像素），384px 素材保证：
     DPR≤2 超采样锐利，DPR=3 基本持平，边缘由 LANCZOS + 浏览器双线性共同抗锯齿。 */
  var PET_META = [
    { state: 'idle',     label: '空闲',   group: '基础' },
    { state: 'happy',    label: '开心',   group: '基础' },
    { state: 'sad',      label: '难过',   group: '基础' },
    { state: 'angry',    label: '生气',   group: '基础' },
    { state: 'shy',      label: '害羞',   group: '基础' },
    { state: 'thinking', label: '思考',   group: '互动' },
    { state: 'pat',      label: '摸头',   group: '互动' }
    /* 以后加：{ state: 'surprised', label: '惊讶', group: '基础' } */
  ];
  var HAS_ASSET = {};              // 有 webp 素材的状态集合
  PET_META.forEach(function (p) { HAS_ASSET[p.state] = true; });
  var PRELOAD_ORDER = ['thinking', 'happy', 'pat', 'sad', 'angry', 'shy']; // 空闲预加载顺序
  var SIZE = 160;                  // 桌宠显示尺寸（px）
  var MINI_SIZE = 30;              // 最小化圆点尺寸（px）
  var LONG_PRESS_MS = 450;         // 长按弹菜单（移动端替代右键）
  var DRAG_THRESHOLD = 8;          // 位移超过此值视为拖动（px）
  var BUBBLE = {
    idle:     ['嗯？', '在呢~', '…', '☁️', '嗯哼'],
    happy:    ['嘿嘿~', '😄', '好开心'],
    sad:      ['呜呜…', 'T_T', '唉'],
    angry:    ['哼！', '气鼓鼓', '哼~'],
    shy:      ['别看啦~', '害羞', '唔…'],
    thinking: ['…', '🤔', '想想'],
    pat:      ['嗯~', '乖乖乖', '嘿嘿'],
    speaking: ['~', '嗯嗯']
  };
  var POS_KEY    = 'pet_pos_v1';
  var STATE_KEY  = 'pet_state_v1';
  var MINI_KEY   = 'pet_mini_v1';
  var DISMISS_BUBBLE_MS = 2200;

  /* ---------- 状态 ---------- */
  var _current = 'idle';
  var _manual  = false;   // 右键锁定后不被自动覆盖
  var _longPress = false; // 刚长按弹过菜单，单击不再弹气泡
  var _suppressClickUntil = 0; // 刚结束拖拽的时间戳：拖拽松手的合成 click 不弹气泡
  var _hasCustomPos = false;  // 恢复过自定义位置：resize/旋转屏幕时需重新钳制
  var _el, _img, _bubble, _stage;

  /* 把桌宠钳制在当前视口内（旋转屏幕/缩小窗口后保存的坐标可能已在屏幕外，
     不处理宠物会永久消失，只能清 localStorage 找回） */
  function clampToViewport() {
    if (!_el || !_hasCustomPos) return;
    var w = _el.offsetWidth || SIZE, h = _el.offsetHeight || SIZE;
    var maxX = Math.max(0, window.innerWidth - w);
    var maxY = Math.max(0, window.innerHeight - h);
    var r = _el.getBoundingClientRect();
    var x = Math.max(0, Math.min(maxX, r.left));
    var y = Math.max(0, Math.min(maxY, r.top));
    if (Math.abs(x - r.left) > 0.5 || Math.abs(y - r.top) > 0.5) {
      _el.style.left = x + 'px';
      _el.style.top = y + 'px';
      _el.style.right = 'auto';
      _el.style.bottom = 'auto';
      try { localStorage.setItem(POS_KEY, JSON.stringify({ x: x, y: y })); } catch (_) {}
    }
  }
  var _clampTimer = 0;
  function scheduleClamp() {
    if (_clampTimer) clearTimeout(_clampTimer);
    _clampTimer = setTimeout(function () {
      clampToViewport();
      /* v4 智能模式：窗口变完形后回落舒适位，而不是留在原先漫步的角落 */
      if (_auto && _el && !_el.classList.contains('mini')) {
        var a = smartAnchor();
        var r = _el.getBoundingClientRect();
        if (Math.abs(a.x - r.left) > 2 || Math.abs(a.y - r.top) > 2) glideTo(a.x, a.y);
      }
    }, 120);
  }

  /* ==========================================================================
   * v4 智能定位引擎
   *   安全区 = 顶栏(.chat-top)下方 ~ 输入栏(.inputbar)上方，右缘贴近视口右侧。
   *   智能落点永远在安全区内，浮层打开时让位，空闲时缓慢“漫步”。
   * ========================================================================== */
  var WANDER_DELAY = 9000;              // 空闲多久开始漫步（ms）
  var SIT_MIN = 2600, SIT_MAX = 8200;   // 走到目标后的歇息时长范围（ms）
  var _auto = true;                     // 智能定位模式：手动拖拽后关闭
  var _glideRaf = 0, _gliding = false;
  var _wanderTimer = 0, _sitTimer = 0;
  var _dodged = false;              // 正在躲附件菜单，复位时需归位

  /* 可活动安全区（实时量测，自适应桌面/移动/键盘弹起） */
  function shieldRect() {
    var vw = window.innerWidth, vh = window.innerHeight;
    var x0 = 8, x1 = vw - SIZE - 8, y0 = 8, y1 = vh - SIZE - 8;
    var topEl = document.querySelector('.chat-top'), inpEl = document.querySelector('.inputbar');
    if (topEl) { var tr = topEl.getBoundingClientRect(); y0 = Math.max(y0, tr.bottom + 6); }
    if (inpEl) {
      var ir = inpEl.getBoundingClientRect();
      if (ir.top > 0) y1 = Math.min(y1, ir.top - 8);   // 贴着输入栏上方，绝不压发送键
    }
    /* 移动端侧边抽屉打开时别飘到它上面 */
    var side = document.querySelector('.side');
    if (side) {
      var sr = side.getBoundingClientRect();
      if (getComputedStyle(side).position === 'fixed' && sr.width > 0 && sr.right > 0) {
        x0 = Math.max(x0, sr.right + 8);
      }
    }
    /* 空间被键盘/安全区压没了（如输入栏顶到屏底附近），退回视口兜底 */
    if (y1 - y0 < SIZE + 24) { y0 = 12; y1 = vh - SIZE - 12; }
    return { x0: x0, x1: Math.max(x0, x1), y0: y0, y1: Math.max(y0, y1) };
  }
  /* 写位置前一律钳进安全区（自动模式下永不飘出屏幕/压住输入栏） */
  function setPos(x, y) {
    if (!_el) return;
    var s = shieldRect();
    x = Math.max(s.x0, Math.min(s.x1, x));
    y = Math.max(s.y0, Math.min(s.y1, y));
    _el.style.left = x + 'px'; _el.style.top = y + 'px';
    _el.style.right = 'auto'; _el.style.bottom = 'auto';
  }
  /* 智能落点：安全区右下角（贴着输入栏上方，右侧贴边） */
  function smartAnchor() {
    var s = shieldRect();
    return { x: s.x1, y: s.y1 - 6 };
  }
  /* 平滑滑到目标点（rAF 指数趋近，可被任何打断取消） */
  function glideTo(tx, ty, onDone) {
    if (!_el || _el.classList.contains('mini')) return;
    stopGlide();
    var r = _el.getBoundingClientRect();
    var fx = r.left, fy = r.top;
    _gliding = true;
    (function step() {
      fx += (tx - fx) * 0.10; fy += (ty - fy) * 0.10;
      setPos(fx, fy);
      if (Math.abs(tx - fx) < 1.2 && Math.abs(ty - fy) < 1.2) {
        setPos(tx, ty);
        stopGlide();
        if (onDone) onDone();
        return;
      }
      _glideRaf = requestAnimationFrame(step);
    })();
  }
  function stopGlide() {
    _gliding = false;
    if (_glideRaf) { cancelAnimationFrame(_glideRaf); _glideRaf = 0; }
  }
  function clearWanderTimer() {
    if (_wanderTimer) { clearTimeout(_wanderTimer); _wanderTimer = 0; }
    if (_sitTimer)   { clearTimeout(_sitTimer);   _sitTimer = 0; }
  }
  function overlayOpen() {
    var sels = ['#settings', '#confirm-modal', '#attach-menu', '#lightbox'];
    for (var i = 0; i < sels.length; i++) {
      var el = document.querySelector(sels[i]);
      if (el && getComputedStyle(el).display !== 'none' && el.offsetParent !== null) return true;
    }
    return false;
  }
  function inputFocused() {
    var a = document.activeElement;
    return !!(a && a.matches && a.matches('textarea,input,select'));
  }
  function canWander() {
    if (!_auto || !_el || _el.classList.contains('mini')) return false;
    if (inputFocused() || overlayOpen()) return false;
    if (_current === 'thinking' || _current === 'speaking') return false;
    if (document.visibilityState !== 'visible') return false;
    var side = document.querySelector('.side');
    if (side && side.classList.contains('open')) return false;  // 抽屉开着不闹
    return true;
  }
  function resumeWanderAfter(ms) {
    clearWanderTimer();
    _wanderTimer = setTimeout(planWander, ms);
  }
  function planWander() {
    if (!canWander()) return;
    stopGlide();
    var s = shieldRect();
    var r = _el.getBoundingClientRect();
    var cx = r.left + SIZE / 2, cy = r.top + SIZE / 2;
    /* 新目标：偏右侧踱步（少盖聊天正文），多数贴着安全区底部，偶尔上探；步幅不大 */
    var lb = s.x0 + (s.x1 - s.x0) * 0.28;
    var tx = cx + (Math.random() * 2 - 1) * 180;
    tx = Math.max(lb, Math.min(s.x1, tx));
    var low = s.y1, up = s.y0 + (s.y1 - s.y0) * 0.35;
    var ty = (Math.random() < 0.7) ? (low + r.top) / 2 : (up + cy) / 2;
    ty = Math.max(s.y0, Math.min(s.y1, ty));
    glideTo(tx, ty, function () {
      if (!canWander()) return;
      clearWanderTimer();
      _sitTimer = setTimeout(function () { if (canWander()) planWander(); },
                             SIT_MIN + Math.random() * (SIT_MAX - SIT_MIN));
    });
  }
  /* 用户有动作 / 状态切换：先停下，安静一会儿再考虑走两步 */
  function wanderIdleReset() {
    if (!_auto) return;
    pauseWander();
    if (_el && _el.classList.contains('mini')) return;
    dodgeCheck();                 // 顺手看一眼附件菜单是否挡住了宠物
    resumeWanderAfter(WANDER_DELAY);
  }
  function pauseWander() {
    stopGlide();
    clearWanderTimer();
  }
  function rectOverlap(a, b, pad) {
    pad = pad || 12;
    return !(a.right - pad < b.left || b.right < a.left + pad ||
             a.bottom - pad < b.top || b.bottom < a.top + pad);
  }
  /* 附件菜单（z-index 高于宠物、非全屏、贴着输入栏）打开且压住宠物时让位；
     全屏浮层（设置/灯箱）在宠物之上自成图层，直接被盖住，无需躲闪 */
  function dodgeCheck() {
    if (!_auto || !_el || _el.classList.contains('mini')) return;
    var menu = document.querySelector('#attach-menu');
    if (!menu) { _dodged = false; return; }
    var petR = _el.getBoundingClientRect();
    var menuOpen = getComputedStyle(menu).display !== 'none' && menu.offsetParent !== null;
    if (menuOpen) {
      var mr = menu.getBoundingClientRect();
      if (rectOverlap(petR, mr)) {
        var a = smartAnchor();
        var ar = { left: a.x, top: a.y, right: a.x + SIZE, bottom: a.y + SIZE };
        if (rectOverlap(ar, mr, 8)) {      // 右下角也被占住时挪到左下角
          var s = shieldRect();
          a = { x: s.x0, y: s.y1 };
        }
        _dodged = true;
        glideTo(a.x, a.y);
        return;
      }
    } else if (_dodged) {                  // 菜单收起来了，坐回老位置
      _dodged = false;
      var anchor = smartAnchor();
      var r = _el.getBoundingClientRect();
      if (Math.abs(anchor.x - r.left) > 4 || Math.abs(anchor.y - r.top) > 4) {
        glideTo(anchor.x, anchor.y);
      }
    }
  }
  /* 常驻低频巡检：附件菜单是异步弹/收的，事件驱动可能漏，兜底轮询 */
  function startDodgeTimer() {
    setInterval(dodgeCheck, 1100);
  }

  /* ---------- 注入 CSS ---------- */
  function injectStyle() {
    if (document.getElementById('pet-style')) return;
    var s = document.createElement('style');
    s.id = 'pet-style';
    s.textContent = [
      '#pet{position:fixed;right:18px;bottom:calc(env(safe-area-inset-bottom, 0px) + 18px);width:' + SIZE + 'px;height:' + SIZE + 'px;',
      'z-index:60;user-select:none;-webkit-user-select:none;touch-action:none;cursor:grab;',
      'transition:width .2s ease,height .2s ease}',
      '#pet:active{cursor:grabbing}',
      '#pet.dragging{transition:none}',
      '#pet .pet-stage{position:absolute;inset:0;border-radius:50%;overflow:hidden;',
      'background:radial-gradient(circle at 30% 30%, rgba(255,255,255,.08), transparent 60%);',
      'box-shadow:0 6px 22px -8px rgba(0,0,0,.45),0 0 0 1px rgba(255,255,255,.06);',
      'transition:transform .25s cubic-bezier(.2,.7,.2,1)}',
      '#pet:hover .pet-stage{transform:scale(1.04)}',
      '#pet[data-state="thinking"] .pet-stage{animation:pet-think 1.2s ease-in-out infinite}',
      '#pet[data-state="speaking"] .pet-stage{animation:pet-speak .5s ease-in-out infinite alternate}',
      /* 注意：不加 filter/drop-shadow —— 动画 WebP 每帧重绘滤镜非常贵，
         立体感交给 .pet-stage 的 box-shadow */
      '#pet .pet-img{width:100%;height:100%;object-fit:contain;display:block;',
      /* image-rendering:auto = 双线性（清晰度靠素材 384px 超采样 + 浏览器自动各向异性过滤），
         不要设 pixelated（会出锯齿）或 crisp-edges（会糊透明渐变） */
      'image-rendering:auto;',
      'animation:pet-fadein .18s ease}',
      '#pet .pet-bubble{position:absolute;left:-6px;top:-14px;max-width:140px;min-width:24px;',
      'background:var(--card,#fff);color:var(--foreground,#0a0a0a);font-size:13px;line-height:1.4;',
      'padding:6px 10px;border-radius:10px;border:1px solid var(--border,#e5e5e5);',
      'box-shadow:0 4px 14px -4px rgba(0,0,0,.18);opacity:0;transform:translateY(4px) scale(.9);',
      'pointer-events:none;transition:opacity .2s ease,transform .2s ease}',
      '#pet .pet-bubble.show{opacity:1;transform:translateY(0) scale(1)}',
      '#pet .pet-bubble::after{content:"";position:absolute;left:18px;bottom:-6px;',
      'width:10px;height:10px;background:inherit;border-right:1px solid var(--border,#e5e5e5);',
      'border-bottom:1px solid var(--border,#e5e5e5);transform:rotate(45deg)}',
      '@keyframes pet-think{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(3deg)}}',
      '@keyframes pet-speak{from{transform:scale(1)}to{transform:scale(1.03)}}',
      '@keyframes pet-fadein{from{opacity:.35}to{opacity:1}}',
      /* 最小化圆点 */
      '#pet.mini{width:' + MINI_SIZE + 'px;height:' + MINI_SIZE + 'px;right:12px;bottom:calc(env(safe-area-inset-bottom, 0px) + 12px)}',
      '#pet.mini .pet-stage{border-radius:50%;background:var(--chart-2,#FF8A3D);',
      'box-shadow:0 2px 10px -2px rgba(0,0,0,.5)}',
      '#pet.mini .pet-img{display:none}',
      '#pet.mini .pet-bubble{display:none}',
      '#pet.mini::after{content:"🐾";position:absolute;inset:0;display:flex;align-items:center;',
      'justify-content:center;font-size:15px}',
      /* 右键菜单 */
      '#pet-ctx{position:fixed;z-index:70;min-width:140px;',
      'background:var(--popover,#fff);color:var(--popover-foreground,#0a0a0a);',
      'border:1px solid var(--border,#e5e5e5);border-radius:10px;',
      'box-shadow:0 12px 36px -10px rgba(0,0,0,.25);padding:4px;',
      'opacity:0;transform:scale(.96);transform-origin:top left;pointer-events:none;',
      'transition:opacity .15s ease,transform .15s ease}',
      '#pet-ctx.show{opacity:1;transform:scale(1);pointer-events:auto}',
      '#pet-ctx .pet-ctx-group{font-size:11px;color:var(--muted-foreground,#666);',
      'padding:4px 10px 2px;letter-spacing:.04em}',
      '#pet-ctx .pet-ctx-item{padding:7px 10px;font-size:13px;border-radius:6px;cursor:pointer;',
      'display:flex;align-items:center;justify-content:space-between}',
      '#pet-ctx .pet-ctx-item:hover{background:var(--accent,#f5f5f5)}',
      '#pet-ctx .pet-ctx-item.active{background:var(--accent,#f5f5f5);font-weight:600}',
      '#pet-ctx .pet-ctx-item.missing{opacity:.4;cursor:default}',
      '#pet-ctx .pet-ctx-sep{height:1px;background:var(--border,#e5e5e5);margin:4px 0}',
      '#pet-ctx .pet-ctx-danger{color:#E8605A}',
      /* 移动端适配：小屏缩小 + 菜单满宽；默认位置抬到输入栏上方，不遮发送键 */
      '@media (max-width: 640px){',
      '  #pet:not(.mini){width:120px;height:120px;bottom:calc(env(safe-area-inset-bottom, 0px) + 84px)}',
      '  #pet.mini{bottom:calc(env(safe-area-inset-bottom, 0px) + 78px)}',
      '  #pet .pet-bubble{font-size:12px;max-width:120px}',
      '  #pet-ctx{left:8px !important;right:8px;width:auto;min-width:0;',
      '  transform-origin:bottom center}',
      '}',
      '@media (prefers-color-scheme: dark){',
      '  #pet .pet-bubble,#pet-ctx{background:#17131A;color:#F2EAE2;border-color:#2A2331}',
      '  #pet-ctx .pet-ctx-item:hover{background:#272030}',
      '}'
    ].join('');
    document.head.appendChild(s);
  }

  /* ---------- 图片懒加载缓存 ---------- */
  var _cache = {};
  function loadImg(state) {
    if (_cache[state]) return _cache[state];
    var p = new Promise(function (resolve) {
      var img = new Image();
      img.className = 'pet-img';
      img.draggable = false;
      img.decoding = 'async';
      img.src = 'pet/' + state + '.webp?v=' + ASSET_VERSION;  // 固定版本，可被长缓存
      img.onload  = function () { resolve(img); };
      /* 失败不缓存：网络抖一下就把某情绪的失败 Promise 永久留住，
         会导致该情绪裂图直到刷新页面；删掉缓存让下次切换重试 */
      img.onerror = function () { delete _cache[state]; resolve(null); };
    });
    _cache[state] = p;
    return p;
  }

  /* ---------- 空闲预加载：切换零等待 ---------- */
  function preloadRest() {
    /* 素材总量 6MB+：省流量模式 / 弱网（2g）下不预载，各情绪按需加载即可 */
    var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (conn && (conn.saveData || /(^|-)2g$/.test(conn.effectiveType || ''))) return;
    var queue = PRELOAD_ORDER.slice();
    function next() {
      var st = queue.shift();
      if (!st) return;
      loadImg(st).then(function () {
        if (window.requestIdleCallback) window.requestIdleCallback(next, { timeout: 2000 });
        else setTimeout(next, 300);
      });
    }
    if (window.requestIdleCallback) window.requestIdleCallback(next, { timeout: 2000 });
    else setTimeout(next, 800);
  }

  /* ---------- 状态切换 ---------- */
  function applyState(state) {
    if (!state || state === _current) return;
    _current = state;
    if (_el) _el.dataset.state = state;
    if (!HAS_ASSET[state]) return;   // speaking 等无素材状态：只加 CSS 动效，不发 404
    /* mount 前收到事件（外部在 DOMContentLoaded 前派发 pet:state）时 _stage 未初始化：
       静默跳过，不抛 TypeError */
    if (!_stage) return;
    loadImg(state).then(function (img) {
      if (!img) return;              // 文件不存在
      if (state !== _current) return; // 加载期间又切走了，别盖
      if (!_stage) return;           // 卸载时序防御
      if (_img) _img.remove();
      _img = img;
      _stage.appendChild(_img);
    });
  }

  /* ---------- 情绪闪现：短动画后回到原状态（摸头/拖后害羞）。
     token 机制保证：期间聊天状态事件（thinking/speaking/idle）优先，不被覆盖回去 ---------- */
  var _moodToken = 0;
  function moodBurst(stateName, ms) {
    if (!HAS_ASSET[stateName]) return;
    var my = ++_moodToken;
    var prev = _current || 'idle';
    applyState(stateName);
    setTimeout(function () {
      if (_moodToken !== my) return;  /* 期间有新闪现或聊天状态事件 */
      _moodToken++;
      if (_current === stateName) applyState(prev === '' ? 'idle' : prev);
    }, ms);
  }

  /* ---------- 气泡 ---------- */
  function popBubble(text) {
    if (!_bubble) return;
    _bubble.textContent = text;
    _bubble.classList.add('show');
    clearTimeout(_bubble._t);
    _bubble._t = setTimeout(function () { _bubble.classList.remove('show'); }, DISMISS_BUBBLE_MS);
  }

  /* ---------- 拖拽 + 长按弹菜单 ---------- */
  function makeDraggable() {
    var drag = false, ox = 0, oy = 0, moved = false;
    var pressTimer = null, startX = 0, startY = 0, activePointerId = null;
    function clearPress() {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    }
    _el.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 && e.pointerType === 'mouse') return;  // 右键交给 contextmenu
      // 双指/多指：只有第一指接管拖拽，第二指落下直接忽略，
      // 否则两指坐标互相覆盖会让宠物跳动、第二指还可能误弹长按菜单
      if (!e.isPrimary) return;
      // 自愈：activePointerId 残留但 drag 已为 false（capture 失败、系统手势抢走指针，
      // 导致 pointerup 没回到本元素），若继续 return 会永久卡死拖拽+长按直到刷新
      if (activePointerId !== null) {
        if (drag) return;
        activePointerId = null;
      }
      activePointerId = e.pointerId;
      drag = true; moved = false;
      startX = e.clientX; startY = e.clientY;
      var r = _el.getBoundingClientRect();
      ox = e.clientX - r.left; oy = e.clientY - r.top;
      try { _el.setPointerCapture(e.pointerId); } catch (_) {
        /* capture 失败不致命也不重试：下面把 pointerup/pointercancel 同时挂在 window 上兜底 */
      }
      /* 长按 450ms = 弹菜单（移动端无右键，长按替代） */
      clearPress();
      pressTimer = setTimeout(function () {
        pressTimer = null;
        _longPress = true;
        showCtx(e);
      }, LONG_PRESS_MS);
    });
    _el.addEventListener('pointermove', function (e) {
      if (e.pointerId !== activePointerId) return;  // 非活动手指的事件一律忽略
      if (!drag) return;
      if (!moved && (Math.abs(e.clientX - startX) > DRAG_THRESHOLD ||
                     Math.abs(e.clientY - startY) > DRAG_THRESHOLD)) {
        clearPress();               // 在拖 → 不是长按
        moved = true;
        hideCtx();                  // 菜单按旧坐标定位，宠物一动就必须收起
        _el.classList.add('dragging');
      }
      if (!moved) return;
      var w = _el.offsetWidth, h = _el.offsetHeight;
      var x = Math.max(0, Math.min(window.innerWidth  - w, e.clientX - ox));
      var y = Math.max(0, Math.min(window.innerHeight - h, e.clientY - oy));
      _el.style.left = x + 'px';
      _el.style.top  = y + 'px';
      _el.style.right = 'auto'; _el.style.bottom = 'auto';
    });
    function endDrag(e) {
      // 第二指误触的 pointerup 不能结束第一指的拖拽：先比 pointerId
      if (e && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
      if (!drag) return;
      drag = false;
      activePointerId = null;
      clearPress();
      _el.classList.remove('dragging');
      if (moved) {
        _suppressClickUntil = Date.now() + 300;  // 松手的合成 click 不弹气泡
        /* 合成 click 会先在 _suppressClickUntil 判断处 return，走不到清 _longPress 的那行；
           这里不清，则「长按→拖拽→松手」之后用户的下一次真实单击会被凭空吃掉 */
        _longPress = false;
        try {
          var r = _el.getBoundingClientRect();
          localStorage.setItem(POS_KEY, JSON.stringify({ x: r.left, y: r.top }));
        } catch (_) {}
        /* 拖过即为自定义位置：不打标记，旋转屏幕/缩小窗口时 clampToViewport 直接 return，
           宠物会留在新视口外，按代码自己的说法只能清 localStorage 找回。
           放 try 外：localStorage 写失败（无痕/配额）时内存里的位置同样需要钳制 */
        _hasCustomPos = true;
        /* v4：手动拖过 = 退出智能定位，尊重用户自定义位置，不再自动漫步 */
        _auto = false;
        pauseWander();
        moodBurst('shy', 1500);  /* 被拖走后害羞一下 */
      }
    }
    _el.addEventListener('pointerup', endDrag);
    _el.addEventListener('pointercancel', endDrag);
    /* capture 失败或被系统手势抢走指针时，pointerup 只会派发到别处（宠物被钳制在视口内，
       手指却能移出元素），只挂 _el 会让 drag 状态永久卡死；window 上再兜一层。
       桌宠与页面同生命周期、无卸载路径，故不需 removeEventListener。 */
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
    /* 捕获被释放（浏览器/系统收回指针）时也收尾，不依赖 pointerup 是否到达 */
    _el.addEventListener('lostpointercapture', endDrag);
  }

  /* ---------- 最小化 / 恢复 ---------- */
  function setMini(on) {
    _el.classList.toggle('mini', !!on);
    if (on) pauseWander();                    // 圆点状态不漫步
    else if (_auto) resumeWanderAfter(WANDER_DELAY);
    try {
      if (on) localStorage.setItem(MINI_KEY, '1');
      else localStorage.removeItem(MINI_KEY);
    } catch (_) {}
  }

  /* ---------- 右键菜单 ---------- */
  function hideCtx() {
    var m = document.getElementById('pet-ctx');
    if (m) m.classList.remove('show');
  }
  function ensureCtxMenu() {
    var m = document.getElementById('pet-ctx');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'pet-ctx';
    document.body.appendChild(m);
    document.addEventListener('click', function (e) {
      if (m.classList.contains('show') && !m.contains(e.target) && !_el.contains(e.target)) {
        m.classList.remove('show');
      }
    });
    window.addEventListener('blur', function () { m.classList.remove('show'); });
    return m;
  }
  function showCtx(e) {
    e.preventDefault(); e.stopPropagation();
    var m = ensureCtxMenu();
    m.innerHTML = '';
    var groups = {};
    PET_META.forEach(function (p) { (groups[p.group] = groups[p.group] || []).push(p); });
    Object.keys(groups).forEach(function (g) {
      var h = document.createElement('div');
      h.className = 'pet-ctx-group'; h.textContent = g;
      m.appendChild(h);
      groups[g].forEach(function (p) {
        var item = document.createElement('div');
        item.className = 'pet-ctx-item' + (p.state === _current ? ' active' : '');
        item.textContent = p.label;
        item.addEventListener('click', function () {
          _manual = true;
          applyState(p.state);
          try { localStorage.setItem(STATE_KEY, p.state); } catch (_) {}
          m.classList.remove('show');
        });
        m.appendChild(item);
      });
    });
    var sep = document.createElement('div'); sep.className = 'pet-ctx-sep'; m.appendChild(sep);
    /* v4：手动拖过位置后出现「回到智能位置」，一键恢复自动落位/漫步 */
    if (!_auto) {
      var reauto = document.createElement('div');
      reauto.className = 'pet-ctx-item'; reauto.textContent = '回到智能位置';
      reauto.addEventListener('click', function () {
        _auto = true; _hasCustomPos = false;
        try { localStorage.removeItem(POS_KEY); } catch (_) {}
        m.classList.remove('show');
        stopGlide();
        var a = smartAnchor();
        glideTo(a.x, a.y, function () { resumeWanderAfter(WANDER_DELAY); });
      });
      m.appendChild(reauto);
    }
    if (_manual) {
      var auto = document.createElement('div');
      auto.className = 'pet-ctx-item'; auto.textContent = '解锁 · 跟随聊天自动';
      auto.addEventListener('click', function () {
        _manual = false;
        applyState('idle');
        try { localStorage.removeItem(STATE_KEY); } catch (_) {}
        m.classList.remove('show');
      });
      m.appendChild(auto);
    }
    var hide = document.createElement('div');
    hide.className = 'pet-ctx-item'; hide.textContent = '最小化';
    hide.addEventListener('click', function () {
      setMini(true);
      m.classList.remove('show');
    });
    m.appendChild(hide);
    /* 定位：超出屏幕时翻转；窄窗口兜底 clamp 到 ≥8px（旧实现窗口 <180px 时 x 为负，菜单溢出屏幕左缘） */
    var mw = m.offsetWidth || 180;
    var mh = m.offsetHeight || 320;
    var x = Math.max(8, Math.min(e.clientX, window.innerWidth - mw - 8));
    var y = Math.max(8, Math.min(e.clientY, window.innerHeight - mh - 8));
    m.style.left = x + 'px'; m.style.top = y + 'px';
    m.classList.add('show');
  }

  /* ---------- 挂载 ---------- */
  function mount() {
    injectStyle();
    _el = document.createElement('div');
    _el.id = 'pet';
    _el.innerHTML = '<div class="pet-bubble"></div><div class="pet-stage"></div>';
    document.body.appendChild(_el);
    _bubble = _el.querySelector('.pet-bubble');
    _stage  = _el.querySelector('.pet-stage');
    /* 恢复位置 / 最小化 / 手动状态 */
    try {
      var pos = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
      /* x/y 都要校验：只查 x 时，y 为 undefined/null 会把 style.top 写成 "undefinedpx"
         静默失效——宠物落回 CSS 默认位置，却已被标记成自定义位置 */
      if (pos && typeof pos.x === 'number' && typeof pos.y === 'number') {
        _hasCustomPos = true;
        _el.style.left = pos.x + 'px'; _el.style.top = pos.y + 'px';
        _el.style.right = 'auto'; _el.style.bottom = 'auto';
      }
      if (localStorage.getItem(MINI_KEY) === '1') setMini(true);
      var saved = localStorage.getItem(STATE_KEY);
      if (saved && PET_META.some(function (p) { return p.state === saved; })) {
        _manual = true; _current = saved;
      }
    } catch (_) {}
    _el.addEventListener('contextmenu', showCtx);
    _el.addEventListener('click', function (e) {
      if (e.button !== 0) return;
      if (Date.now() < _suppressClickUntil) return;      // 拖拽松手的合成 click，不弹气泡
      if (_longPress) { _longPress = false; return; }   // 长按弹完菜单，不弹气泡
      if (_el.classList.contains('mini')) {             // 圆点：点击恢复
        setMini(false);
        popBubble('回来啦~');
        return;
      }
      var pool = (BUBBLE[_current] || BUBBLE.id).slice();
      pool.push(_current);
      popBubble(pool[Math.floor(Math.random() * pool.length)]);
      moodBurst('pat', 1800);  /* 摸头动画 1.8s，随后回到聊天状态 */
    });
    makeDraggable();
    var initial = _current;
    _current = '';                // 初始状态强制加载（applyState 对相同状态去重）
    applyState(initial);
    preloadRest();
    /* 恢复的保存位置按当前视口再钳制一次；旋转屏幕/窗口缩放时同样钳制，
       防止横屏拖到边缘、切竖屏后宠物永久停在屏幕外 */
    clampToViewport();
    /* v4 智能定位：没有用户拖过/存过的坐标 → 自动落位 + 空闲漫步；
       有自定义坐标 → 尊重手动位置，退出智能模式
       活动监听常驻注册：菜单「回到智能位置」切回自动模式后同样生效 */
    if (!_hasCustomPos) {
      _auto = true;
      var anchor = smartAnchor();
      setPos(anchor.x, anchor.y);
      resumeWanderAfter(WANDER_DELAY);
    } else {
      _auto = false;
    }
    /* 用户有动作（点击/聚焦/切回标签）就重置空闲时钟：刚操作过不立刻乱跑 */
    document.addEventListener('pointerdown', wanderIdleReset, true);
    document.addEventListener('focusin', wanderIdleReset, true);
    document.addEventListener('visibilitychange', wanderIdleReset);
    startDodgeTimer();
    window.addEventListener('resize', scheduleClamp);
    window.addEventListener('orientationchange', scheduleClamp);
  }

  /* ---------- 监听外部状态事件 ---------- */
  window.addEventListener('pet:state', function (e) {
    var s = e.detail && e.detail.state;
    if (s && !_manual) applyState(s);
    /* v4：思考/说话时乖乖站好，不漫步；回 idle 再考虑走两步 */
    if (s === 'thinking' || s === 'speaking') pauseWander();
    else if (s === 'idle' && _auto) resumeWanderAfter(WANDER_DELAY);
  });

  /* ---------- 启动 ---------- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
