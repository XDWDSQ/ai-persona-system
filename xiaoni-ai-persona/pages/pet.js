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
  var _el, _img, _bubble, _stage;

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
      '#pet.mini .pet-stage{border-radius:50%;background:var(--chart-2,#4f6ef7);',
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
      '#pet-ctx .pet-ctx-danger{color:#d33}',
      /* 移动端适配：小屏缩小 + 菜单满宽 */
      '@media (max-width: 640px){',
      '  #pet:not(.mini){width:120px;height:120px}',
      '  #pet .pet-bubble{font-size:12px;max-width:120px}',
      '  #pet-ctx{left:8px !important;right:8px;width:auto;min-width:0;',
      '  transform-origin:bottom center}',
      '}',
      '@media (prefers-color-scheme: dark){',
      '  #pet .pet-bubble,#pet-ctx{background:#171717;color:#fafafa;border-color:#262626}',
      '  #pet-ctx .pet-ctx-item:hover{background:#262626}',
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
      img.onerror = function () { resolve(null); };
    });
    _cache[state] = p;
    return p;
  }

  /* ---------- 空闲预加载：切换零等待 ---------- */
  function preloadRest() {
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
    var pressTimer = null, startX = 0, startY = 0;
    function clearPress() {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
    }
    _el.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 && e.pointerType === 'mouse') return;  // 右键交给 contextmenu
      drag = true; moved = false;
      startX = e.clientX; startY = e.clientY;
      var r = _el.getBoundingClientRect();
      ox = e.clientX - r.left; oy = e.clientY - r.top;
      try { _el.setPointerCapture(e.pointerId); } catch (_) {}
      /* 长按 450ms = 弹菜单（移动端无右键，长按替代） */
      clearPress();
      pressTimer = setTimeout(function () {
        pressTimer = null;
        _longPress = true;
        showCtx(e);
      }, LONG_PRESS_MS);
    });
    _el.addEventListener('pointermove', function (e) {
      if (!drag) return;
      if (!moved && (Math.abs(e.clientX - startX) > DRAG_THRESHOLD ||
                     Math.abs(e.clientY - startY) > DRAG_THRESHOLD)) {
        clearPress();               // 在拖 → 不是长按
        moved = true;
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
    function endDrag() {
      if (!drag) return;
      drag = false;
      clearPress();
      _el.classList.remove('dragging');
      if (moved) {
        try {
          var r = _el.getBoundingClientRect();
          localStorage.setItem(POS_KEY, JSON.stringify({ x: r.left, y: r.top }));
        } catch (_) {}
      }
    }
    _el.addEventListener('pointerup', endDrag);
    _el.addEventListener('pointercancel', endDrag);
  }

  /* ---------- 最小化 / 恢复 ---------- */
  function setMini(on) {
    _el.classList.toggle('mini', !!on);
    try {
      if (on) localStorage.setItem(MINI_KEY, '1');
      else localStorage.removeItem(MINI_KEY);
    } catch (_) {}
  }

  /* ---------- 右键菜单 ---------- */
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
      if (pos && typeof pos.x === 'number') {
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
      if (_longPress) { _longPress = false; return; }   // 长按弹完菜单，不弹气泡
      if (_el.classList.contains('mini')) {             // 圆点：点击恢复
        setMini(false);
        popBubble('回来啦~');
        return;
      }
      var pool = (BUBBLE[_current] || BUBBLE.idle).slice();
      pool.push(_current);
      popBubble(pool[Math.floor(Math.random() * pool.length)]);
    });
    makeDraggable();
    var initial = _current;
    _current = '';                // 初始状态强制加载（applyState 对相同状态去重）
    applyState(initial);
    preloadRest();
  }

  /* ---------- 监听外部状态事件 ---------- */
  window.addEventListener('pet:state', function (e) {
    var s = e.detail && e.detail.state;
    if (s && !_manual) applyState(s);
  });

  /* ---------- 启动 ---------- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
