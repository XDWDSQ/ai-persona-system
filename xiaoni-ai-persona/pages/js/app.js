        (function(){
          var $ = function(s){ return document.querySelector(s); };
          var msgsInner = document.querySelector('.msgs-inner');
          /* 播放中对齐：点击当前播放消息的分段文字 → 按字符比例 seek 到对应音频位置（APK 环境） */
          function seekBySeg(seg) {
            if (!seg || !state.playing) return;
            if (!seg.closest('.msg') || seg.closest('.msg') !== state.playingMsg) return;
            if (!window.AndroidBridge || !window.AndroidBridge.audioDurationMs) return;
            var dur = window.AndroidBridge.audioDurationMs();
            var total = state.playingText ? state.playingText.length : 0;
            if (dur <= 0 || !total) return;
            var ms = Math.floor(parseInt(seg.dataset.cs, 10) / total * dur);
            if (ms >= dur - 200) ms = Math.max(0, dur - 200); /* 末尾段别 seek 到终点直接触发 ended */
            window.AndroidBridge.seekAudio(ms);
            /* 暂停中点击：跳到目标位置并继续播放，让跳转有实际反馈 */
            if (state.playing.paused) {
              try { window.AndroidBridge.resumeAudio(); } catch (e) {}
              state.playing.paused = false;
              if (state.playingBtn) {
                setSpeak(state.playingBtn, '播放中…', true);
                setBtnWave(state.playingBtn, true);
                setMsgSpeaking(state.playingBtn, true);
              }
            }
          }
          /* 真机触屏滚动/微动后浏览器可能不合成 click，必须走 touchend 判定 tap；
             移动 <10px 且按住 <300ms 才算点击（滚动/长按不触发）；桌面保留 click */
          var _tSeg = null, _tX = 0, _tY = 0, _tT = 0, _justSeeked = 0;
          msgsInner.addEventListener('touchstart', function(e){
            if (e.touches.length !== 1) { _tSeg = null; return; }
            _tX = e.touches[0].clientX; _tY = e.touches[0].clientY; _tT = Date.now();
            _tSeg = e.target && e.target.closest ? e.target.closest('.tts-seg') : null;
          }, { passive: true });
          msgsInner.addEventListener('touchend', function(e){
            if (!_tSeg) return;
            var t = e.changedTouches && e.changedTouches[0];
            if (t && Date.now() - _tT < 300 &&
                Math.abs(t.clientX - _tX) < 10 && Math.abs(t.clientY - _tY) < 10) {
              seekBySeg(_tSeg);
              _justSeeked = Date.now();
            }
            _tSeg = null;
          }, { passive: true });
          msgsInner.addEventListener('click', function(e){
            if (Date.now() - _justSeeked < 500) return;  /* touchend 已处理，防重复 */
            var seg = e.target && e.target.closest ? e.target.closest('.tts-seg') : null;
            if (seg) seekBySeg(seg);
          });
          var state = { history: [], cloudProviders: {}, soundOn: (function(){ try { return localStorage.getItem('xiaoni_sound_on') !== '0'; } catch(e) { return true; } })(), speed: (function(){ try { return parseFloat(localStorage.getItem('xiaoni_speed') || '1.0') || 1.0; } catch(e) { return 1.0; } })(), provider: 'cloud', voiceProvider: 'local', voiceKey: '', aliyunConfigured: false, minimaxConfigured: false, audioCache: {}, ttsInflight: {}, playing: null, playingBtn: null, playSeq: 0, pendingAttachments: [], attachMenuOpen: false, awaiting: {}, sendingCount: 0, uploading: false, lastSync: null, voiceProviderTouched: false };
          /* 当前激活角色 key：同步维护，页面加载即生效（默认与后端 config 一致为大帅），
             避免头像判断依赖异步的 /api/status 返回导致首帧渲染成首字 */
          var currentRoleKey = 'dashuai';

          /* Auto-grow textarea (max 150px) */
          var ta = document.getElementById('text');
          if (ta) {
            var grow = function () {
              /* 测量时暂空占位符：Chrome 会把空 textarea 的 placeholder 换行
                 计入 scrollHeight（桌面长占位符在窄屏折两行 → 输入栏被撑成 60px）。
                 栏高只应跟随实际输入内容。 */
              var ph = ta.placeholder;
              ta.placeholder = '';
              ta.style.height = 'auto';
              ta.style.height = Math.min(ta.scrollHeight, 150) + 'px';
              ta.placeholder = ph;
            };
            ta.addEventListener('input', grow);
            /* 输入即存草稿（防抖 300ms）：刷新/掉线/切会话都不丢未发送内容 */
            ta.addEventListener('input', function(){ saveDraft(false); });
            grow();
          }

          /* ---------- 输入草稿：按会话分开存，刷新或切会话后自动恢复 ---------- */
          var _draftTimer = 0;
          function draftKey(id){ return 'xiaoni_draft_' + id; }
          function saveDraft(immediate){
            if (!ta) return;
            var persist = function(){
              try {
                var v = ta.value;
                if (v) localStorage.setItem(draftKey(currentSessionId), v);
                else localStorage.removeItem(draftKey(currentSessionId));
              } catch (e) {}
            };
            if (immediate) { clearTimeout(_draftTimer); persist(); return; }
            clearTimeout(_draftTimer);
            _draftTimer = setTimeout(persist, 300);
          }
          function restoreDraft(){
            if (!ta) return;
            var v = '';
            try { v = localStorage.getItem(draftKey(currentSessionId)) || ''; } catch (e) {}
            if (ta.value !== v) {
              ta.value = v;
              ta.dispatchEvent(new Event('input'));  /* 触发 auto-grow 恢复高度 */
            }
          }
          function clearDraft(){
            try { localStorage.removeItem(draftKey(currentSessionId)); } catch (e) {}
          }

          /* ---------- toast ---------- */
          var toastsBox = null;
          function toast(msg, ms, kind) {
            if (!toastsBox) {
              toastsBox = document.createElement('div');
              toastsBox.className = 'toasts';
              toastsBox.setAttribute('role', 'status');
              toastsBox.setAttribute('aria-live', 'polite');
              document.body.appendChild(toastsBox);
            }
            var el = document.createElement('div');
            el.className = 'toast ' + (kind === 'error' ? 'err' : (kind === 'ok' ? 'ok' : ''));
            var icon = kind === 'error'
              ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>'
              : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
            el.innerHTML = icon + '<span></span>';
            el.querySelector('span').textContent = msg;
            var t = (ms || 2600);
            var bar = document.createElement('i');
            bar.className = 'toast-bar';
            bar.style.animationDuration = t + 'ms';
            el.appendChild(bar);
            toastsBox.appendChild(el);
            /* 堆叠上限：连续报错时不要把整屏堆满 toast */
            while (toastsBox.children.length > 4) {
              toastsBox.removeChild(toastsBox.firstChild);
            }
            requestAnimationFrame(function(){ requestAnimationFrame(function(){ el.classList.add('show'); }); });
            setTimeout(function(){
              el.classList.remove('show');
              setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 260);
            }, t);
          }

          /* ---------- 确认弹窗 ---------- */
          var _cfModal = null, _cfOk = null, _cfCancel = null, _cfMsg = null, _cfTitle = null;
          function _cfReady() {
            if (_cfModal) return true;
            _cfModal = document.getElementById('confirm-modal');
            if (!_cfModal) return false;
            _cfTitle = document.getElementById('cf-title');
            _cfMsg = document.getElementById('cf-msg');
            _cfOk = document.getElementById('cf-ok');
            _cfCancel = document.getElementById('cf-cancel');
            return true;
          }
          /* 重入保护：同一时刻只允许一个确认框存在。
             同一个按钮连点/移动端双击时，旧实现会向同一个 #confirm-modal 再挂一遍
             监听并覆盖文案，两个 Promise 被同一次点击同时 resolve —— 用户看到的是
             第二个问题，回答的却是第一个。已经在开了就直接返回"取消"。 */
          var _cfBusy = false;
          /* 返回 Promise：resolve(true) 确认 / resolve(false) 取消。message 为文本时自动转义。 */
          function confirmDialog(message, opts) {
            if (!_cfReady()) return Promise.resolve(window.confirm(message || ''));
            if (_cfBusy) return Promise.resolve(false);
            _cfBusy = true;
            opts = opts || {};
            return new Promise(function(resolve){
              var cfReturnFocus = document.activeElement;
              var onOk = function(){ close(); resolve(true); };
              var onCancel = function(){ close(); resolve(false); };
              var onKey = function(e){
                if (e.key === 'Escape') { onCancel(); return; }
                trapTabIn(_cfModal, e);
              };
              function close() {
                _cfBusy = false;   /* 必须复位，否则一次确认后所有后续确认框都被吞掉 */
                _cfModal.classList.remove('show');
                _cfOk.removeEventListener('click', onOk);
                _cfCancel.removeEventListener('click', onCancel);
                _cfModal.removeEventListener('click', onOverlay);
                document.removeEventListener('keydown', onKey);
                restoreFocusTo(cfReturnFocus);
              }
              function onOverlay(e){ if (e.target === _cfModal) onCancel(); }
              if (_cfTitle) _cfTitle.textContent = opts.title || '确认';
              if (_cfMsg) {
                _cfMsg.textContent = message || '';
                _cfMsg.style.whiteSpace = 'pre-wrap';
              }
              if (_cfOk) {
                _cfOk.textContent = opts.okText || '确定';
                _cfOk.style.visibility = 'visible';
              }
              if (_cfCancel) _cfCancel.textContent = opts.cancelText || '取消';
              _cfModal.classList.add('show');
              _cfOk.addEventListener('click', onOk);
              _cfCancel.addEventListener('click', onCancel);
              _cfModal.addEventListener('click', onOverlay);
              document.addEventListener('keydown', onKey);
              setTimeout(function(){ if (_cfOk) _cfOk.focus(); }, 60);
            });
          }

          /* ---------- API ---------- */
          /* 网络错误中文映射：浏览器原生 TypeError: Failed to fetch 对用户毫无信息量，
             统一翻译成可操作的中文（本地确认 8000 端口、远程确认 ngrok 隧道+警告页）。 */
          function friendlyNetError(e) {
            var m = (e && e.message) || '';
            if (e && e.message === '__ABORT__') return '已停止生成';
            if (/Failed to fetch|NetworkError|Load failed|Network request failed|fetch failed/i.test(m)
                || (e && e.name === 'TypeError' && !m)) {
              return '网络连接失败：连不上后端。请检查：1) 电脑上服务在跑（打开 http://127.0.0.1:8000 看能否进登录页）；'
                + '2) 手机远程时 ngrok 在跑且用 data/tunnel_url.txt 里最新地址（免费版重启会换域名，旧地址报离线）；'
                + '3) 新浏览器首次打开隧道地址先点 Visit Site 过 ERR_NGROK_6024 警告页再回聊天';
            }
            if (/Unexpected token '<'|is not valid JSON|JSON\.parse/i.test(m)) {
              return '隧道返回了网页而非数据（多为 ngrok 免费警告页 ERR_NGROK_6024）：新浏览器先打开隧道地址点 Visit Site，通过后再回聊天页重试';
            }
            return m || '网络异常';
          }
          /* 统一请求超时：fetch 本身没有超时，半开连接（ngrok 隧道抖动、手机切网、
             服务重启未发 RST）会让它挂几分钟不 settle。以前只有 chat 流有 180s 兜底，
             普通 api() 调用可以永久挂住 —— 调用方各自的 finally 因此永远不执行：
             state.saving / state.uploading 永久 latch，保存与发送整体静默停摆。
             这里给所有 api() 调用装 20s 硬超时；调用方自带 signal 时用 AbortSignal.any
             合并（不支持则该浏览器/WebView 保持旧行为，不会更糟）。 */
          var API_TIMEOUT_MS = 20000;
          /* ms 省略时用 20s；TTS 合成这类本来就慢的请求传 60000。 */
          function apiTimeoutSignal(outer, ms) {
            var budget = ms || API_TIMEOUT_MS;
            var ctl = null;
            try { ctl = new AbortController(); } catch (e) { return { signal: outer || undefined, timedOut: function(){ return false; }, done: function(){} }; }
            var timedOut = false;
            var timer = setTimeout(function(){
              timedOut = true;
              try { ctl.abort(); } catch (e) {}
            }, budget);
            var signal = ctl.signal;
            if (outer) {
              if (typeof AbortSignal !== 'undefined' && AbortSignal.any) {
                try { signal = AbortSignal.any([ctl.signal, outer]); } catch (e) { signal = ctl.signal; }
              } else {
                /* 没有 AbortSignal.any：优先保证超时仍能触发，外部 signal 透传回调用方自行处理 */
                try { outer.addEventListener('abort', function(){ try { ctl.abort(); } catch (e) {} }); } catch (e) {}
              }
            }
            return { signal: signal, timedOut: function(){ return timedOut; }, done: function(){ clearTimeout(timer); } };
          }
          async function api(path, opt) {
            var r;
            var to = apiTimeoutSignal(opt && opt.signal);
            var opts = Object.assign({}, opt || {}, { signal: to.signal });
            try {
              r = await fetch(path, opts);
            } catch (e) {
              to.done();
              if (to.timedOut()) throw new Error('请求超时（服务或隧道无响应），请稍后重试');
              /* 调用方自己 abort（如切会话）：原样抛出，由调用方按 __ABORT__ 语义处理 */
              if (opt && opt.signal && opt.signal.aborted) throw e;
              throw new Error(friendlyNetError(e));
            }
            /* 超时定时器保留到读完 body 再清：只等响应头就清的话，半开连接在
               r.json()/r.text() 阶段仍可永久挂死（正是要修的那类卡顿）。 */
            try {
              if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
              if (!r.ok) {
                var m = '请求失败(' + r.status + ')';
                if (r.status === 404) {
                  /* ngrok 隧道离线时（ERR_NGROK_3200）同样是 404 但 body 是 ngrok 错误页 HTML，
                     直接报 404 会误导去查接口；先看 body 是否 ngrok 错误页。 */
                  try {
                    var t404 = await r.text();
                    if (/ERR_NGROK_3200|endpoint .* is offline/i.test(t404)) {
                      throw new Error('隧道已离线（ngrok 未运行）：电脑上执行 ngrok http 8000，并用 data/tunnel_url.txt 里最新地址重进');
                    }
                    try { m = (JSON.parse(t404)).detail || m; } catch (e2) {}
                  } catch (e2) { if (e2 && /隧道已离线/.test(e2.message)) throw e2; }
                  throw new Error(m);
                }
                try { m = (await r.json()).detail || m; } catch (e) {}
                throw new Error(m);
              }
              try {
                return r.status === 200 ? await r.json() : null;
              } catch (e) {
                if (to.timedOut()) throw new Error('请求超时（响应读取中断），请稍后重试');
                throw new Error(friendlyNetError(e));
              }
            } finally {
              to.done();
            }
          }

          /* ---------- 流式输出（SSE） ---------- */
          function parseSSEEvent(block) {
            var data = '';
            /* SSE 规范：多行 data 以 \n 连接，行内空白必须保留（trim 会破坏 JSON 值内的首尾空格）；
               当前后端单行输出，这里按规范处理避免未来埋雷 */
            block.split('\n').forEach(function(l){
              if (l.indexOf('data:') === 0) data += l.slice(5) + '\n';
            });
            data = data.replace(/\n$/, '');
            if (!data) return null;
            try { return JSON.parse(data); } catch (e) { return null; }
          }

          /* SSE 流式请求：POST /api/chat (stream:true)，逐事件回调。
             事件：{"d":增量} / {"reset":true} / {"done":true,...} / {"err":"..."}
             onDelta(fullRaw) 在每次增量/重置后回调。
             signal：用户点"停止生成"/切会话时 abort，调用方按 __ABORT__ 识别。
             返回 {clean, style, searched, vision_used}；中途出错 throw。 */
          async function chatStreamRequest(payload, onDelta, signal) {
            var resp;
            try {
              resp = await fetch('/api/chat', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({ stream: true }, payload)),
                signal: signal || undefined,
              });
            } catch (e) {
              if (signal && signal.aborted) throw new Error('__ABORT__');
              throw new Error(friendlyNetError(e));
            }
            if (!resp.ok) {
              if (resp.status === 401) { location.href = '/login'; }
              var m = '请求失败(' + resp.status + ')';
              if (resp.status === 404) {
                try {
                  var t404 = await resp.text();
                  if (/ERR_NGROK_3200|endpoint .* is offline/i.test(t404)) {
                    throw new Error('隧道已离线（ngrok 未运行）：电脑上执行 ngrok http 8000，并用 data/tunnel_url.txt 里最新地址重进');
                  }
                  try { m = (JSON.parse(t404)).detail || m; } catch (e2) {}
                } catch (e2) { if (e2 && /隧道已离线/.test(e2.message)) throw e2; }
                throw new Error(m);
              }
              try { m = (await resp.json()).detail || m; } catch (e) {}
              throw new Error(m);
            }
            var reader = resp.body.getReader();
            var dec = new TextDecoder();
            var buf = '';
            var fullRaw = '';
            var out = null;
            for (;;) {
              var r;
              try {
                r = await reader.read();
              } catch (e) {
                if (signal && signal.aborted) throw new Error('__ABORT__');
                throw new Error(friendlyNetError(e));
              }
              if (r.done) break;
              if (signal && signal.aborted) throw new Error('__ABORT__');
              buf += dec.decode(r.value, { stream: true });
              var parts = buf.split('\n\n');
              buf = parts.pop();
              for (var i = 0; i < parts.length; i++) {
                var ev = parseSSEEvent(parts[i]);
                if (!ev) continue;
                if (typeof ev.d === 'string') {
                  fullRaw += ev.d;
                  if (onDelta) onDelta(fullRaw);
                } else if (ev.reset) {
                  fullRaw = '';
                  if (onDelta) onDelta(fullRaw);
                } else if (ev.done) {
                  out = { clean: ev.clean, style: ev.style || '', narration: ev.narration || '', searched: !!ev.searched, vision_used: !!ev.vision_used, story_update: ev.story_update || null };
                } else if (ev.err) {
                  throw new Error(ev.err);
                }
              }
            }
            if (!out) out = { clean: stripStyleTag(fullRaw), style: '', narration: '', searched: false, vision_used: false, story_update: null };
            return out;
          }

          /* 流式对话统一入口：随 SSE 增量渲染气泡；结束后入库、挂操作按钮、可选朗读。
             失败时保留已流出的部分并落失败气泡（带重试按钮）。send/重新生成/重试共用。 */
          /* 在飞的生成请求控制器：点发送按钮（停止态）/ 切会话 / 新对话 / 删会话时 abort，
             不再让旧流在后台继续写旧会话、也不让发送按钮永久转圈。 */
          var chatAborter = null;
          function abortChatInFlight() {
            if (chatAborter) { try { chatAborter.abort(); } catch (e) {} }
          }
          /* 发送按钮双态：空闲 = 发送；生成中 = 停止生成（保持可点，点按即 abort）。
             上传附件的短阶段仍用 disabled（见 send），流式阶段绝不 disabled。 */
          function syncSendBtn() {
            var b = document.querySelector('.send-btn');
            if (!b) return;
            var on = (state.sendingCount || 0) > 0;
            b.classList.toggle('loading', on);
            if (!on) b.disabled = false;
            b.title = on ? '停止生成' : '发送';
            b.setAttribute('aria-label', on ? '停止生成' : '发送');
          }
          async function streamReplyInto(sessionId, snap, payload) {
            var s = findSession(sessionId);
            var div = null;
            var started = false;
            var fullRaw = '';
            var clean = '', style = '';
            var bubble = null;    /* 缓存气泡引用，避免每帧 querySelector */
            var renderer = null;  /* rAF 帧合并 + 增量渲染器 */
            var ctl = null;
            try { ctl = new AbortController(); } catch (e) { ctl = null; }
            chatAborter = ctl;
            state.sendingCount = (state.sendingCount || 0) + 1;
            syncSendBtn();
            /* 兜底超时 180s：隧道挂死 / SSE 半开连接时不再卡死，用户也可随时手动停止 */
            var timedOut = false;
            var timeoutId = 0;
            if (ctl) timeoutId = setTimeout(function(){ timedOut = true; try { ctl.abort(); } catch (e) {} }, 180000);
            try {
              var res = await chatStreamRequest(payload, function(fullText){
                fullRaw = fullText;
                /* 切走会话后只继续消费流（保证收尾入库完整），绝不操作当前视图，
                   流式气泡不能插进别的会话 */
                if (currentSessionId !== sessionId) return;
                if (!started) {
                  started = true;
                  hideTyping();
                  /* 开始演出：移除剧情引导卡，让位给真实对话 */
                  var _g = msgsInner.querySelector('.msg-guide');
                  if (_g) _g.remove();
                  div = addMsg('assistant', fullText);
                  bubble = div.querySelector('.bubble');
                  renderer = makeStreamRenderer(bubble, function(){
                    /* 流式期间贴底滚动合并进渲染帧、瞬时到位，避免 smooth 动画逐帧叠加 */
                    if (stickBottom) snapBottom();
                  });
                } else if (renderer) {
                  renderer.frame(fullText); /* 帧合并 + 增量追加，渲染频率锁到屏幕刷新率 */
                }
              }, ctl && ctl.signal);
              hideTyping();
              clean = res.clean || stripStyleTag(fullRaw);
              style = res.style || '';
              if (res.vision_used) toast('角色看到了你发的图片');
              if (res.searched) toast('我查了下最新消息');
              if (!clean || !clean.trim()) {
                if (div && div.parentNode) div.remove();
                addFailureMsg(sessionId, '模型没有返回内容', snap);
                return;
              }
              /* 流式期间 syncSessionsFromServer 的多端合并可能已把 sessions 数组里的
                 会话对象整体替换（旧对象脱离数组）——入库前必须重新取最新引用，
                 否则 push 进旧对象 = 这条回复永久丢失。 */
              var fresh = findSession(sessionId);
              if (!fresh) {
                if (div && div.parentNode) div.remove();
                addFailureMsg(sessionId, '会话已被删除', snap);
                return;
              }
              s = fresh;
              var aiMsg = { role: 'assistant', content: clean, style: style, narration: res.narration || '', ts: Date.now() };
              s.history.push(aiMsg);
              /* 先记下本条回复在 history 里的下标（剧情 event 会紧接着再 push 一条，
                 之后 len-1 就不是它了）。流式气泡此前从不写 dataset.hidx，于是删除它
                 时只能退回"按内容正序匹配第一条"—— 模型复读同一句话时，删掉的会是
                 **更早的那条**同文消息。 */
              var aiIdx = s.history.length - 1;
              /* 剧情推进：本轮触发了剧情事件（赛果记录/阶段推进）时，把系统分隔线也入库，
                 刷新/换设备后仍能看到剧情节点；role=event 不会进模型上下文 */
              var storyEvText = '';
              if (res.story_update && res.story_update.events && res.story_update.events.length) {
                storyEvText = '剧情推进 · ' + res.story_update.events.join('；');
                s.history.push({ role: 'event', content: storyEvText });
              }
              markSessionActivity(sessionId);
              if (currentSessionId === sessionId) {
                /* 旁白双声部：旁白气泡插在 AI 气泡之前（灰色小字推剧情，大帅气泡说台词） */
                if (res.narration) {
                  var narrDiv = addMsg('narration', res.narration);
                  if (div && div.parentNode) div.parentNode.insertBefore(narrDiv, div);
                }
                if (!div) {
                  div = addMsg('assistant', clean);
                } else {
                  var b2 = div.querySelector('.bubble');
                  /* 以 clean 为准：剥净残留 style/元话语标记；终稿优先走 renderer.finish
                     （见下），它写纯文本（网页端与增量帧一致，均不经过链接化）。 */
                  /* 终稿必须走 renderer.finish：它会取消挂起帧并置 finalized，
                     否则最后一个 delta 排队的 rAF 会把气泡写回未清洗原文/重复尾段
                     （服务端 clean 还做了 _strip_meta_notes/_fix_addressing/_split_narration，
                     与客户端 stripStyleTag 不等，相等兜底挡不住）。 */
                  if (renderer && renderer.finish) renderer.finish(clean);
                  else setBubbleContent(b2, clean); /* 兜底：无渲染器时保留原链接化/分段行为 */
                  applyClamp(div, clean);
                  b2.classList.remove('no-text');
                }
                /* 绑定下标：删除/操作这条气泡时按 hidx 精确定位，不再依赖内容匹配 */
                div.dataset.hidx = String(aiIdx);
                var meta = div.querySelector('.msg-meta');
                var spk = makeSpeakBtn(clean, style, aiMsg, sessionId);
                meta.appendChild(spk);
                meta.appendChild(makeResynthBtn(clean, style, aiMsg, sessionId));
                meta.appendChild(makeRegenBtn(div));
                if (storyEvText) addMsg('event', storyEvText); /* 分隔线跟在回复后面，像系统结算 */
                if (res.story_update) refreshStoryEntry(res.story_update);
                /* .catch：ttsAndPlay 的前置段（stopAudio 等）在 try 之外，未捕获会变成
                   控制台 unhandled rejection，污染错误追踪 */
                if (state.soundOn) ttsAndPlay(clean, spk, style, false, aiMsg, sessionId).catch(function(){});
              }
            } catch (e) {
              hideTyping();
              var aborted = (e && e.message === '__ABORT__') || (ctl && ctl.signal && ctl.signal.aborted);
              var abortReason = timedOut ? '响应超时' : '已停止生成';
              /* 流中断但已流出内容（如收尾断连/部署重启/隧道抖动/手动停止）：把已生成内容入库，
                 否则重进后历史以用户消息结尾，误报"上次回复没有生成成功"。
                 没有任何内容才落失败气泡（可重试）。 */
              var kept = clean || stripStyleTag(fullRaw);
              if (kept && kept.trim()) {
                var f2 = findSession(sessionId);
                if (f2) {
                  var keptMsg = { role: 'assistant', content: kept, style: style || '' };
                  f2.history.push(keptMsg);
                  markSessionActivity(sessionId);
                  if (currentSessionId === sessionId && div && div.querySelector('.bubble')) {
                    /* 中断收尾同样要取消挂起帧，否则最后一批增量的 rAF 会在写入
                       kept 之后再追加一段，屏幕上出现重复尾巴 */
                    if (renderer && renderer.finish) renderer.finish(kept);
                    else setBubbleContent(div.querySelector('.bubble'), kept);
                    applyClamp(div, kept);
                    div.classList.remove('no-text');
                    /* 与正常收尾保持同一形态：补挂朗读/重新合成/重新生成，
                       否则刷新前这条已入库消息没有任何操作入口 */
                    var keptMeta = div.querySelector('.msg-meta');
                    if (keptMeta && !keptMeta.querySelector('.speak-btn')) {
                      keptMeta.appendChild(makeSpeakBtn(kept, style || '', keptMsg, sessionId));
                      keptMeta.appendChild(makeResynthBtn(kept, style || '', keptMsg, sessionId));
                      keptMeta.appendChild(makeRegenBtn(div));
                    }
                  }
                  toast(aborted
                    ? (timedOut ? '响应超时，已保留已生成的内容' : '已停止生成，已保留已生成的内容')
                    : '网络中断，已保留已生成的内容');
                } else if (currentSessionId === sessionId && div && div.parentNode) {
                  div.remove();
                  addFailureMsg(sessionId, '会话已被删除', snap);
                }
              } else {
                if (div && div.parentNode) div.remove(); /* 流中断：删掉半成品，落失败气泡 */
                addFailureMsg(sessionId, aborted ? abortReason : ((e && e.message) || '生成失败，请重试'), snap);
              }
            } finally {
              if (timeoutId) clearTimeout(timeoutId);
              if (chatAborter === ctl) chatAborter = null;
              state.sendingCount = Math.max(0, (state.sendingCount || 1) - 1);
              syncSendBtn();
            }
          }

          /* ---------- 气泡内容：转义 + 链接化 + 长文折叠 ----------
             APK（AndroidBridge）环境保持 .tts-seg 分句结构（点字跳播依赖）；
             其余环境把纯文本转义后将 http(s) 链接变成可点击的 <a>。
             textContent 读回仍是纯文本，编辑/重试/删除的按内容寻址不受影响。 */
          var MSG_CLAMP_LEN = 600;
          function escapeHtml(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch){
              return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch];
            });
          }
          function linkifyHtml(text) {
            var esc = escapeHtml(text);
            return esc.replace(/https?:\/\/[^\s<>"'）】｝}]+/g, function(url){
              var trail = '';
              var m = url.match(/[.,;:!?'"'，。！？；：、）】》〉,.!?;:]+$/);
              if (m) { trail = m[0]; url = url.slice(0, -trail.length); }
              if (!url) return trail;
              return '<a href="' + url + '" target="_blank" rel="noreferrer noopener">' + url + '</a>';
            });
          }
          function setBubbleContent(bubble, text) {
            if (window.AndroidBridge) { fillBubbleText(bubble, text || ''); return; }
            bubble.innerHTML = linkifyHtml(text || '');
          }
          /* 超长消息折叠：气泡收起到约 8 行，附「展开全文/收起」按钮（插在 meta 前） */
          function applyClamp(div, text) {
            if (!div || !text || text.length < MSG_CLAMP_LEN) return;
            var bubble = div.querySelector('.bubble');
            var body = div.querySelector('.msg-body');
            if (!bubble || !body || div.querySelector('.clamp-toggle')) return;
            bubble.classList.add('clamped');
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'clamp-toggle';
            btn.textContent = '展开全文';
            btn.addEventListener('click', function(){
              bubble.classList.toggle('clamped');
              btn.textContent = bubble.classList.contains('clamped') ? '展开全文' : '收起';
            });
            var meta = div.querySelector('.msg-meta');
            if (meta) body.insertBefore(btn, meta);
            else body.appendChild(btn);
          }
          /* 防御性清理：LLM 有时把 [style:xxx] 风格标记放在回复中间/末尾（后端解析只保证剥离开头），
             这里在展示与朗读前统一剥离，保证历史消息也不会把标记显示出来或被 TTS 读出来。
             兼容 [style:x]/[风格:x]/【style：x】/全角冒号/大小写。 */
          function stripStyleTag(text) {
            text = String(text || '');
            var cleaned = text.replace(/[\[【](?:style|风格)\s*[:：]\s*[^\]】\r\n]*?[\]】]/gi, '');
            if (cleaned === text) return text.trim();
            cleaned = cleaned.replace(/[ \t]*\n[ \t]*/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
            return cleaned;
          }

          /* ---------- 流式渲染优化 ----------
             针对"帧数低"的三个根因：
             1) 每帧全量 textContent 重写（文本越长越卡，累计 O(n²)）→ 增量 append TextNode（O(增量)）
             2) 每帧 stripStyleTag 对全文重跑正则 → 只做前缀比对，多数情况跳过全文处理
             3) 每帧 scrollTo smooth 滚动动画叠加 → 渲染统一合并进 rAF 帧，贴底滚动瞬时到位
             返回一个接收 fullRaw 的渲染函数：SSE 回调只存缓冲，渲染频率被锁到屏幕刷新率。 */
          function makeStreamRenderer(bubble, onRendered) {
            var pending = null;   /* 待渲染的 fullRaw（帧合并缓冲） */
            var rafId = 0;
            var base = '';        /* 已渲染的 clean 文本，作为增量计算的前缀基准 */
            var finalized = false; /* 收尾已写终稿：之后排队的帧一律只同步 base，绝不碰 DOM */
            function flush() {
              rafId = 0;
              if (pending === null) return;
              var raw = pending; pending = null;
              /* 收尾竞态防护（第一道）：done 处理会绕过渲染器直接写整段 clean，
                 此时若还有排队的 rAF flush，按 base 追加增量会把末段重复一遍
                 （delta 与 done 同帧到达时必现）。finalized 之后终稿已经写好，
                 排队帧一律直接丢掉，绝不再读写 DOM。 */
              if (finalized) { return; }
              /* 快速路径：全文没有 style 标记的起始特征时不跑全文正则（长文每帧 O(n) 正则
                 是长回复流式掉帧的根源之一）；indexOf 探测是原生快速操作，标记真出现时再回落 */
              var clean;
              if (raw.length > 400
                  && raw.indexOf('[style') < 0 && raw.indexOf('[风格') < 0
                  && raw.indexOf('【style') < 0 && raw.indexOf('【风格') < 0) {
                clean = raw.trim();
              } else {
                clean = stripStyleTag(raw);
              }
              /* 收尾竞态防护（第二道，兜住"终稿内容恰好等于本帧内容"的良性情况）：
                 气泡文本已等于本帧 clean 时无需再动 DOM，只同步 base。 */
              if (clean && bubble.textContent === clean) {
                base = clean;
                if (onRendered) onRendered();
                return;
              }
              if (base) {
                if (clean.indexOf(base) === 0) {
                  /* 前缀一致（常态）：只把新增部分 append 成独立 TextNode，不重建整个气泡 */
                  var inc = clean.slice(base.length);
                  if (inc) bubble.appendChild(document.createTextNode(inc));
                } else {
                  /* 罕见情况（中间出现残留 style 标记被剥离、或前缀因 trim 变化）：整段重设兜底 */
                  bubble.textContent = clean;
                }
              } else if (clean) {
                bubble.textContent = clean;
              }
              base = clean;
              bubble.classList.remove('no-text');
              if (onRendered) onRendered();
            }
            /* 收尾固化：写终稿的唯一入口。先取消挂起帧、丢弃缓冲并置位 finalized，
               再写文本 —— 此后任何已排队的 rAF 回到 flush 都会在第一道守卫直接返回，
               不可能再按旧 base 追加重复尾段或写回未清洗原文。 */
            function finish(finalText) {
              if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
              pending = null;
              finalized = true;
              var t = (finalText === undefined || finalText === null) ? '' : String(finalText);
              base = t;
              bubble.textContent = t;
              if (t) bubble.classList.remove('no-text');
              if (onRendered) onRendered();
            }
            return {
              frame: function(fullRaw) {
                pending = fullRaw;
                if (!rafId) rafId = requestAnimationFrame(flush);
              },
              finish: finish,
            };
          }

          /* ---------- 状态 ---------- */
          function pickVoiceProvider(p) {
            /* 本地合成不再出现在设置里（后端能力保留）：存量 voice_provider=local 时兜底显示 MiniMax */
            if (p !== 'aliyun' && p !== 'minimax') p = 'minimax';
            document.querySelectorAll('#voice-tabs .set-tab').forEach(function(b){ b.classList.toggle('active', b.dataset.voice === p); });
            var map = { 'aliyun': 'aliyun-voice-fields', 'minimax': 'minimax-voice-fields' };
            Object.keys(map).forEach(function(k){
              var el = document.getElementById(map[k]);
              if (el) el.style.display = (k === p) ? '' : 'none';
            });
          }
          function fillConfig(s) {
            if (!document.getElementById('cloud_provider_sel')) return;
            /* 防御：后端字段缺失/结构异常时不抛 TypeError 中断整段回填（旧实现 s.local 为
               undefined 时整页设置面板崩溃，refreshStatus 误报"后端未连接"） */
            s = s || {};
            var cc = s.cloud || {};
            var sel = document.getElementById('cloud_provider_sel');
            sel.innerHTML = '';
            Object.keys(state.cloudProviders).forEach(function(k){
              var o = document.createElement('option');
              o.value = k; o.textContent = state.cloudProviders[k].label;
              sel.appendChild(o);
            });
            sel.value = cc.provider || 'custom';
            /* MiniMax 计费模式回填：仅 MiniMax 供应商显示该行 */
            var bmSel = document.getElementById('cloud_billing_mode');
            var bmRow = document.getElementById('cloud-billing-row');
            if (bmSel && bmRow) {
              var curBilling = cc.billing_mode
                || ((state.cloudProviders[cc.provider || 'custom'] || {}).billing_mode)
                || 'payg';
              bmSel.value = curBilling;
              bmRow.style.display = (cc.provider === 'minimax') ? '' : 'none';
            }
            document.getElementById('cloud_base_url').value = cc.base_url || '';
            document.getElementById('cloud_model').value = cc.model || '';
            /* 密钥与思考开关直接回填：切换供应商后保存不会串用上一个 key */
            document.getElementById('cloud_api_key').value = cc.api_key || '';
            var ct = document.getElementById('cloud_thinking');
            /* 仅在后端明确给出布尔值时回填：老配置缺字段时保留用户当前选择，不强制改回开启 */
            if (ct && typeof cc.thinking === 'boolean') ct.checked = cc.thinking;
            document.getElementById('persona').value = s.persona || '';
            var va = s.voice_aliyun || {};
            document.getElementById('aliyun_api_key').value = va.api_key || '';
            document.getElementById('aliyun_base_url').value = va.base_url || 'https://dashscope.aliyuncs.com/api/v1';
            document.getElementById('aliyun_model').value = va.model || 'qwen3-tts-flash';
            document.getElementById('aliyun_voice').value = va.voice || 'Cherry';
            var vm = s.voice_minimax || {};
            document.getElementById('minimax_api_key').value = vm.api_key || '';
            document.getElementById('minimax_base_url').value = vm.base_url || 'https://api.minimaxi.com/v1';
            document.getElementById('minimax_model').value = vm.model || 'speech-02-hd';
            document.getElementById('minimax_voice').value = vm.voice || 'male-qn-jingying';
            document.getElementById('minimax_speed').value = (vm.speed != null) ? vm.speed : 1.0;
            document.getElementById('minimax_vol').value = (vm.vol != null) ? vm.vol : 1.0;
            document.getElementById('minimax_pitch').value = (vm.pitch != null) ? vm.pitch : 0;
            var msrEl = document.getElementById('minimax_sample_rate');
            if (msrEl) msrEl.value = vm.sample_rate || 32000;
            pickVoiceProvider(s.voice_provider || 'minimax');
            var gt = document.getElementById('greeting_toggle');
            if (gt) {
              var gv = null;
              try { gv = localStorage.getItem('xiaoni_greeting_enabled'); } catch (e) {}
              gt.checked = (gv === null ? true : gv === '1');
            }
            /* 自动朗读开关与语速回填（持久化在 localStorage，与顶栏 chip 同源） */
            var st2 = document.getElementById('sound_toggle');
            if (st2) st2.checked = state.soundOn;
            var sp2 = document.getElementById('tts-speed');
            if (sp2) {
              /* String(1.0)='1' 匹配不上 option value '1.0'（下拉框显示为空的根源），
                 匹配失败时回落 1.0x */
              sp2.value = String(state.speed);
              if (sp2.selectedIndex < 0) sp2.value = '1.0';
            }
          }
          function renderRoles(roles, active) {
            var box = document.getElementById('role-tabs');
            if (!box) return;
            box.innerHTML = '';
            (roles || []).forEach(function(r){
              var b = document.createElement('button');
              b.className = 'set-tab' + (r.key === active ? ' active' : '');
              b.textContent = r.name;
              b.title = (r.full_name ? r.full_name + '：' : '') + (r.desc || '');
              b.addEventListener('click', function(){
                if (r.key === active) return;
                api('/api/roles/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: r.key }) })
                  .then(function(){
                    toast('已切换角色：' + r.name + (r.voice_provider === 'aliyun' ? '（阿里云音色）' : ''));
                    refreshStatus();
                  })
                  .catch(function(e){ toast('切换失败：' + e.message); });
              });
              box.appendChild(b);
            });
          }
          /* 入场动画只播一次：后续 refreshStatus 重渲染不要反复闪 */
          var rolesAnimatedOnce = false;
          function renderSideRoles(roles, active) {
            var box = document.getElementById('role-list');
            if (!box) return;
            currentRoleKey = active || currentRoleKey;
            box.innerHTML = '';
            (roles || []).forEach(function(r, idx){
              var b = document.createElement('button');
              b.className = 'role-item' + (!rolesAnimatedOnce ? ' side-in' : '') + (r.key === active ? ' active' : '');
              if (!rolesAnimatedOnce) b.style.animationDelay = Math.min(idx * 40, 280) + 'ms';
              b.title = (r.full_name ? r.full_name + '：' : '') + (r.desc || '');
              var ava = document.createElement('span');
              ava.className = 'r-ava';
              if (r.key === 'dashuai') {
                var img = document.createElement('img');
                img.src = 'avatar_dashuai_64.webp';
                img.alt = '大帅';
                img.width = 64;
                img.height = 64;
                img.decoding = 'async';
                ava.appendChild(img);
              } else {
                ava.textContent = r.name.slice(0, 1);
              }
              var nm = document.createElement('span');
              nm.className = 'r-name';
              nm.textContent = r.name;
              /* 徽标显示实际生效的引擎：全局开了 manual_provider 时角色自己的
                 voice.provider 已被覆盖（角色配"本地"但实际走 MiniMax 的失真） */
              var badge = document.createElement('span');
              badge.className = 'r-badge';
              badge.textContent = state.voiceManualProvider
                ? voiceLabel(state.voiceProvider)
                : (r.voice_provider === 'aliyun' ? '阿里云' : (r.voice_provider === 'minimax' ? 'MiniMax' : '本地'));
              b.appendChild(ava);
              b.appendChild(nm);
              b.appendChild(badge);
              b.addEventListener('click', function(){
                if (r.key === active) return;
                api('/api/roles/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: r.key }) })
                  .then(function(){
                    toast('已切换角色：' + r.name);
                    refreshStatus();
                  })
                  .catch(function(e){ toast('切换失败：' + e.message); });
              });
              box.appendChild(b);
            });
            rolesAnimatedOnce = true;
          }
          function setDotsChecking() {
            document.querySelectorAll('.p-dot, .c-dot').forEach(function(d){
              d.classList.remove('online', 'offline');
              d.classList.add('checking');
            });
          }
          function applyOnlineState(online) {
            document.querySelectorAll('.c-dot').forEach(function(d){
              d.classList.remove('checking');
              d.classList.toggle('offline', !online);
            });
            var chatDot = document.querySelector('.provider-row[data-kind="chat"] .p-dot');
            if (chatDot) {
              chatDot.classList.remove('checking','local','cloud');
              chatDot.classList.toggle('offline', !online);
              chatDot.classList.add(state.provider === 'local' ? 'local' : 'cloud');
            }
          }
          function voiceLabel(p) {
            return p === 'local' ? '本地合成' : (p === 'aliyun' ? '阿里云' : (p === 'minimax' ? 'MiniMax' : (p === 'mimo' ? '小米 MiMo' : p)));
          }
          function applyVoiceState() {
            var dot = document.querySelector('.provider-row[data-kind="voice"] .p-dot');
            if (!dot) return;
            dot.classList.remove('checking','local','cloud');
            var ok = state.voiceProvider === 'local'
              || (state.voiceProvider === 'aliyun' && state.aliyunConfigured)
              || (state.voiceProvider === 'minimax' && state.minimaxConfigured)
              || (state.voiceProvider === 'mimo' && state.mimoConfigured);
            dot.classList.toggle('offline', !ok);
            dot.classList.add(state.voiceProvider === 'local' ? 'local' : 'cloud');
          }
          /* refreshStatus 竞态保护：页面加载/打开设置/保存后等多处都会触发刷新，
             手机弱网或隧道延迟下旧请求可能晚于新请求返回，若不丢弃过期响应，
             fillConfig 会把刚保存的思考模式等设置回填成旧值（关了又自动开启） */
          var _statusSeq = 0;
          var _statusRetry = 0;  /* refreshStatus 失败重试计数（成功时重置） */
          async function refreshStatus() {
            var seq = ++_statusSeq;
            setDotsChecking();
            try {
              var s = await api('/api/status');
              if (seq !== _statusSeq) return;  /* 过期响应：丢弃，不覆盖新状态 */
              _statusRetry = 0;  /* 成功重置重试计数 */
              state.provider = s.provider;
              state.voiceProvider = s.voice_provider || 'local';
              state.aliyunConfigured = !!s.aliyun_configured;
              state.minimaxConfigured = !!s.minimax_configured;
              state.mimoConfigured = !!s.mimo_configured;
              state.cloudProviders = s.cloud_providers || {};
              state.voiceManualProvider = !!s.voice_manual_provider;
              /* TTS 缓存维度：同句不同引擎/音色/模型不得复用同一份 blob，
                 否则切换引擎后点朗读会播出旧引擎的旧声音 */
              try {
                var _vk = state.voiceProvider || '';
                if (_vk === 'aliyun' && s.voice_aliyun) _vk += ' ' + (s.voice_aliyun.model || '') + ' ' + (s.voice_aliyun.voice || '');
                else if ((_vk === 'minimax' || _vk === 'mimo') && s.voice_minimax) _vk += ' ' + (s.voice_minimax.model || '') + ' ' + (s.voice_minimax.voice || '');
                state.voiceKey = _vk;
              } catch (_e) { state.voiceKey = state.voiceProvider || ''; }
              var cc2 = s.cloud || {};  /* 防御：后端字段缺失时不抛 TypeError */
              var cp = state.cloudProviders[cc2.provider];
              var pName = $('.p-name'), pDetail = $('.p-detail'), cSub = $('.c-sub');
              if (pName) pName.textContent = s.provider === 'local' ? '本地语言生成' : '云端语言生成 · ' + (cp ? cp.label : cc2.model);
              if (pDetail) pDetail.textContent = s.provider === 'local' ? (s.local ? s.local.model : '') : (cc2.model || '');
              if (cSub) cSub.textContent = s.provider === 'local' ? '本地对话' : '云端对话';
              var row = document.querySelector('.provider-row[data-kind="chat"]');
              if (row) row.title = '当前：' + (s.provider === 'local' ? '本地语言生成' : '云端语言生成') + '（在设置中切换引擎）';
              var vRow = document.querySelector('.provider-row[data-kind="voice"]');
              var vName = vRow ? vRow.querySelector('.p-name') : null;
              var vDetail = vRow ? vRow.querySelector('.p-detail') : null;
              var vLabel = voiceLabel(state.voiceProvider);
              if (vName) vName.textContent = vLabel;
              /* 实际可达性：本地引擎常可用；云端引擎看对应 key 是否已配置
                 （此前的 `ok` 引用了 applyVoiceState 的局部变量 → ReferenceError，
                 整个 refreshStatus 成功路径被 catch 吞成"后端未连接"） */
              var vOk = state.voiceProvider === 'local'
                || (state.voiceProvider === 'aliyun' && state.aliyunConfigured)
                || (state.voiceProvider === 'minimax' && state.minimaxConfigured)
                || (state.voiceProvider === 'mimo' && state.mimoConfigured);
              if (vDetail) vDetail.textContent = vOk ? '语音合成' : '未配置';
              if (vRow) vRow.title = '当前：' + vLabel + '（在设置中切换引擎）';
              applyOnlineState(!!s.active_online);
              applyVoiceState();
              /* 语音合成 chip 由 syncVoiceChipUI 统一管理（开关文案），这里不覆盖 */
              fillConfig(s);
              state.activeRole = s.active_role;
              state.roles = s.roles || [];
              renderRoles(s.roles, s.active_role);
              renderSideRoles(s.roles, s.active_role);
              updateStoryEntry(s.active_role);
              syncSessionForRole();
              var role = (s.roles || []).find(function(r){ return r.key === s.active_role; });
              var wl = document.querySelector('.welcome .eyebrow');
              if (wl && role) {
                wl.textContent = '';
                var pip = document.createElement('span');
                pip.className = 'pip';
                wl.appendChild(pip);
                wl.appendChild(document.createTextNode(role.name + ' · ' + (s.active_online ? '在线' : '离线')));
                /* 欢迎区按当前角色动态填充：不再硬编码任何角色的名字与话术 */
                var wt = document.getElementById('welcome-title');
                if (wt) {
                  wt.textContent = '';
                  var grad = document.createElement('span');
                  grad.className = 'grad';
                  grad.textContent = role.name;
                  wt.appendChild(document.createTextNode('嗨，我是'));
                  wt.appendChild(grad);
                }
                var ws = document.getElementById('welcome-sub');
                if (ws) {
                  ws.textContent = (ROLE_WELCOME[role.key] || ROLE_WELCOME.default);
                }
                renderPromptChips(role);
                /* 欢迎区肖像：仅「大帅」展示照片，其他角色隐藏（避免形象错配） */
                var wa = document.getElementById('welcome-avatar');
                if (wa) wa.style.display = (role.key === 'dashuai') ? 'block' : 'none';
              }
            } catch (e) {
              if (seq !== _statusSeq) return;  /* 过期错误同样丢弃 */
              /* 首次连接时登录可能尚未就绪（APK 预热登录/代理自动重登进行中）：
                 延迟重试，避免误报"后端未连接"；401 已跳登录页，不再重试 */
              if (e && e.message !== '未登录' && _statusRetry < 6) {
                _statusRetry++;
                setTimeout(function(){ refreshStatus(); }, 2000);
                return;
              }
              var pn = $('.p-name');
              if (pn) pn.textContent = '后端未连接';
              var pd = $('.p-detail');
              /* 失败原因直接展示中文可操作提示（此前只显示 —，用户只能看到 Failed to fetch） */
              if (pd) pd.textContent = friendlyNetError(e);
              applyOnlineState(false);
            }
          }
          /* ---------- 欢迎区文案与话题（按角色） ---------- */
          /* 副标题不直接展示 role.desc：desc 含内部设定原文（称呼/口吻约束），上屏观感差 */
          var ROLE_WELCOME = {
            dashuai: '你的专属陪伴。我记着我们聊过的事，也会一直在这儿。',
            default: '你的专属陪伴。我记着我们聊过的事，也会一直在这儿。'
          };
          var ROLE_PROMPTS = {
            dashuai: ['今天训练累不累', '最近有比赛吗', '陪我聊会儿天'],
            default: ['今天过得怎么样', '陪我说说话', '聊点开心的']
          };
          function renderPromptChips(role) {
            var box = document.getElementById('prompt-chips');
            if (!box) return;
            var list = (role && ROLE_PROMPTS[role.key]) || ROLE_PROMPTS.default;
            box.innerHTML = '';
            list.forEach(function(t){
              var b = document.createElement('button');
              b.className = 'prompt-chip';
              b.type = 'button';
              b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span></span>';
              b.querySelector('span').textContent = t;
              b.addEventListener('click', function(){
                var input = document.getElementById('text');
                if (!input) return;
                input.value = t;
                input.focus();
                input.dispatchEvent(new Event('input'));
              });
              box.appendChild(b);
            });
          }

          /* ---------- 欢迎区动态背景视频：跟随可见性与「减少动态」启停 ----------
             有历史消息（欢迎区隐藏）或用户开启减少动态时暂停，
             避免隐藏后仍占用解码与带宽。 */
          var _videoKicked = false;
          /* 省流模式（浏览器"省流量"开关或 2G 弱网）：欢迎页只出静态海报，不拉视频 */
          function dataSaverMode() {
            try {
              var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
              if (c && (c.saveData || /(^|\b)(slow-2g|2g)(\b|$)/.test(String(c.effectiveType || '')))) return true;
            } catch (e) {}
            return false;
          }
          function startWelcomeVideo(v) {
            var p = v.play();
            if (p && p.catch) p.catch(function(){ /* 自动播放策略拦截时静默 */ });
          }
          function syncWelcomeVideo(visible) {
            var v = document.getElementById('welcome-bg-video');
            if (!v) return;
            if (visible && !reduceMotion && !dataSaverMode()) {
              /* preload=none：海报先出，页面 load 后再启动视频——不与 JS/CSS/API 抢首屏带宽 */
              if (_videoKicked) { startWelcomeVideo(v); return; }
              _videoKicked = true;
              var kick = function(){
                setTimeout(function(){
                  var w = document.querySelector('.welcome');
                  if (w && w.style.display === 'none') return; /* 等待期间已来消息/切会话：不播 */
                  startWelcomeVideo(v);
                }, 250);
              };
              if (document.readyState === 'complete') kick();
              else window.addEventListener('load', kick, { once: true });
            } else {
              try { v.pause(); } catch (_) {}
            }
          }

          /* ---------- 消息渲染 ---------- */
          function aiAvatarHtml() {
            if (currentRoleKey === 'dashuai') return '<img src="avatar_dashuai_64.webp" alt="大帅" width="64" height="64" decoding="async" loading="lazy">';
            var r = (state.roles || []).find(function(x){ return x.key === currentRoleKey; });
            return String((r && r.name ? r.name : 'AI').slice(0, 1))
              .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
          }
          /* 减少动态：跟随系统偏好，减弱平滑滚动/搜索定位动画（无障碍） */
          var reduceMotion = false;
          try { reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (e) {}
          /* 贴底跟随：用户手动上翻时暂停跟随；贴底状态下内容高度变化自动补滚 */
          var stickBottom = true;
          function scrollBottom(instant){
            var m = document.getElementById('msgs');
            if (!m) return;
            stickBottom = true;
            /* instant 场景（切会话/补滚）必须瞬时到位，不能走平滑动画，否则迟滞 */
            if (instant || reduceMotion) { snapBottom(); return; }
            if (!m.scrollTo) { m.scrollTop = m.scrollHeight; }
            else { m.scrollTo({ top: m.scrollHeight, behavior: 'smooth' }); }
          }
          /* 'instant' 是较晚加入规范的枚举值（部分旧 WebKit/Safari 抛 TypeError），
             不支持的浏览器回退直接赋值 scrollTop（等价瞬时滚动） */
          function snapBottom(){
            var m = document.getElementById('msgs');
            if (!m) return;
            var ok = false;
            try {
              if (m.scrollTo) { m.scrollTo({ top: m.scrollHeight, behavior: 'instant' }); ok = true; }
            } catch (e) { ok = false; }
            if (!ok) m.scrollTop = m.scrollHeight;
          }
          /* 未读回底角标钩子：实现在下方回底按钮 IIFE 内，addMsg 在上翻时调用 */
          var jumpUnreadBump = null;
          (function(){
            var m = document.getElementById('msgs');
            if (!m) return;
            /* 回到底部浮钮：上翻浏览历史时出现，点击恢复贴底跟随 */
            var jump = document.createElement('button');
            jump.type = 'button';
            jump.className = 'jump-bottom';
            jump.title = '回到底部';
            jump.setAttribute('aria-label', '回到底部');
            jump.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7"/></svg>';
            /* 未读角标：上翻浏览时 AI 回复到达 → 红点计数；回到底部即清零 */
            var unread = 0;
            var badge = document.createElement('b');
            badge.className = 'jump-badge';
            badge.hidden = true;
            jump.appendChild(badge);
            function paintUnread(){
              badge.textContent = unread > 99 ? '99+' : String(unread);
              badge.hidden = unread <= 0;
            }
            function clearUnread(){ if (unread) { unread = 0; paintUnread(); } }
            jumpUnreadBump = function(){
              if (!stickBottom) { unread++; paintUnread(); }
            };
            jump.addEventListener('click', function(){ scrollBottom(); clearUnread(); });
            (m.closest('.main') || document.body).appendChild(jump);
            m.addEventListener('scroll', function(){
              stickBottom = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
              if (stickBottom) clearUnread();
              jump.classList.toggle('show', !stickBottom);
            }, { passive: true });
            /* 内容高度变化（图片异步加载完成、meta 按钮后加、气泡折行等）且贴底时，即时补滚到底 */
            if (typeof ResizeObserver !== 'undefined' && msgsInner) {
              new ResizeObserver(function(){
                if (!stickBottom) return;
                if (m.scrollHeight - m.scrollTop - m.clientHeight > 1) snapBottom();
              }).observe(msgsInner);
            }
          })();
          function formatFileSize(bytes) {
            if (!bytes) return '';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1024 / 1024).toFixed(1) + ' MB';
          }
          function missingAttachmentCard(name) {
            /* 图片源文件失效（/uploads 下文件被清理等）时的占位卡片，避免裂图图标 */
            var ph = document.createElement('div');
            ph.className = 'att-missing';
            ph.title = '原始图片文件已不在服务器上，无法显示';
            ph.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/><path d="m2 2 20 20"/></svg>';
            var info = document.createElement('span');
            info.className = 'att-info';
            var nm = document.createElement('strong'); nm.textContent = name; info.appendChild(nm);
            var sz = document.createElement('small'); sz.textContent = '图片文件已失效'; info.appendChild(sz);
            ph.appendChild(info);
            return ph;
          }
          function renderAttachments(div, attachments) {
            if (!attachments || !attachments.length) return;
            var bubble = div.querySelector('.bubble');
            if (!bubble) return;
            var wrap = document.createElement('div');
            wrap.className = 'attachments';
            attachments.forEach(function(a){
              var name = a.name || '附件';
              if (a.kind === 'image') {
                if (!a.url) { wrap.appendChild(missingAttachmentCard(name)); return; }
                var link = document.createElement('a');
                link.className = 'att-img';
                link.href = a.url; link.target = '_blank'; link.rel = 'noreferrer'; link.title = name;
                var img = document.createElement('img');
                img.src = a.url; img.alt = name; img.loading = 'lazy';
                img.addEventListener('load', function(){
                  /* 图片异步加载完成后高度才增加，贴底状态下补滚到底 */
                  if (stickBottom) snapBottom();
                });
                img.addEventListener('error', function(){
                  /* 加载失败（文件被清理/404）：替换为占位卡片 */
                  link.replaceWith(missingAttachmentCard(name));
                  if (stickBottom) snapBottom();
                });
                link.appendChild(img);
                var cap = document.createElement('span');
                cap.className = 'att-name'; cap.textContent = name;
                link.appendChild(cap);
                wrap.appendChild(link);
              } else {
                var card = document.createElement('a');
                card.className = 'att-file';
                card.href = a.url || '#'; card.target = '_blank'; card.rel = 'noreferrer';
                card.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M12 18v-6"/><path d="m9 15 3 3 3-3"/></svg>';
                var info = document.createElement('span');
                info.className = 'att-info';
                var nm = document.createElement('strong'); nm.textContent = name; info.appendChild(nm);
                var sz = document.createElement('small'); sz.textContent = formatFileSize(a.size); info.appendChild(sz);
                card.appendChild(info);
                wrap.appendChild(card);
              }
            });
            bubble.parentNode.insertBefore(wrap, bubble);
          }
          /* assistant 气泡填字：APK（AndroidBridge）环境按句切 .tts-seg span，
             供「点击文字跳转音频进度」；其余环境直接 textContent。
             流式收尾也必须走这里——直接 textContent 整段覆盖会把分段结构销毁，
             导致点字跳播失效。 */
          function fillBubbleText(bubble, text) {
            if (!window.AndroidBridge || !text) { bubble.textContent = text || ''; return; }
            var parts = [], cur = '', _i;
            for (_i = 0; _i < text.length; _i++) {
              cur += text[_i];
              if (/[。！？…；\n]/.test(text[_i]) || cur.length >= 24) { parts.push(cur); cur = ''; }
            }
            if (cur) parts.push(cur);
            var cs = 0, frag = document.createDocumentFragment();
            parts.forEach(function(p){
              var sp = document.createElement('span');
              sp.className = 'tts-seg';
              sp.dataset.cs = cs;  /* 该块在全文中的字符起始偏移 */
              sp.textContent = p;
              frag.appendChild(sp);
              cs += p.length;
            });
            bubble.textContent = '';
            bubble.appendChild(frag);
          }
          function addMsg(role, text, opts) {
            opts = opts || {};
            if (role === 'assistant') text = stripStyleTag(text);  // 展示前剥离残留标记
            /* 只要出现任何新消息就收起欢迎区（覆盖问候/首条消息等所有入口） */
            var _welcome = document.querySelector('.welcome');
            if (_welcome) _welcome.style.display = 'none';
            syncWelcomeVideo(false);
            /* 剧情推进分隔线：无头像无气泡无操作菜单，居中一条细字 */
            if (role === 'event') {
              var evDiv = document.createElement('div');
              evDiv.className = 'msg event' + (opts.animate === false ? '' : ' anim');
              evDiv.dataset.role = 'event';
              var evLine = document.createElement('div');
              evLine.className = 'event-line';
              var evTxt = document.createElement('span');
              evTxt.className = 'event-txt';
              evTxt.textContent = text || '';
              evLine.appendChild(evTxt);
              evDiv.appendChild(evLine);
              (opts.container || msgsInner).appendChild(evDiv);
              if (!opts.noScroll && stickBottom) scrollBottom();
              return evDiv;
            }
            var div = document.createElement('div');
            /* .anim 门控入场动画：新消息默认有；长历史整页渲染传 animate:false 跳过，
               避免几百个元素同时播动画导致切会话掉帧 */
            div.className = 'msg ' + (role === 'assistant' ? 'ai' : role) + (opts.animate === false ? '' : ' anim');
            div.dataset.role = role;
            div.innerHTML = '<div class="avatar">' + (role === 'user' ? '我' : (role === 'narration' ? '' : aiAvatarHtml())) + '</div><div class="msg-body"><div class="bubble"></div><div class="msg-meta"></div></div>';
            /* 头像图片失效（文件被删/404）时回退为角色首字，不露裂图图标 */
            var _avaImg = div.querySelector('.avatar img');
            if (_avaImg) {
              _avaImg.addEventListener('error', function(){
                var r = (state.roles || []).find(function(x){ return x.key === currentRoleKey; });
                _avaImg.parentNode.textContent = String((r && r.name ? r.name : 'AI').slice(0, 1));
              });
            }
            var bubble = div.querySelector('.bubble');
            if (text) {
              /* APK 播放中对齐：assistant 消息文字按句切块，点击跳转对应音频位置；
                 其余环境统一转义+链接化；超长消息折叠 */
              setBubbleContent(bubble, text);
              applyClamp(div, text);
            } else {
              bubble.classList.add('no-text');
            }
            renderAttachments(div, opts.attachments);
            /* 历史渲染的错峰入场：每条延迟一点，封顶 400ms，避免长历史等太久 */
            if (opts.delay) {
              var av = div.querySelector('.avatar'), bd = div.querySelector('.msg-body');
              if (av) av.style.animationDelay = opts.delay + 'ms';
              if (bd) bd.style.animationDelay = (opts.delay + 70) + 'ms';
            }
            /* 右键/长按消息 -> 自定义菜单（自己的文本消息可修改，其余删除） */
            var menuItems = function(){
              var items = [];
              if (role === 'user' && !(opts.attachments && opts.attachments.length)) {
                items.push({ label: '修改消息', icon: 'edit', onClick: function(){ editMessage(div); } });
              }
              items.push({ label: '复制', icon: 'copy', onClick: function(){ copyToClipboard(text, '已复制'); } });
              items.push({ label: '删除消息', onClick: function(){
                div.remove();
                removeFromHistory(div);
              } });
              return items;
            };
            div.addEventListener('contextmenu', function(e){
              if (e.target.closest && e.target.closest('.edit-wrap')) return; /* 编辑中：保留 textarea 默认右键菜单 */
              e.preventDefault();
              showCtxMenu(e.clientX, e.clientY, menuItems());
            });
            /* 触屏长按 480ms 弹菜单（iOS Safari 不触发 contextmenu，与会话列表同款）；
               移动超 12px 视为滚动取消；编辑中的 textarea 长按不弹菜单（让位文本选择） */
            (function(){
              var lpTimer = null, lpFired = false, lpX = 0, lpY = 0;
              div.addEventListener('touchstart', function(e){
                if (e.touches.length !== 1) return;
                if (e.target.closest && e.target.closest('.edit-wrap')) return;
                lpFired = false;
                lpX = e.touches[0].clientX; lpY = e.touches[0].clientY;
                lpTimer = setTimeout(function(){
                  lpFired = true;
                  try { if (navigator.vibrate) navigator.vibrate(12); } catch (err) {}
                  showCtxMenu(lpX, lpY, menuItems());
                }, 480);
              }, { passive: true });
              div.addEventListener('touchmove', function(e){
                if (!lpTimer) return;
                var t = e.touches[0];
                if (Math.abs(t.clientX - lpX) > 12 || Math.abs(t.clientY - lpY) > 12) {
                  clearTimeout(lpTimer); lpTimer = null;
                }
              }, { passive: true });
              div.addEventListener('touchend', function(e){
                clearTimeout(lpTimer); lpTimer = null;
                if (lpFired) { e.preventDefault(); lpFired = false; }
              });
              div.addEventListener('touchcancel', function(){ clearTimeout(lpTimer); lpTimer = null; });
            })();
            (opts.container || msgsInner).appendChild(div);
            /* 自己发的消息强制贴底；对方消息仅在已贴底时跟随（不打断上翻浏览） */
            if (!opts.noScroll) {
              if (role === 'user') stickBottom = true;
              if (stickBottom) scrollBottom();
            }
            /* 上翻浏览时 AI 回复悄悄到达：未读计数亮在回底按钮上，不打断浏览；
               animate:false 是历史整页渲染，不算新消息 */
            if (role === 'assistant' && !opts.noScroll && opts.animate !== false && jumpUnreadBump) jumpUnreadBump();
            return div;
          }
          function showTyping() {
            hideTyping(); /* 防御：发送与重新生成并发时不要叠出两个 typing */
            var div = document.createElement('div');
            div.className = 'msg ai'; div.id = 'typing';
            div.innerHTML = '<div class="avatar">' + aiAvatarHtml() + '</div><div class="msg-body"><div class="bubble typing"><span class="typing-txt">思考中…</span><i></i><i></i><i></i></div></div>';
            msgsInner.appendChild(div);
            if (stickBottom) scrollBottom();
          }
          function hideTyping(){ var t = document.getElementById('typing'); if (t) t.remove(); }

          /* ---------- 通用右键菜单 ---------- */
          var ctxMenu = null, ctxMenuItems = [], ctxMenuOpen = false;
          function ensureCtxMenu() {
            if (ctxMenu) return;
            ctxMenu = document.createElement('div');
            ctxMenu.id = 'ctx-menu';
            ctxMenu.className = 'ctx-menu';
            ctxMenu.addEventListener('click', function(e){
              var btn = e.target.closest('[data-idx]');
              if (btn) {
                var item = ctxMenuItems[parseInt(btn.dataset.idx, 10)];
                if (item && item.onClick) item.onClick();
              }
              hideCtxMenu();
            });
            document.body.appendChild(ctxMenu);
          }
          /* 菜单项图标库：item.icon 取值 edit / trash / pin / copy / download */
          var CTX_ICONS = {
            edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
            trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
            pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/></svg>',
            copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
            download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>'
          };
          function showCtxMenu(x, y, items) {
            ensureCtxMenu();
            ctxMenuItems = items;
            /* 取消上一次隐藏挂起的定时器，避免旧 hideCtxMenu 把刚弹出的菜单立刻隐藏 */
            clearTimeout(hideCtxMenu._h);
            hideCtxMenu._h = null;
            ctxMenu.classList.remove('show');
            ctxMenu.innerHTML = '';
            items.forEach(function(it, i){
              if (it.divider) { ctxMenu.appendChild(document.createElement('hr')); return; }
              var b = document.createElement('button');
              b.dataset.idx = String(i);
              var icon = it.icon ? (CTX_ICONS[it.icon] || CTX_ICONS.edit)
                : (it.danger ? CTX_ICONS.trash : CTX_ICONS.edit);
              b.innerHTML = icon + '<span></span>';
              b.querySelector('span').textContent = it.label;
              if (it.danger) b.style.color = 'var(--destructive)';
              ctxMenu.appendChild(b);
            });
            ctxMenu.style.display = 'block';
            var mw = ctxMenu.offsetWidth || 160;
            var mh = ctxMenu.offsetHeight || 40;
            ctxMenu.style.left = Math.max(8, Math.min(x, window.innerWidth - mw - 8)) + 'px';
            ctxMenu.style.top = Math.max(8, Math.min(y, window.innerHeight - mh - 8)) + 'px';
            ctxMenuOpen = true;
            ctxMenuOpenedAt = Date.now();  /* 滚动关闭宽限计时起点 */
            requestAnimationFrame(function(){ requestAnimationFrame(function(){ ctxMenu.classList.add('show'); }); });
          }
          function hideCtxMenu() {
            if (!ctxMenu || !ctxMenuOpen) return; /* 未显示时零开销返回：scroll/click 监听触发极频繁 */
            ctxMenuOpen = false;
            ctxMenu.classList.remove('show');
            clearTimeout(hideCtxMenu._h);
            hideCtxMenu._h = setTimeout(function(){ if (ctxMenu) ctxMenu.style.display = 'none'; }, 200);
          }
          function removeFromHistory(div) {
            var role = div.dataset.role;
            var idx = parseInt(div.dataset.hidx, 10);
            var hist = state.history || [];
            var hit = false;
            if (!isNaN(idx) && hist[idx] && hist[idx].role === role) {
              hist.splice(idx, 1);
              hit = true;
            } else {
              var bubbleEl = div.querySelector('.bubble');
              var content = bubbleEl ? bubbleEl.textContent : null;
              if (content != null && role === 'narration') {
                /* 旁白气泡不占 hidx、也不是独立条目：narration 只是 assistant 条目的字段。
                   按内容清字段，清完正文也空了才整条丢弃，否则下次渲染旁白又回来了 */
                for (var n = 0; n < hist.length; n++) {
                  var am = hist[n];
                  if (am && am.role === 'assistant' && am.narration === content) {
                    am.narration = '';
                    if (!(am.content || '').trim()) hist.splice(n, 1);
                    hit = true;
                    break;
                  }
                }
              } else if (content != null) {
                for (var i = 0; i < hist.length; i++) {
                  if (hist[i].role === role && hist[i].content === content) {
                    hist.splice(i, 1);
                    hit = true;
                    break;
                  }
                }
              }
            }
            if (hit) saveSessions();
            /* 必须重渲染：其余消息 DOM 的 dataset.hidx 已失效，不重渲染会导致
               下次按 hidx 删除时下标错位误删另一条同角色消息；一条都没命中时也要重绘
               （气泡已被调用方摘掉，不重绘界面会与历史对不上）。
               preserveScroll + keepAudio：删旧消息不拽视口、不断正在听的音频 */
            renderCurrentSession({ preserveScroll: true, keepAudio: true });
          }
          /* 点击其他区域 / 右键非消息处 / 滚动 / 窗口缩放 -> 关闭菜单。
             滚动关闭带 500ms 宽限：右键/长按弹出菜单的瞬间，浏览器常因焦点/锚定
             触发一次 #msgs 微滚动（约 3ms 后），无宽限会把刚弹出的菜单立刻吞掉。 */
          var ctxMenuOpenedAt = 0;
          document.addEventListener('click', hideCtxMenu);
          document.addEventListener('contextmenu', function(e){
            if (!(e.target.closest && e.target.closest('.msg, .session'))) hideCtxMenu();
          });
          document.addEventListener('scroll', function(){
            if (Date.now() - ctxMenuOpenedAt < 500) return;
            hideCtxMenu();
          }, true);
          window.addEventListener('resize', hideCtxMenu);

          /* ---------- 修改消息 ----------
             右键/长按自己发的文本消息 → 就地编辑：更新文本 → 截断其后所有消息（上下文已变，
             旧回复作废，与主流产品一致）→ 自动重新生成回复。 */
          function editMessage(div) {
            var s = currentSession();
            if (!s) return;
            if (div.querySelector('.edit-wrap')) return; /* 已在编辑中，防重复插入 */
            /* 与 send/重新生成同一把全局门禁：只按会话判定的话，别的会话在飞时仍能从这里
               再起一条流，而 chatAborter 只有一个槽位会被覆盖 —— 旧流再也停不掉
               （白烧 180s 超时，且该会话的 awaiting 一直挂着） */
            if ((state.sendingCount || 0) > 0 || state.uploading) { toast('正在生成中，请稍候再修改'); return; }
            if (state.awaiting[s.id]) { toast('回复生成中，请稍候再修改'); return; }
            var idx = parseInt(div.dataset.hidx, 10);
            var m = s.history[idx];
            if (!m || m.role !== 'user') {
              /* 下标失效兜底：按内容倒查（与重试逻辑同思路） */
              var content = div.querySelector('.bubble').textContent;
              idx = -1;
              for (var i = s.history.length - 1; i >= 0; i--) {
                if (s.history[i].role === 'user' && s.history[i].content === content) { idx = i; m = s.history[i]; break; }
              }
            }
            if (idx < 0 || !m || m.role !== 'user') { toast('找不到对应的消息，可能已被删除'); return; }
            var bubble = div.querySelector('.bubble');
            if (!bubble) return;
            /* 气泡替换为 textarea 就地编辑 */
            var ta = document.createElement('textarea');
            ta.className = 'edit-ta';
            ta.value = m.content || '';
            ta.rows = Math.max(2, Math.min(6, Math.ceil((m.content || '').length / 30)));
            ta.setAttribute('aria-label', '编辑消息');
            var wrap = document.createElement('div');
            wrap.className = 'edit-wrap';
            var bar = document.createElement('div');
            bar.className = 'edit-bar';
            var ok = document.createElement('button');
            ok.type = 'button'; ok.className = 'edit-ok'; ok.textContent = '发送';
            ok.title = '发送修改后的消息（Enter）';
            var cancel = document.createElement('button');
            cancel.type = 'button'; cancel.className = 'edit-cancel'; cancel.textContent = '取消';
            bar.appendChild(ok); bar.appendChild(cancel);
            wrap.appendChild(ta); wrap.appendChild(bar);
            bubble.style.display = 'none';
            bubble.parentNode.insertBefore(wrap, bubble);
            ta.focus();
            try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (err) {}
            var done = false;
            function finish(submit) {
              if (done) return;
              var newText = submit ? ta.value.trim() : '';
              /* 编辑框开着期间别的入口可能已起流：提交前复跑同一把全局门禁。
                 这里不置 done、不摘编辑框，用户改的文本不会被吞，空闲后再点发送 */
              if ((submit && ((state.sendingCount || 0) > 0 || state.uploading))) { toast('正在生成中，请稍候再修改'); return; }
              done = true;
              wrap.remove();
              bubble.style.display = '';
              if (!submit) return; /* 取消：还原气泡 */
              if (!newText) { toast('消息不能为空'); return; }
              if (newText === (m.content || '').trim()) return; /* 内容没变，什么都不做 */
              /* 编辑期间可能发生多端合并替换会话对象，重新取最新引用再改 */
              var cur = findSession(s.id);
              if (cur) { s = cur; m = s.history[idx]; }
              if (!m || m.role !== 'user') { toast('消息已失效，无法发送'); return; }
              m.content = newText;
              s.history.splice(idx + 1);   /* 截断其后所有消息：上下文已变，旧回复作废 */
              state.history = s.history;
              markSessionActivity(s.id);
              markAwaiting(s.id, true);    /* 先标记：renderCurrentSession 不会补失败气泡 */
              renderCurrentSession();      /* 重渲染：新文本生效、后续消息消失 */
              showTyping();
              var hist = s.history.slice(0, idx).slice(-20).map(function(x){
                if (x.content) return x;
                var mm = Object.assign({}, x);
                mm.content = (x.attachments && x.attachments.length) ? '（发送了附件）' : '';
                return mm;
              });
              var atts = m.attachments || [];
              streamReplyInto(s.id, { idx: idx, text: newText, atts: atts },
                { message: newText, history: hist, attachments: atts })
                .then(function(){ markAwaiting(s.id, false); });
              toast('消息已修改并重新发送，正在生成回复');
            }
            ok.addEventListener('click', function(){ finish(true); });
            cancel.addEventListener('click', function(){ finish(false); });
            ta.addEventListener('keydown', function(e){
              if (e.isComposing || e.keyCode === 229) return; /* IME 合成期 Enter=确认候选词 */
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); finish(true); }
              else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
            });
          }

          /* ---------- 朗读（TTS） ---------- */
          function setSpeak(btn, txt, playing) {
            var l = btn.querySelector('.speak-label');
            if (l) l.textContent = txt;
            btn.classList.toggle('playing', !!playing);
          }
          /* 朗读按钮里的迷你声波条（复用 .mini-wave/barDance，只在播放中显示） */
          function setBtnWave(btn, on) {
            if (!btn) return;
            var w = btn.querySelector('.mini-wave');
            if (on) {
              if (!w) {
                w = document.createElement('span');
                w.className = 'mini-wave';
                w.innerHTML = '<i></i><i></i><i></i><i></i><i></i>';
                btn.appendChild(w);
              }
            } else if (w) { w.remove(); }
          }
          function setMsgSpeaking(btn, on) {
            var m = btn && btn.closest ? btn.closest('.msg') : null;
            if (m) m.classList.toggle('speaking', !!on);
            /* 桌宠联动：TTS 播放中 → speaking；结束 → idle */
            try {
              window.dispatchEvent(new CustomEvent('pet:state', {
                detail: { state: on ? 'speaking' : 'idle' }
              }));
            } catch (e) {}
          }
          /* 合成中的伪进度：请求完成前缓步爬到 90%，完成后直接到 100%，失败标红 */
          function startSynthProgress(btn) {
            var msg = btn && btn.closest ? btn.closest('.msg') : null;
            if (msg) {
              var old = msg.querySelector('.synth-progress');
              if (old) old.remove();
            }
            var wrap = null;
            if (msg) {
              wrap = document.createElement('div');
              wrap.className = 'synth-progress';
              wrap.innerHTML = '<div class="track"><i></i></div><span>0%</span>';
              msg.querySelector('.msg-body').appendChild(wrap);
            }
            var timer = null, pct = 0;
            function set(v) {
              pct = Math.max(0, Math.min(100, Math.round(v)));
              if (!wrap) return;
              wrap.querySelector('.track i').style.width = pct + '%';
              wrap.querySelector('span').textContent = pct + '%';
            }
            function tick() {
              if (!wrap || !wrap.isConnected) { if (timer) clearInterval(timer); return; }
              set(pct + (90 - pct) * 0.16 + 1.5);
            }
            if (wrap) timer = setInterval(tick, 420);
            return {
              done: function(){
                if (timer) clearInterval(timer);
                set(100);
                if (wrap) {
                  wrap.classList.add('done');
                  wrap.querySelector('span').textContent = '完成';
                  setTimeout(function(){ if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 900);
                }
              },
              fail: function(){
                if (timer) clearInterval(timer);
                if (wrap) {
                  wrap.classList.add('err');
                  wrap.querySelector('span').textContent = '合成失败';
                  setTimeout(function(){ if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 2200);
                }
              }
            };
          }
          /* 重渲染后重绑播放按钮：删除旧消息等轻量重绘不应掐断正在听的音频。
             按播放文本找回新 DOM 里的朗读按钮并恢复播放态；
             找不到（删的正是播放中的那条）才真正停止。 */
          function rebindPlayingBtn() {
            if (!state.playing || !state.playingText) return;
            var found = null;
            msgsInner.querySelectorAll('.msg.ai').forEach(function(m){
              if (found) return;
              var b = m.querySelector('.bubble');
              if (b && b.textContent === state.playingText) found = m;
            });
            if (found) {
              var nb = found.querySelector('.speak-wrap .speak-btn') || found.querySelector('.speak-btn');
              state.playingBtn = nb || null;
              state.playingMsg = found;
              if (nb) {
                setSpeak(nb, state.playing.paused ? '已暂停' : '播放中…', true);
                setBtnWave(nb, !state.playing.paused);
                setMsgSpeaking(nb, true);
              }
            } else {
              stopAudio();
            }
          }
          function stopAudio() {
            if (state.playing) {
              try {
                /* 打标记：该音频后续的 ended/error 回调直接忽略，
                   不再覆盖新播放状态（原实现还调了 load()，会触发异步 error
                   事件把刚复位的按钮文案改成"朗读失败"） */
                state.playing._stopped = true;
                if (state.playing.stop) state.playing.stop();  // APK 代理：原生真正停止
                else state.playing.pause();
              } catch (e) {}
              state.playing = null;
              state.playingMsg = null;
              state.playingText = null;
            }
            /* 被新播放掐断的旧按钮要复位，否则会永远停在"播放中…" */
            if (state.playingBtn) {
              setSpeak(state.playingBtn, '朗读', false);
              setBtnWave(state.playingBtn, false);
              setMsgSpeaking(state.playingBtn, false);
              state.playingBtn = null;
            }
          }

          /* APK 环境：音频播放交给原生 MediaPlayer+MediaSession（触发系统媒体控件/流体云）；
             网页环境：退回标准 Audio。代理对象实现标准 Audio 的常用接口。
             httpUrl：blob: URL 原生播不了，优先用后端持久化 URL（本地代理转发自动带 cookie）。 */
          function createAudio(url, httpUrl) {
            if (!(window.AndroidBridge && window.AndroidBridge.playAudio)) return new Audio(url);
            var a = {
              src: url,
              _httpUrl: httpUrl || null,
              _loop: false,
              _speed: 1,
              paused: true,
              _stopped: false,
              _handlers: { ended: [], error: [] },
              addEventListener: function(type, fn) {
                if (this._handlers[type]) this._handlers[type].push(fn);
              },
              play: function() {
                var self = this;
                if (this._stopped) return Promise.resolve();
                var ok = false;
                try {
                  var src = this.src;
                  if (src.indexOf('blob:') === 0 && this._httpUrl) src = this._httpUrl;
                  if (src.indexOf('blob:') === 0) {
                    // 无持久化 URL 兜底：blob 转 base64 交原生播放
                    return fetch(src).then(function(r){ return r.arrayBuffer(); }).then(function(buf){
                      if (self._stopped) return;
                      var b64 = '';
                      var bytes = new Uint8Array(buf);
                      var chunk = 0x8000;
                      for (var i = 0; i < bytes.length; i += chunk) {
                        b64 += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                      }
                      b64 = btoa(b64);
                      ok = window.AndroidBridge.playAudioBase64(b64, self._loop);
                      if (!ok) throw new Error('play failed');
                      self._registerCallbacks();
                      return;
                    });
                  }
                  ok = window.AndroidBridge.playAudio(src, this._loop);
                } catch (e) {}
                if (!ok) return Promise.reject(new Error('play failed'));
                this._registerCallbacks();
                return Promise.resolve();
              },
              _registerCallbacks: function() {
                var self = this;
                this.paused = false;
                window.__audioEnded = function() {
                  self.paused = true;
                  self._handlers.ended.forEach(function(f){ try { f(); } catch (e) {} });
                };
                window.__audioError = function() {
                  self.paused = true;
                  self._handlers.error.forEach(function(f){ try { f(); } catch (e) {} });
                };
              },
              pause: function() {
                this.paused = true;
                try { window.AndroidBridge.pauseAudio(); } catch (e) {}
              },
              stop: function() {
                this._stopped = true;
                this.paused = true;
                try { window.AndroidBridge.stopAudio(); } catch (e) {}
              },
            };
            Object.defineProperty(a, 'loop', {
              get: function() { return this._loop; },
              set: function(v) {
                this._loop = !!v;
                try { window.AndroidBridge.setLoop(!!v); } catch (e) {}
              },
            });
            Object.defineProperty(a, 'playbackRate', {
              get: function() { return this._speed; },
              set: function(v) { this._speed = v || 1; },  // 语速已由后端按 speed 合成，原生侧不重复变速
            });
            return a;
          }
          /* 保存/下载合成音频到本地：绕过前端内存缓存，直接请求后端
             （后端 tts_cache 磁盘缓存命中，秒回；force=false 复用原合成结果）。
             文件名带文本前缀与时间戳，方便识别。 */
          function makeSpeakBtn(text, style, msg, sid) {
            var wrap = document.createElement('span');
            wrap.className = 'speak-wrap';
            var b = document.createElement('button');
            b.className = 'speak-btn';
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/></svg><span class="speak-label">朗读</span>';
            b._msg = msg || null;
            b._sid = sid || null;
            b.onclick = function(){ ttsAndPlay(text, b, style, false, msg, sid).catch(function(){}); };
            /* 循环播放开关：开启后该条音频播完自动从头再播。
               偏好持久化进消息（msg.loop，随会话保存）：切会话/重渲染后仍保持。 */
            var lb = document.createElement('button');
            lb.type = 'button';
            lb.className = 'loop-btn';
            lb.title = '循环播放';
            b._loop = !!(msg && msg.loop);
            lb.classList.toggle('on', !!b._loop);
            lb.setAttribute('aria-pressed', b._loop ? 'true' : 'false');
            if (b._loop) lb.title = '已开启循环播放';
            lb.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/></svg>';
            lb.onclick = function(ev) {
              if (ev) { ev.preventDefault(); ev.stopPropagation(); }
              b._loop = !b._loop;
              lb.classList.toggle('on', !!b._loop);
              lb.setAttribute('aria-pressed', b._loop ? 'true' : 'false');
              lb.title = b._loop ? '已开启循环播放' : '循环播放';
              try {
                if (msg) msg.loop = !!b._loop;
                saveSessions();
              } catch (e) {}
              /* 若该条正在播放，实时生效，无需重新点朗读 */
              if (state.playingBtn === b && state.playing) state.playing.loop = !!b._loop;
            };
            wrap.appendChild(b);
            wrap.appendChild(lb);
            wrap._speak = b; /* 调用处传入 wrap 时据此解包到朗读按钮 */
            return wrap;
          }
          function makeResynthBtn(text, style, msg, sid) {
            var b = document.createElement('button');
            b.className = 'speak-btn resynth-btn';
            b.title = '重新合成并替换当前音频';
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg><span class="speak-label">重新合成</span>';
            b._msg = msg || null;
            b._sid = sid || null;
            b.onclick = function(){ ttsAndPlay(text, b, style, true, msg, sid).catch(function(){}); };
            return b;
          }
          async function ttsAndPlay(text, btn, style, force, msg, sid) {
            force = !!force;
            if (btn && btn._speak) btn = btn._speak; /* 朗读按钮组容器 -> 解包到朗读按钮 */
            /* 兜底：调用方未显式传 msg/sid 时（如自动朗读），从按钮上携带的引用取，
               保证自动播放的音频也能持久化进消息 */
            if (!msg && btn && btn._msg) msg = btn._msg;
            if (!sid && btn && btn._sid) sid = btn._sid;
            /* 播放/暂停切换：同一条正在播放时再点一下暂停，再点一下继续 */
            if (!force && state.playing && state.playingBtn === btn) {
              if (state.playing.paused) {
                var rp = state.playing.play();
                if (rp && rp.catch) rp.catch(function(){});
                setSpeak(btn, '播放中…', true);
                setBtnWave(btn, true);
                setMsgSpeaking(btn, true);
              } else {
                state.playing.pause();
                setSpeak(btn, '已暂停', true);
                setBtnWave(btn, false);
                setMsgSpeaking(btn, false);
              }
              return;
            }
            /* 播放代次：每次发起播放意图自增，任何更新的请求都会让本请求作废，
               避免并发合成完成后"复活"抢占播放（新旧音频叠加的混乱根源） */
            var mySeq = ++state.playSeq;
            /* 立即打断当前播放：点新朗读/新自动播放即刻静音旧的，
               不再等新音频合成完成才切换（合成期间旧音频继续响 = "同时播放"的观感） */
            stopAudio();
            // 语速由 Audio.playbackRate 控制，不再作为 cache key — 同一句不同语速复用同一份音频；
            // 引擎/音色/模型是 cache key 的一部分（state.voiceKey），切引擎后自动重新合成、不串音
            var key = (state.voiceKey || state.voiceProvider || '') + '\u0001' + (style || '') + '\u0001' + text;
            var url = state.audioCache[key];
            var prog = null;
            var a = null;  /* 先声明：合成在 new Audio 之前失败时，catch 分支可安全引用 */
            if ((!url || force) && btn) {
              setSpeak(btn, force ? '重新合成…' : '正在合成…', true);
              prog = startSynthProgress(btn);
            }
            try {
              if (!url || force) {
                // 已持久化到消息的音频 URL（刷新/切会话后仍在）：直接取，免重新合成
                if (!force && msg && msg.audio) {
                  /* 读已缓存音频也必须有上限：这条 GET 半开时不设限会让整个朗读流程
                     永不 settle（按钮停在"正在合成…"），这里超时后自然落到下面的合成兜底 */
                  var ato = apiTimeoutSignal(null, 20000);
                  try {
                    var gr = await fetch(msg.audio, { signal: ato.signal });
                    if (gr.ok) {
                      var gu = URL.createObjectURL(await gr.blob());
                      if (state.audioCache[key]) URL.revokeObjectURL(state.audioCache[key]);
                      state.audioCache[key] = gu;
                      url = gu;
                    }
                  } catch (e) { /* 落到下面的合成兜底 */ }
                  finally { ato.done(); }
                }
                if (!url || force) {
                  if (!force && state.ttsInflight[key]) {
                    // 同一句已有合成请求在飞（自动朗读+手动点朗读撞车），直接等它结果，不重复请求
                    url = await state.ttsInflight[key];
                  } else {
                    /* 硬超时：/api/tts 要真的合成音频（长文/弱网可达数十秒），但半开连接
                       （ngrok 抖动、切网、后端重启未发 RST）时它会永不 settle —— 而返回的
                       Promise 又被 state.ttsInflight 缓存，于是"点重试"拿到的是同一个死
                       Promise，朗读按钮永久停在"正在合成…"，只能刷新页面。给 60s 上限。 */
                    var tto = apiTimeoutSignal(null, 60000);
                    var job = (async function(){
                      var r = await fetch('/api/tts', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: text, style: style || '', speed: state.speed, force: force }),
                        signal: tto.signal,
                      });
                      if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
                      if (!r.ok) throw new Error('TTS 失败');
                      // 把稳定可回放/下载的音频 URL 持久化进消息，随对话一起保存；
                      // silent：听语音不是对话活动，不能刷新会话排序（markSessionActivity 内部已 save）
                      var cacheHash = r.headers.get('X-TTS-Cache');
                      if (cacheHash && msg && sid) {
                        msg.audio = '/api/tts/file?h=' + cacheHash;
                        try { markSessionActivity(sid, null, true); } catch (e) {}
                      }
                      // 释放旧 URL 避免内存泄漏
                      if (state.audioCache[key]) URL.revokeObjectURL(state.audioCache[key]);
                      var u = URL.createObjectURL(await r.blob());
                      state.audioCache[key] = u;
                      // 超过 50 条时淘汰最旧条目，防止无界增长（跳过正在播放的 URL）
                      var ck = Object.keys(state.audioCache);
                      if (ck.length > 50) {
                        for (var ci = 0; ci < ck.length; ci++) {
                          var oldU = state.audioCache[ck[ci]];
                          if (state.playing && state.playing.src === oldU) continue;
                          URL.revokeObjectURL(oldU);
                          delete state.audioCache[ck[ci]];
                          break;
                        }
                      }
                      return u;
                    })();
                    if (!force) state.ttsInflight[key] = job;
                    try {
                      url = await job;
                    } catch (e) {
                      /* 超时给出可读中文：浏览器原生的 "Failed to fetch"/AbortError 对用户毫无信息量 */
                      if (tto.timedOut()) throw new Error('语音合成超时，请重试');
                      throw e;
                    } finally {
                      if (!force) delete state.ttsInflight[key];
                      tto.done();
                    }
                  }
                }
              }
              if (prog) { prog.done(); prog = null; }
              /* 合成期间已有更新的播放请求（点了别的/又点了一次）：本请求作废，
                 不播放、不覆盖状态，杜绝旧请求晚到造成的叠加发声 */
              if (mySeq !== state.playSeq) return;
              stopAudio();
              /* blob: URL 原生播不了：优先用后端持久化 URL（经本地代理自动带 cookie） */
              var httpUrl = (msg && msg.audio) ? location.origin + msg.audio : null;
              var a = createAudio(url, httpUrl);  // APK 环境走原生播放（流体云），网页环境退回 Audio
              a.playbackRate = state.speed;  // 前端原生调速，零延迟、不占后端缓存
              a.loop = !!(btn && btn._loop); // 该条开启循环时，播完自动从头再播
              state.playing = a;
              state.playingBtn = btn || null;
              /* 播放中对齐：记录当前播放消息与全文（点击分段跳转用） */
              state.playingMsg = btn ? btn.closest('.msg') : null;
              state.playingText = text;
              var finish = function(label) {
                if (a._stopped) return; /* 被 stopAudio 掐断的旧音频，事件忽略 */
                if (state.playing === a) state.playing = null;
                if (btn) {
                  setSpeak(btn, label, false);
                  setBtnWave(btn, false);
                  setMsgSpeaking(btn, false);
                }
                if (state.playingBtn === btn) state.playingBtn = null;
              };
              a.addEventListener('ended', function(){ finish(force ? '重新合成' : '重听'); });
              a.addEventListener('error', function(){ finish(force ? '重新合成失败' : '朗读失败'); });
              if (btn) {
                setSpeak(btn, '播放中…', true);
                setBtnWave(btn, true);
                setMsgSpeaking(btn, true);
              }
              await a.play();
            } catch (e) {
              if (prog) prog.fail();
              if (mySeq !== state.playSeq) return; /* 过期请求的失败提示不再弹出 */
              /* 浏览器自动播放策略拦截（首条问候语自动朗读最常见）：不算错误，
                 静默复位为「朗读」，用户点一下即可播放，不再弹 toast 吓人 */
              if (e && e.name === 'NotAllowedError') {
                if (state.playing === a) state.playing = null;
                if (btn) {
                  setSpeak(btn, '朗读', false);
                  setBtnWave(btn, false);
                  setMsgSpeaking(btn, false);
                }
                return;
              }
              if (btn) setSpeak(btn, force ? '重新合成失败' : '朗读失败', false);
              toast((force ? '重新合成失败：' : '朗读失败：') + e.message);
            }
          }

          /* ---------- 重新生成 ---------- */
          /* 每条 AI 回复下方的「重新生成」按钮：丢弃这条回复及其之后的消息，
             用触发它的那条用户输入重新请求一次模型，不满意的生成可以随时重来。 */
          function makeRegenBtn(div) {
            var b = document.createElement('button');
            b.className = 'meta-btn regen-btn';
            b.title = '重新生成这条回复';
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg><span>重新生成</span>';
            b.addEventListener('click', function(){ regenerate(div, b); });
            return b;
          }
          async function regenerate(div, btn) {
            /* 与发送入口同一把门禁：生成中再点旧消息的"重新生成"会 splice 截断
               正在使用的历史、覆盖唯一的 chatAborter，并产生两条错乱回复 */
            if ((state.sendingCount || 0) > 0 || state.uploading) { toast('正在生成中，请稍候'); return; }
            if (btn && btn.disabled) return;
            if (btn) btn.disabled = true;
            var s = currentSession();
            if (!s) { if (btn) btn.disabled = false; return; }
            /* 定位这条回复：优先用渲染下标（精确），失效时按 role+content 倒查
               （模型偶发复读会产生多条相同 content，正序会命中更早那条导致删错位置） */
            var idx = -1;
            var hidx = parseInt(div.dataset.hidx, 10);
            var _bubbleTxt = div.querySelector('.bubble').textContent;
            if (!isNaN(hidx) && s.history[hidx]
                && s.history[hidx].role === 'assistant'
                && s.history[hidx].content === _bubbleTxt) {
              idx = hidx;
            } else {
              for (var i = s.history.length - 1; i >= 0; i--) {
                if (s.history[i].role === 'assistant' && s.history[i].content === _bubbleTxt) { idx = i; break; }
              }
            }
            if (idx < 0) {
              if (btn) btn.disabled = false;
              toast('找不到对应的消息，可能已被删除');
              return;
            }
            /* 往前找触发它的那条用户消息；历史里该用户消息之前的部分作为上下文 */
            var u = idx - 1;
            while (u >= 0 && s.history[u].role !== 'user') u--;
            if (u < 0) {
              if (btn) btn.disabled = false;
              toast('找不到触发这条回复的消息，无法重新生成');
              return;
            }
            var userMsg = s.history[u];
            var userText = userMsg.content || ((userMsg.attachments && userMsg.attachments.length) ? '（发送了附件）' : '');
            var userAtts = userMsg.attachments || [];
            var hist = s.history.slice(0, u);
            s.history.splice(idx); /* 丢掉旧回复及其之后的所有消息 */
            state.history = s.history;
            markSessionActivity(s.id);
            markAwaiting(s.id, true); /* 先标记：renderCurrentSession 末尾用户消息不会误补失败气泡 */
            /* 整页重渲染同步 DOM 与数据：splice 后若该回复之后还有消息（用户又发了新内容），
               仅删当前气泡会造成界面残留，必须重绘让后续消息一起消失（与编辑消息同一套逻辑） */
            renderCurrentSession();
            showTyping();
            var sessionId = s.id;
            try {
              var historyPayload = hist.slice(-20).map(function(m){
                if (m.content) return m;
                var mm = Object.assign({}, m);
                mm.content = (m.attachments && m.attachments.length) ? '（发送了附件）' : '';
                return mm;
              });
              await streamReplyInto(
                sessionId,
                { idx: u, text: userText, atts: userAtts },
                { message: userText, history: historyPayload, attachments: userAtts }
              );
            } catch (e) {
              hideTyping();
              toast('重新生成失败：' + (e && e.message));
            } finally {
              markAwaiting(sessionId, false);
              if (btn) {
                btn.disabled = false;
                var l2 = btn.querySelector('span');
                if (l2) l2.textContent = '重新生成';
              }
            }
          }

          /* ---------- 失败重试 ----------
             生成失败（网络错误/后端报错/空回复）时统一落一条带「重试」按钮的失败气泡。
             旧实现失败时只弹 toast 或插一条无按钮、不入库的错误气泡，刷新/切会话后
             只剩用户消息没有任何回复，也没有重新生成入口。
             snap = { idx: 用户消息在历史中的下标, text: 用户消息原文, atts: 用户附件 } */
          /* 按会话记录进行中的生成请求数：渲染时末尾用户消息是"生成中"还是"失败"靠它区分。
             注：发送/重新生成/重试三个入口统一由全局 sendingCount 门禁串行化，
             并发生成会截断在途历史、覆盖 abort 句柄并产生错乱回复，故不允许 */
          function markAwaiting(sessionId, on) {
            if (!sessionId) return;
            state.awaiting[sessionId] = Math.max(0, (state.awaiting[sessionId] || 0) + (on ? 1 : -1));
            var n = state.awaiting[sessionId];
            if (n === 0) delete state.awaiting[sessionId]; /* 计数归零即清理键，防无界残留 */
            /* 桌宠联动：生成中 → thinking；全部结束 → idle。
               旧实现只在 on 时派发 thinking、off 时等 TTS 事件收尾，
               但生成失败/用户切会话的路径没有 TTS 收尾 → 桌宠永久卡在思考动画。
               生成成功且 TTS 正在播放的场景，TTS 自己的状态事件会覆盖/收尾，idle 无害。 */
            try {
              window.dispatchEvent(new CustomEvent('pet:state', {
                detail: { state: on ? 'thinking' : 'idle' }
              }));
            } catch (e) {}
          }
          function makeRetryBtn(sessionId, snap) {
            var b = document.createElement('button');
            b.className = 'meta-btn regen-btn';
            b.title = '重新发送这条消息并生成回复';
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg><span>重试</span>';
            b.addEventListener('click', function(){ retryGeneration(sessionId, snap, b); });
            return b;
          }
          function addFailureMsg(sessionId, reason, snap, opts) {
            /* 用户已切走会话时不插错视图；切回该会话渲染时，
               末尾用户消息的逻辑会补出带重试按钮的失败气泡 */
            if (currentSessionId !== sessionId) return null;
            var div = addMsg('assistant', reason || '生成失败，请重试', opts);
            div.classList.add('failed');
            var meta = div.querySelector('.msg-meta');
            if (meta) meta.appendChild(makeRetryBtn(sessionId, snap));
            return div;
          }
          async function retryGeneration(sessionId, snap, btn) {
            if ((state.sendingCount || 0) > 0 || state.uploading) { toast('正在生成中，请稍候'); return; }
            if (btn && btn.disabled) return;
            if (btn) btn.disabled = true;
            var s = findSession(sessionId);
            if (!s) { if (btn) btn.disabled = false; return; }
            /* 定位触发消息：优先记录下标；历史发生过增删导致下标失效时，退回按内容倒查 */
            var u = -1;
            var at = s.history[snap.idx];
            if (at && at.role === 'user' && (at.content || '') === snap.text) u = snap.idx;
            else {
              for (var i = s.history.length - 1; i >= 0; i--) {
                if (s.history[i].role === 'user' && (s.history[i].content || '') === snap.text) { u = i; break; }
              }
            }
            if (u < 0) {
              if (btn) btn.disabled = false;
              toast('找不到对应的消息，可能已被删除');
              return;
            }
            var userMsg = s.history[u];
            var userText = userMsg.content || ((userMsg.attachments && userMsg.attachments.length) ? '（发送了附件）' : '');
            var userAtts = userMsg.attachments || snap.atts || [];
            /* 防数据丢失：只有该消息是历史最后一条时才允许重试。
               若失败后用户又发了新消息并成功（u 之后还有内容），splice(u+1)
               会把后续成功对话全部删掉——此时拒绝重试，提示用户。 */
            if (u < s.history.length - 1) {
              if (btn) {
                btn.disabled = false;
                var l0 = btn.querySelector('span');
                if (l0) l0.textContent = '重试';
              }
              toast('这条消息之后还有新对话，无法重试（避免删除已成功的消息）');
              return;
            }
            var hist = s.history.slice(0, u);
            s.history.splice(u + 1); /* 丢弃该用户消息之后的残留（u 已是末尾，安全） */
            state.history = s.history;
            var failDiv = btn ? btn.closest('.msg') : null;
            if (failDiv) failDiv.remove();
            markSessionActivity(s.id);
            showTyping();
            var lab = btn && btn.querySelector('span');
            if (lab) lab.textContent = '重试中…';
            markAwaiting(sessionId, true);
            try {
              var historyPayload = hist.slice(-20).map(function(m){
                if (m.content) return m;
                var mm = Object.assign({}, m);
                mm.content = (m.attachments && m.attachments.length) ? '（发送了附件）' : '';
                return mm;
              });
              await streamReplyInto(
                sessionId,
                { idx: u, text: snap.text, atts: snap.atts },
                { message: userText, history: historyPayload, attachments: userAtts }
              );
            } catch (e) {
              hideTyping();
              addFailureMsg(sessionId, (e && e.message) || '生成失败，请重试', { idx: u, text: snap.text, atts: snap.atts });
            } finally {
              markAwaiting(sessionId, false);
              if (btn) {
                btn.disabled = false;
                var l2 = btn.querySelector('span');
                if (l2) l2.textContent = '重试';
              }
            }
          }

          /* ---------- 附件 ---------- */
          var attachBtn = document.getElementById('attach-btn');
          var attachMenu = document.getElementById('attach-menu');
          var attachInput = document.getElementById('attach-input');
          var attachPreview = document.getElementById('attach-preview');
          function toggleAttachMenu(force) {
            var show = force === undefined ? !state.attachMenuOpen : !!force;
            if (attachMenu) attachMenu.classList.toggle('show', show);
            state.attachMenuOpen = show;
          }
          function openAttachPicker(kind) {
            if (!attachInput) return;
            toggleAttachMenu(false); /* 先收菜单：用户取消文件选择时不会留一个悬空菜单 */
            attachInput.accept = kind === 'image'
              ? 'image/*'
              : (kind === 'doc'
                ? '.pdf,.doc,.docx,.txt,.md,.csv,.json,.log,.xls,.xlsx,.ppt,.pptx'
                : '');
            attachInput.value = '';
            attachInput.click();
          }
          function addPendingAttachment(file) {
            if (!file) return;
            if (state.pendingAttachments.length >= 8) { toast('一次最多发送 8 个附件'); return; }
            if (file.size > 15 * 1024 * 1024) { toast(file.name + ' 超过 15MB，无法发送'); return; }
            state.pendingAttachments.push({
              id: 'p' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
              file: file,
              name: file.name,
              size: file.size,
              preview: file.type && file.type.indexOf('image/') === 0 ? URL.createObjectURL(file) : null
            });
            renderPendingAttachments();
          }
          function removePendingAttachment(id) {
            for (var i = 0; i < state.pendingAttachments.length; i++) {
              if (state.pendingAttachments[i].id === id) {
                if (state.pendingAttachments[i].preview) URL.revokeObjectURL(state.pendingAttachments[i].preview);
                state.pendingAttachments.splice(i, 1);
                break;
              }
            }
            renderPendingAttachments();
          }
          function clearPendingAttachments() {
            state.pendingAttachments.forEach(function(a){
              if (a.preview) URL.revokeObjectURL(a.preview);
            });
            state.pendingAttachments = [];
            renderPendingAttachments();
          }
          function renderPendingAttachments() {
            if (!attachPreview) return;
            attachPreview.innerHTML = '';
            state.pendingAttachments.forEach(function(a){
              var chip = document.createElement('div');
              chip.className = 'att-chip';
              if (a.preview) {
                var img = document.createElement('img');
                img.src = a.preview; img.alt = a.name;
                chip.appendChild(img);
              } else {
                var ic = document.createElement('span');
                ic.className = 'att-chip-icon';
                ic.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M12 18v-6"/><path d="m9 15 3 3 3-3"/></svg>';
                chip.appendChild(ic);
              }
              var info = document.createElement('span');
              info.className = 'att-chip-info';
              var nm = document.createElement('strong'); nm.textContent = a.name; info.appendChild(nm);
              var sz = document.createElement('small'); sz.textContent = formatFileSize(a.size); info.appendChild(sz);
              chip.appendChild(info);
              var rm = document.createElement('button');
              rm.type = 'button'; rm.className = 'att-chip-rm'; rm.title = '移除'; rm.textContent = '×';
              rm.addEventListener('click', function(){ removePendingAttachment(a.id); });
              chip.appendChild(rm);
              attachPreview.appendChild(chip);
            });
            attachPreview.classList.toggle('has', state.pendingAttachments.length > 0);
          }
          /* ---------- 拖拽文件到窗口发送（桌面端自然交互）：
             dragenter/dragleave 深度计数（进出子元素都会冒泡），drop 复用附件校验 ---------- */
          (function setupDragDrop(){
            var dragDepth = 0;
            function hasFiles(e){
              var t = e.dataTransfer && e.dataTransfer.types;
              if (!t) return false;
              for (var i = 0; i < t.length; i++){ if (t[i] === 'Files') return true; }
              return false;
            }
            window.addEventListener('dragenter', function(e){
              if (!hasFiles(e)) return;
              e.preventDefault();
              dragDepth++;
              document.body.classList.add('drag-over');
            });
            window.addEventListener('dragover', function(e){
              if (hasFiles(e)) e.preventDefault();  /* 不阻止的话浏览器会直接打开文件 */
            });
            window.addEventListener('dragleave', function(e){
              if (!hasFiles(e)) return;
              dragDepth = Math.max(0, dragDepth - 1);
              if (dragDepth === 0) document.body.classList.remove('drag-over');
            });
            window.addEventListener('drop', function(e){
              if (!hasFiles(e)) return;
              e.preventDefault();
              dragDepth = 0;
              document.body.classList.remove('drag-over');
              var n = 0;
              Array.prototype.forEach.call(e.dataTransfer.files, function(f){
                addPendingAttachment(f); n++;
              });
              if (n) toast('已添加 ' + n + ' 个文件，点发送即可');
            });
          })();
          /* 图片上传前本地压缩（加量不加价：省流量省磁盘，视觉模型读图更快）。
             手机照片动辄 3~8MB，canvas 重采样到长边 1600 + JPEG 0.85 后通常 <500KB，
             肉眼无感。规则：>400KB 的位图才压；GIF 跳过（保留动画）；PNG 仍走 PNG
             （保留透明通道，尺寸缩小体积也随之下降）；解码失败一律回退原图。 */
          var IMG_COMPRESS_MIN_BYTES = 400 * 1024;
          var IMG_COMPRESS_MAX_EDGE = 1600;
          function compressImageFile(file) {
            return new Promise(function(resolve){
              var done = false;
              function finish(f) { if (!done) { done = true; try { URL.revokeObjectURL(url); } catch (e) {} resolve(f || file); } }
              var url = '';
              try {
                if (!file || !file.type || file.type.indexOf('image/') !== 0 || file.type === 'image/gif') return finish(file);
                if (file.size <= IMG_COMPRESS_MIN_BYTES) return finish(file);
                if (!window.URL || !URL.createObjectURL || !document.createElement('canvas').getContext) return finish(file);
                url = URL.createObjectURL(file);
                var img = new Image();
                img.onload = function(){
                  try {
                    var w = img.naturalWidth, h = img.naturalHeight;
                    if (!w || !h) return finish(file);
                    var scale = Math.min(1, IMG_COMPRESS_MAX_EDGE / Math.max(w, h));
                    var cw = Math.max(1, Math.round(w * scale)), ch = Math.max(1, Math.round(h * scale));
                    var canvas = document.createElement('canvas');
                    canvas.width = cw; canvas.height = ch;
                    var ctx = canvas.getContext('2d');
                    if (!ctx) return finish(file);
                    ctx.drawImage(img, 0, 0, cw, ch);
                    var outType = (file.type === 'image/png') ? 'image/png' : 'image/jpeg';
                    if (!canvas.toBlob) return finish(file);
                    canvas.toBlob(function(blob){
                      try {
                        if (blob && blob.size < file.size) {
                          var name = (file.name || 'image').replace(/\.[^.]+$/, '') + (outType === 'image/png' ? '.png' : '.jpg');
                          var nf;
                          try { nf = new File([blob], name, { type: outType }); } catch (e2) { nf = blob; try { blob.name = name; } catch (e3) {} }
                          finish(nf);
                        } else finish(file); /* 压完反而更大（已高度压缩的图）：用原图 */
                      } catch (e) { finish(file); }
                    }, outType, 0.85);
                  } catch (e) { finish(file); }
                };
                img.onerror = function(){ finish(file); };
                img.src = url;
                setTimeout(function(){ finish(file); }, 8000); /* 兜底：解码卡死不让发送流程挂住 */
              } catch (e) { finish(file); }
            });
          }
          async function uploadPendingAttachments() {
            var out = [];
            /* 并行上传：串行时多文件耗时线性叠加；后端每请求独立写盘，可安全并发。
               图片先本地压缩再上传（异步并行），失败/不适用时回退原文件 */
            /* 每个上传自带 60s 上限：api() 的默认 20s 对大图/弱网偏紧，但绝不能不设。
               没有超时时，任一上传卡住 -> Promise.all 永不 settle -> send() 的 finally
               永不执行 -> state.uploading 永久为 true，发送/编辑/重新生成/重试四个入口
               被同一个闸门同时挡死，只能刷新页面才能恢复。 */
            var jobs = state.pendingAttachments.map(async function(p){
              var f = p.file;
              if (f && f.type && f.type.indexOf('image/') === 0) {
                try { f = await compressImageFile(f); } catch (e2) {}
              }
              var fd = new FormData();
              fd.append('files', f, (f && f !== p.file && f.name) ? f.name : p.name);
              var upCtl = null;
              try { upCtl = new AbortController(); } catch (e2) { upCtl = null; }
              var upTimer = upCtl ? setTimeout(function(){ try { upCtl.abort(); } catch (e3) {} }, 60000) : 0;
              return api('/api/upload', { method: 'POST', body: fd, signal: upCtl ? upCtl.signal : undefined })
                .then(function(r){
                  return (r && r.files && r.files.length) ? r.files[0] : null;
                })
                .finally(function(){ if (upTimer) clearTimeout(upTimer); });
            });
            var results = await Promise.all(jobs);
            results.forEach(function(x){ if (x) out.push(x); });
            return out;
          }
          function friendlySendError(e) {
            var m = (e && e.message) || '';
            /* 已是中文友好提示（api/chatStream 抛出的隧道/网络文案）直接透传，不再二次包装 */
            if (/网络连接失败|隧道已离线|隧道返回了网页|已停止生成/.test(m)) return m;
            if (/405|Method Not Allowed/.test(m)) return '后端版本太旧，请先重启服务再发送附件';
            if (/404|Not Found/.test(m)) return '后端版本太旧，请先重启服务再发送附件';
            return friendlyNetError(e);
          }

          /* ---------- 对话 ---------- */
          async function send(text) {
            text = (text || '').trim();
            var sendBtn = document.querySelector('.send-btn');
            var pending = state.pendingAttachments.slice();
            if (!text && !pending.length) return;
            /* 生成中：Enter/粘贴发送不再吞字（旧实现点击入口先清空输入框，
               这里直接拒收并提示，用发送按钮点按来停止） */
            if ((state.sendingCount || 0) > 0 || state.uploading) { toast('正在生成中，点击发送按钮可停止'); return; }
            if (sendBtn && sendBtn.disabled) return;
            toggleAttachMenu(false);
            var sessionId = currentSessionId;
            var s = findSession(sessionId);
            if (!s) {
              /* 当前会话已失效：输入框内容原样保留（本次不再预清空），切回主对话再提示 */
              currentSession();
              renderCurrentSession();
              toast('当前会话已失效，已为你切回主对话，请再发一次');
              return;
            }
            /* 输入框清空收归 send() 独占：准入检查通过后才清空。
               旧实现点击入口先清空再调 send，生成中连按 Enter 会清空后被拒收 = 丢消息。 */
            var t0 = document.getElementById('text');
            if (t0) { t0.value = ''; t0.style.height = 'auto'; }
            clearDraft();
            stickBottom = true; /* 自己发消息：强制恢复贴底跟随 */
            /* 附件上传阶段短暂禁用按钮（上传不可 abort，时间很短）；流式阶段按钮保持可点=停止 */
            if (sendBtn) { sendBtn.disabled = true; sendBtn.classList.add('loading'); }
            /* sendingCount 要到 streamReplyInto 里才 +1，而附件上传要数秒乃至数十秒，
               这段窗口里 sendingCount 仍是 0 —— 修改/重新生成/重试各自只查 sendingCount，
               于是能挤进来再起一条流，把单槽 chatAborter 覆盖掉（旧流停不掉、白烧超时）。
               上传期间单独占一个闸门位。 */
            state.uploading = true;
            var atts = [];
            var snap = null;
            try {
              if (pending.length) {
                atts = await uploadPendingAttachments();
                if (!atts.length) throw new Error('附件上传失败，请重试');
              }
              /* 弱网下上传可能耗时数秒~数十秒，期间用户可在侧栏切换/新建/删除会话。
                 复检会话归属：不再是当前会话就停止全部 UI 操作，否则用户气泡、typing、
                 流式气泡会插进新会话视图（数据仍按 sessionId 入库，视图不能串台）。
                 文本恢复为原会话草稿，切回去还能原样重发。 */
              if (currentSessionId !== sessionId || !findSession(sessionId)) {
                try { if (text) localStorage.setItem(draftKey(sessionId), text); } catch (e2) {}
                return;
              }
              /* 上传期间的多端合并可能已把该会话对象整体替换（旧引用脱离数组）：
                 必须重新取最新引用，否则 push 进旧对象 = 这条用户消息永久丢失 */
              s = findSession(sessionId);
              if (!s) return;
              var userDiv = addMsg('user', text, { attachments: atts });
              s.history.push({ role: 'user', content: text, attachments: atts, ts: Date.now() });
              userDiv.dataset.hidx = String(s.history.length - 1);
              /* 记录本轮用户消息的定位信息：生成失败时用它挂「重试」按钮 */
              snap = { idx: s.history.length - 1, text: text, atts: atts };
              markSessionActivity(sessionId, text || (atts[0] && atts[0].name));
              clearPendingAttachments();
              try { localStorage.setItem('xiaoni_last_chat_time', Date.now().toString()); } catch(e) {}
              showTyping();
              markAwaiting(sessionId, true);
              /* 上传结束进入流式：恢复按钮可点态（点按=停止生成），转圈由 syncSendBtn 接管 */
              if (sendBtn) sendBtn.disabled = false;
              try {
                var historyPayload = s.history.slice(0, -1).slice(-20).map(function(m){
                  if (m.content) return m;
                  var mm = Object.assign({}, m);
                  mm.content = (m.attachments && m.attachments.length) ? '（发送了附件）' : '';
                  return mm;
                });
                /* history 只发历史轮次（当前消息刚被 push 到末尾，先去掉再发），
                   当前消息由后端通过 message + attachments 字段追加；stream 流式渲染 */
                await streamReplyInto(sessionId, snap, { message: text, history: historyPayload, attachments: atts });
              } finally {
                markAwaiting(sessionId, false);
              }
            } catch (e) {
              hideTyping();
              if (snap) {
                /* 用户消息已入库但后续环节出错：同样给出可重试的失败气泡 */
                addFailureMsg(sessionId, friendlySendError(e), snap);
              } else {
                toast('发送失败：' + friendlySendError(e));
                /* 消息还没进会话（如附件上传失败）：文本恢复为原会话草稿；
                   仍停留在该会话时直接回填输入框，已切走则只存草稿不灌进新会话 */
                try { if (text) localStorage.setItem(draftKey(sessionId), text); } catch (e2) {}
                var t2 = document.getElementById('text');
                if (t2 && text && currentSessionId === sessionId && !t2.value) {
                  t2.value = text; t2.dispatchEvent(new Event('input'));
                }
              }
            } finally {
              state.uploading = false;
              /* 流式阶段的按钮态由 streamReplyInto 的 syncSendBtn 接管；
                 这里只处理"流开始前就失败"（如附件上传失败）的复位 */
              if ((state.sendingCount || 0) === 0 && sendBtn) {
                sendBtn.classList.remove('loading');
                sendBtn.disabled = false;
              }
            }
          }

          /* ---------- 弹窗焦点管理（无障碍：打开聚焦、Tab 陷阱、关闭还原） ---------- */
          function focusableList(container){
            return Array.prototype.slice.call(container.querySelectorAll(
              'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
            )).filter(function(el){
              return el.type !== 'hidden' && (el.offsetParent !== null || el === document.activeElement);
            });
          }
          function trapTabIn(container, e){
            if (e.key !== 'Tab' || !container) return;
            var list = focusableList(container);
            if (!list.length){ e.preventDefault(); return; }
            var first = list[0], last = list[list.length - 1];
            if (e.shiftKey){
              if (document.activeElement === first || !container.contains(document.activeElement)){
                e.preventDefault(); last.focus();
              }
            } else if (document.activeElement === last){
              e.preventDefault(); first.focus();
            }
          }
          function restoreFocusTo(ref){
            if (ref && ref.focus && ref.isConnected){
              try { ref.focus(); } catch (e) {}
            }
          }

          /* ---------- 设置 ---------- */
          /* 模态框 HTML 在 </main> 之后，需等 DOMContentLoaded 才能绑定 */
          var settingsReturnFocus = null;
          function openSettings(){
            settingsReturnFocus = document.activeElement;
            /* 每次打开设置重置「用户是否真的改过语音引擎」：保存时据此决定是否提交
               voice_provider —— 只改别的字段时不再把 mimo/local 静默改写成 minimax */
            state.voiceProviderTouched = false;
            var el = document.getElementById('settings'); if (el) { el.classList.add('show'); refreshStatus(); refreshWeather(); refreshRoleNews(); refreshMemories(); refreshStats(); }
            setTimeout(function(){
              var c = document.querySelector('#settings .set-close');
              if (c) c.focus();
            }, 60);
            var hint = document.getElementById('server-url-hint');
            if (hint) {
              var cur = (window.AndroidBridge && window.AndroidBridge.getServerUrl) ? window.AndroidBridge.getServerUrl() : '';
              hint.textContent = cur ? cur : (window.AndroidBridge ? '未配置' : '网页版（连接本机）');
            }
          }
          function closeSettings(){
            var el = document.getElementById('settings'); if (!el) return;
            el.classList.remove('show');
            restoreFocusTo(settingsReturnFocus);
            settingsReturnFocus = null;
          }
          /* ---------- 陪伴足迹（/api/stats：纯本地聚合，零 API 成本） ---------- */
          function refreshStats(){
            var sum = document.getElementById('stats-summary');
            if (!sum) return;
            api('/api/stats').then(function(r){
              if (!sum.isConnected) return;
              var charsTxt = r.total_chars >= 10000 ? (r.total_chars / 10000).toFixed(1) + ' 万字' : r.total_chars + ' 字';
              var parts = ['累计 ' + r.total_messages + ' 条消息', charsTxt];
              if (r.total_images) parts.push(r.total_images + ' 张图片');
              if (r.total_sessions) parts.push(r.total_sessions + ' 个会话');
              sum.textContent = parts.join(' · ');
              var bars = document.getElementById('stats-bars');
              if (!bars) return;
              var days = r.daily_7d || [];
              var max = 1;
              days.forEach(function(d){ if (d.count > max) max = d.count; });
              bars.innerHTML = days.map(function(d){
                var h = d.count ? Math.max(6, Math.round(38 * d.count / max)) : 2;
                var label = (d.date || '').slice(5);
                return '<div class="stat-bar" title="' + label + ' · ' + d.count + ' 条">'
                  + '<i style="height:' + h + 'px;opacity:' + (d.count ? '0.9' : '0.25') + '"></i>'
                  + '<small>' + label + '</small></div>';
              }).join('');
            }).catch(function(){
              sum.textContent = '统计暂不可用（服务未连接）';
            });
          }
          /* ---------- 天气感知（位置取自 config.json 的 location.manual，自动定位已下线） ---------- */
          function refreshWeather(){
            var box = document.getElementById('loc-weather');
            if (!box) return;
            var span = box.querySelector('span');
            if (span) span.textContent = '天气获取中…';
            api('/api/weather').then(function(r){
              if (!box) return;
              var sp = box.querySelector('span');
              if (!sp) return;
              if (!r.enabled) { sp.textContent = '天气感知已关闭（config.json location.weather=false）'; return; }
              if (r.weather) {
                var ago = r.cached_at ? Math.round((Date.now()/1000 - r.cached_at)/60) : -1;
                var hint = (r.location ? r.location + ' · ' : '') + r.weather;
                if (ago >= 0) hint += '（' + (ago < 1 ? '刚刚' : ago + ' 分钟前') + '更新）';
                sp.textContent = hint;
              } else {
                sp.textContent = '天气暂不可用：请在 config.json 的 location.manual 配置位置，或点「天气」按钮刷新';
              }
            }).catch(function(){
              var sp = box.querySelector('span');
              if (sp) sp.textContent = '天气获取失败';
            });
          }
          function locWeatherRefresh(){
            api('/api/weather/refresh', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }
            }).then(function(r){
              toast(r && r.weather ? '天气已刷新：' + r.weather : '天气刷新失败，先确认位置已设置');
              refreshWeather();
            }).catch(function(e){ toast(e.message || '天气刷新失败'); });
          }
          /* ---------- 角色现实动态（v2：分级卡片 + 时间线） ---------- */
          var rnView = 'cards';
          var rnStatusName = { confirmed: '已确认', inferred: '推测', missing: '未知' };
          var rnSourceName = { web: '联网搜索', persona: '角色设定', memory: '记忆库', state: '情绪状态', location: '位置', weather: '天气' };
          function rnEsc(s){
            return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch){
              return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch];
            });
          }
          function rnCardHtml(c){
            var badge = '<span class="rn-badge">' + rnEsc(rnStatusName[c.status] || c.status) + '</span>';
            var reason = (c.status === 'missing' && c.reason)
              ? '<div class="rn-reason">缺失原因：' + rnEsc(c.reason) + '</div>' : '';
            if (c.status === 'inferred' && c.reason) {
              reason = '<div class="rn-reason">依据：' + rnEsc(c.reason) + '</div>';
            }
            var sug = (c.status === 'missing' && c.suggestion)
              ? '<div class="rn-reason" style="color:color-mix(in srgb,var(--chart-3) 85%,transparent)">建议：' + rnEsc(c.suggestion) + '</div>' : '';
            var src = c.source ? '<div class="rn-src">来源：' + rnEsc(rnSourceName[c.source] || c.source) + '</div>' : '';
            var content = c.status === 'missing'
              ? '<div class="rn-content">该维度暂无资料</div>'
              : '<div class="rn-content">' + rnEsc(c.content) + '</div>';
            return '<div class="rn-card st-' + rnEsc(c.status) + '">'
              + '<div class="rn-cat">' + rnEsc(c.category) + badge + '</div>'
              + content + reason + sug + src + '</div>';
          }
          function rnRender(r){
            var body = document.getElementById('rn-body');
            var tl = document.getElementById('rn-timeline');
            var summary = document.getElementById('rn-summary');
            var tabs = document.getElementById('rn-tabs');
            var sources = document.getElementById('rn-sources');
            var hint = document.getElementById('rn-missing-hint');
            if (!body) return;
            var cards = (r && r.cards) || [];
            var timeline = (r && r.timeline) || [];
            summary.textContent = (r && r.summary) ? r.summary : '暂无动态：点「搜索动态」获取（首次搜索+总结约需十几秒）';
            summary.classList.toggle('empty', !(r && r.summary));
            tabs.style.display = (cards.length || timeline.length) ? 'flex' : 'none';
            /* 卡片视图 */
            body.innerHTML = '';
            if (cards.length) {
              cards.forEach(function(c){ body.insertAdjacentHTML('beforeend', rnCardHtml(c)); });
            } else if (!(r && r.summary)) {
              body.insertAdjacentHTML('beforeend',
                '<div class="rn-card st-missing"><div class="rn-cat">当前处境<span class="rn-badge">未知</span></div>'
                + '<div class="rn-content">该角色未配置新闻关键词（roles.*.news.keyword），跳过</div></div>');
            }
            /* 时间线视图 */
            tl.innerHTML = '';
            (timeline.length ? timeline : cards.filter(function(c){ return c.status !== 'missing'; })).forEach(function(it){
              var date = it.date || '';
              var label = date ? '<span class="rn-tl-date">' + rnEsc(date) + '</span>' : '';
              var cat = it.category ? '<span class="rn-tl-cat">' + rnEsc(it.category) + '</span>' : '';
              var badge = '<span class="rn-badge" style="font-size:10px;font-weight:700;padding:1px 7px;border-radius:99px;margin-left:6px;color:color-mix(in srgb,var(--muted-foreground) 82%,transparent);background:color-mix(in srgb,var(--muted-foreground) 12%,transparent)">'
                + rnEsc(rnStatusName[it.status] || it.status) + '</span>';
              tl.insertAdjacentHTML('beforeend',
                '<div class="rn-tl-item st-' + rnEsc(it.status || 'confirmed') + '">'
                + '<div>' + label + cat + badge + '</div>'
                + '<div class="rn-tl-content">' + rnEsc(it.content) + '</div></div>');
            });
            /* 视图切换 */
            body.style.display = rnView === 'cards' ? 'flex' : 'none';
            tl.style.display = rnView === 'timeline' ? 'flex' : 'none';
            tabs.querySelectorAll('.rn-tab').forEach(function(t){
              t.classList.toggle('active', t.dataset.rnView === rnView);
            });
            /* 来源状态 */
            sources.innerHTML = '';
            var srcs = (r && r.sources) || {};
            Object.keys(srcs).forEach(function(k){
              var ok = srcs[k] === 'ok';
              sources.insertAdjacentHTML('beforeend',
                '<span class="rn-src-chip ' + (ok ? 'ok' : 'empty') + '">'
                + rnEsc(rnSourceName[k] || k) + (ok ? ' ✓' : ' 缺') + '</span>');
            });
            /* 缺失提示（含补充入口建议） */
            var missing = cards.filter(function(c){ return c.status === 'missing' && c.reason; });
            if (missing.length) {
              hint.style.display = 'block';
              var reasons = missing.map(function(c){
                return '<b>' + rnEsc(c.category) + '</b>：' + rnEsc(c.reason)
                  + (c.suggestion ? '（' + rnEsc(c.suggestion) + '）' : '');
              }).join('<br>');
              hint.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16h14v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1Z"/></svg>资料待补充：' + reasons
                + '<br>可在 config.json 的 roles.*.news.keyword / location.manual 中补充关键词与位置，'
                + '或直接在对话中告诉角色最新动态（角色会以你说的为准）。';
            } else {
              hint.style.display = 'none';
            }
          }
          function refreshRoleNews(){
            var span = document.getElementById('role-news-status');
            if (!span) return;
            api('/api/role-news').then(function(r){
              if (!r) return;
              if (!r.enabled) { span.textContent = '该角色未配置新闻关键词（roles.*.news.keyword），跳过'; }
              else if (r.cached_at) {
                var ago = Math.round((Date.now()/1000 - r.cached_at)/60);
                span.textContent = ago < 1 ? '刚刚更新' : ago + ' 分钟前更新';
              } else {
                span.textContent = '尚未获取';
              }
              rnRender(r);
            }).catch(function(){ span.textContent = '动态获取失败'; rnRender(null); });
          }
          function roleNewsRefresh(){
            var span = document.getElementById('role-news-status');
            if (span) span.textContent = '搜索中（约十几秒）…';
            api('/api/role-news/refresh', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }
            }).then(function(r){
              var hasCards = r && r.cards && r.cards.some(function(c){ return c.status !== 'missing'; });
              toast(hasCards ? '已获取最新动态' : '本次搜索未找到新资料，已记录缺失原因，可稍后再试');
              refreshRoleNews();
            }).catch(function(e){ toast(e.message || '搜索失败'); refreshRoleNews(); });
          }
          function initSettings() {
            var settingsEl = document.getElementById('settings');
            if (!settingsEl) return;
            var serverBtn = document.getElementById('btn-server-settings');
            if (serverBtn) serverBtn.addEventListener('click', function(){
              if (window.AndroidBridge && window.AndroidBridge.openSettings) {
                window.AndroidBridge.openSettings();   // APK：打开原生服务器设置页
              } else {
                toast('网页版直连本机服务，无需修改；如需改地址请直接编辑 config.json');
              }
            });
            var closeBtn = settingsEl.querySelector('[data-close]');
            if (closeBtn) closeBtn.addEventListener('click', closeSettings);
            /* Tab 焦点陷阱：限定在设置面板内循环 */
            var setPanelEl = settingsEl.querySelector('.set-panel');
            settingsEl.addEventListener('keydown', function(e){ trapTabIn(setPanelEl, e); });
            settingsEl.addEventListener('click', function(e){ if (e.target === settingsEl) closeSettings(); });
            settingsEl.querySelectorAll('[data-key-input]').forEach(function(btn){
              btn.addEventListener('click', function(){
                var inp = document.getElementById(btn.dataset.keyInput);
                if (!inp) return;
                var hide = inp.type === 'text';
                inp.type = hide ? 'password' : 'text';
                btn.textContent = hide ? '显示' : '隐藏';
              });
            });
            var locWeatherBtn = document.getElementById('loc-weather-btn');
            if (locWeatherBtn) locWeatherBtn.addEventListener('click', locWeatherRefresh);
            var roleNewsBtn = document.getElementById('role-news-btn');
            if (roleNewsBtn) roleNewsBtn.addEventListener('click', roleNewsRefresh);
            document.querySelectorAll('#rn-tabs .rn-tab').forEach(function(t){
              t.addEventListener('click', function(){
                rnView = t.dataset.rnView === 'timeline' ? 'timeline' : 'cards';
                var body = document.getElementById('rn-body');
                var tl = document.getElementById('rn-timeline');
                if (body) body.style.display = rnView === 'cards' ? 'flex' : 'none';
                if (tl) tl.style.display = rnView === 'timeline' ? 'flex' : 'none';
                document.querySelectorAll('#rn-tabs .rn-tab').forEach(function(x){
                  x.classList.toggle('active', x === t);
                });
              });
            });
            var saveBtn = document.getElementById('save-settings');
            if (saveBtn) saveBtn.addEventListener('click', async function(){
              try {
                await api('/api/config', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    /* 本地 LLM 引擎已从界面下线（后端能力保留）：固定走云端 */
                    provider: 'cloud',
                    cloud_provider: document.getElementById('cloud_provider_sel').value,
                    cloud_base_url: document.getElementById('cloud_base_url').value,
                    cloud_api_key: document.getElementById('cloud_api_key').value,
                    cloud_model: document.getElementById('cloud_model').value,
                    cloud_thinking: document.getElementById('cloud_thinking').checked,
                    /* 计费模式仅 MiniMax 供应商生效：其他供应商提交空串，避免污染其条目 */
                    cloud_billing_mode: (document.getElementById('cloud_provider_sel').value === 'minimax')
                      ? document.getElementById('cloud_billing_mode').value : '',
                    persona: document.getElementById('persona').value,
                    /* voice_provider 只在用户本次真的点过语音 tab 时提交：
                       #voice-tabs 只有 aliyun/minimax 两个 tab，而 config 里的 provider
                       可能是 mimo/local。旧写法直接取 .active 的 dataset.voice 提交，
                       于是"打开设置只改个字号然后保存"就会把 MiMo/本地引擎静默改写成
                       MiniMax，并强制 manual_provider=true 覆盖角色级音色 —— 用户没做错
                       任何事却丢了配置（后端 server.py:5968 的注释就是这个坑的现场记录）。
                       提交空串时后端 `upd.voice_provider in (...)` 直接跳过 = 保持原值。 */
                    voice_provider: state.voiceProviderTouched
                      ? document.querySelector('#voice-tabs .set-tab.active').dataset.voice : '',
                    aliyun_api_key: document.getElementById('aliyun_api_key').value,
                    aliyun_base_url: document.getElementById('aliyun_base_url').value,
                    aliyun_model: document.getElementById('aliyun_model').value,
                    aliyun_voice: document.getElementById('aliyun_voice').value,
                    minimax_api_key: document.getElementById('minimax_api_key').value,
                    minimax_base_url: document.getElementById('minimax_base_url').value,
                    minimax_model: document.getElementById('minimax_model').value,
                    minimax_voice: document.getElementById('minimax_voice').value,
                    minimax_speed: document.getElementById('minimax_speed').value,
                    minimax_vol: document.getElementById('minimax_vol').value,
                    minimax_pitch: document.getElementById('minimax_pitch').value,
                    minimax_sample_rate: document.getElementById('minimax_sample_rate').value,
                  }),
                });
                var gt = document.getElementById('greeting_toggle');
                /* 存储被禁用时不应误报"设置保存失败"——服务端此时已保存成功 */
                if (gt) { try { localStorage.setItem('xiaoni_greeting_enabled', gt.checked ? '1' : '0'); } catch (e) {} }
                toast('设置已保存'); closeSettings(); refreshStatus();
              } catch (e) { toast(e.message); }
            });
            var cpSel = document.getElementById('cloud_provider_sel');
            if (cpSel) cpSel.addEventListener('change', function(){
              var p = state.cloudProviders[this.value];
              if (!p) return;
              document.getElementById('cloud_base_url').value = p.base_url || '';
              document.getElementById('cloud_model').value = p.model || '';
              document.getElementById('cloud_api_key').value = p.api_key || '';
              var ct = document.getElementById('cloud_thinking');
              if (ct) ct.checked = p.thinking !== false;
              var bmSel = document.getElementById('cloud_billing_mode');
              var bmRow = document.getElementById('cloud-billing-row');
              if (bmSel && bmRow) {
                bmSel.value = p.billing_mode || 'payg';
                bmRow.style.display = (this.value === 'minimax') ? '' : 'none';
              }
            });
            var modelsBtn = document.getElementById('cloud-models-btn');
            if (modelsBtn) modelsBtn.addEventListener('click', async function(){
              modelsBtn.disabled = true;
              modelsBtn.textContent = '获取中…';
              try {
                /* 掩码值（***+尾4）按空串发：后端识别为空即复用已存密钥（与保存设置的掩码规则一致），
                   直接发掩码会被供应商当成真 key 拒掉（502） */
                var _mk = document.getElementById('cloud_api_key').value || '';
                if (_mk.indexOf('***') === 0) _mk = '';
                var r = await api('/api/llm-models', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    base_url: document.getElementById('cloud_base_url').value,
                    api_key: _mk,
                  }),
                });
                var sel = document.getElementById('cloud-models-sel');
                sel.innerHTML = '';
                (r.models || []).forEach(function(m){
                  var o = document.createElement('option'); o.value = m; o.textContent = m;
                  sel.appendChild(o);
                });
                if (r.models && r.models.length) {
                  sel.style.display = '';
                  // 当前配置的模型若已下架/不在列表，自动选中第一个可用模型
                  sel.value = r.models.indexOf(document.getElementById('cloud_model').value) >= 0
                    ? document.getElementById('cloud_model').value : r.models[0];
                  toast('找到 ' + r.models.length + ' 个模型，请选择');
                } else {
                  toast('该接口未返回可用模型');
                }
              } catch (e) { toast('获取失败：' + e.message); }
              modelsBtn.disabled = false;
              modelsBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>获取模型列表';
            });
            var modelsSel = document.getElementById('cloud-models-sel');
            if (modelsSel) modelsSel.addEventListener('change', function(){
              document.getElementById('cloud_model').value = this.value;
            });
            document.querySelectorAll('#voice-tabs .set-tab').forEach(function(b){
              b.addEventListener('click', function(){
                /* 只有用户真的点了 tab 才算改过语音引擎；保存时据此决定是否提交
                   voice_provider（未点过 = 提交空串保持原值，见保存处的长注释） */
                state.voiceProviderTouched = true;
                pickVoiceProvider(b.dataset.voice);
              });
            });
            /* ---------- 自动朗读开关（与顶栏语音 chip 同一持久化键） ---------- */
            var soundToggle = document.getElementById('sound_toggle');
            if (soundToggle) soundToggle.addEventListener('change', function(){
              state.soundOn = this.checked;
              try { localStorage.setItem('xiaoni_sound_on', state.soundOn ? '1' : '0'); } catch (e) {}
              syncVoiceChipUI();
              toast(state.soundOn ? '自动朗读已开启' : '自动朗读已关闭（仍可手动点喇叭朗读）');
            });
            /* ---------- 朗读语速（原顶栏控件移入设置） ---------- */
            var speedSel = document.getElementById('tts-speed');
            if (speedSel) speedSel.addEventListener('change', function(){
              state.speed = parseFloat(this.value) || 1.0;
              try { localStorage.setItem('xiaoni_speed', String(state.speed)); } catch (e) {}
            });
            /* ---------- 角色记忆（原 roles 页能力并入） ---------- */
            var memClear = document.getElementById('btn-mem-clear');
            if (memClear) memClear.addEventListener('click', async function(){
              var ok = await confirmDialog('确定清空当前角色的全部记忆与状态吗？此操作不可恢复。', {
                title: '清空记忆',
                okText: '清空',
              });
              if (!ok) return;
              try {
                await api('/api/state', { method: 'DELETE' });
                toast('已清空记忆与状态');
                refreshMemories();
              } catch (e) { toast(e.message || '清空失败'); }
            });
          }

          /* ---------- 角色记忆列表（设置面板内，原 roles 页能力） ---------- */
          async function refreshMemories() {
            var box = document.getElementById('mem-list');
            var cnt = document.getElementById('mem-count');
            if (!box) return;
            box.innerHTML = '';
            try {
              var st = await api('/api/state');
              if (cnt) {
                cnt.textContent = (st && st.enabled)
                  ? ('长期记忆 ' + (st.memory_count != null ? st.memory_count : 0) + ' 条，最近 10 条')
                  : '角色引擎未启用';
              }
              var data = await api('/api/roles/memories?top=10');
              var items = (data && data.enabled && data.memories) || [];
              if (!items.length) {
                box.innerHTML = '<div style="color:var(--muted-foreground);line-height:1.6">还没有记忆，多聊几句就有了</div>';
                return;
              }
              items.forEach(function(m){
                var it = document.createElement('div');
                it.style.cssText = 'border:1px solid var(--border);border-radius:8px;padding:6px 9px;line-height:1.6;color:var(--foreground);word-break:break-word';
                it.textContent = m.text || '';
                box.appendChild(it);
              });
            } catch (e) {
              if (cnt) cnt.textContent = '记忆加载失败';
            }
          }

          /* ---------- 事件 ---------- */
          var sendBtn = document.querySelector('.send-btn');
          if (sendBtn) sendBtn.addEventListener('click', function(){
            /* 生成中点按 = 停止生成；空闲点按 = 发送（清空输入框收归 send() 内，避免拒收丢字） */
            if ((state.sendingCount || 0) > 0) { abortChatInFlight(); return; }
            var t = document.getElementById('text');
            send(t ? t.value : '');
          });
          if (ta) ta.addEventListener('keydown', function(e){
            /* 中文输入法合成期按 Enter 是"确认候选词"不是发送：isComposing 与
               keyCode 229 双守卫（部分 IME 只暴露其中一种信号） */
            if (e.isComposing || e.keyCode === 229) return;
            /* 生成中 Enter 不发送也不停止（防误触吞字/误停）；停止请点发送按钮 */
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if ((state.sendingCount || 0) > 0) return; if (sendBtn) sendBtn.click(); }
            /* Esc：收起输入焦点（全局 Esc 处理器随后运行，无浮层时无副作用） */
            else if (e.key === 'Escape') { ta.blur(); }
          });
          /* 粘贴板文件（截图/复制的图片等）直接进待发送附件；
             不 preventDefault，文本内容照常粘贴 */
          if (ta) ta.addEventListener('paste', function(e){
            var items = e.clipboardData && e.clipboardData.items;
            if (!items) return;
            var files = [];
            for (var i = 0; i < items.length; i++) {
              if (items[i].kind === 'file') {
                var f = items[i].getAsFile();
                if (f) files.push(f);
              }
            }
            if (!files.length) return;
            var pad = function(n){ return (n < 10 ? '0' : '') + n; };
            files.forEach(function(f){
              /* 截图通常无名（image.png），给个可读的唯一名 */
              var ext = ((f.type || '').split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
              var d = new Date();
              var name = '粘贴-' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
                         '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()) + '.' + ext;
              try { addPendingAttachment(new File([f], name, { type: f.type })); }
              catch (err) { addPendingAttachment(f); }
            });
            toast('已添加 ' + files.length + ' 个粘贴的文件');
          });
          if (attachBtn && attachMenu) {
            attachBtn.addEventListener('click', function(e){ e.stopPropagation(); toggleAttachMenu(); });
            attachMenu.addEventListener('click', function(e){ e.stopPropagation(); });
            attachMenu.querySelectorAll('[data-attach-kind]').forEach(function(b){
              b.addEventListener('click', function(){ openAttachPicker(b.dataset.attachKind); });
            });
            if (attachInput) attachInput.addEventListener('change', function(){
              Array.prototype.forEach.call(attachInput.files || [], function(file){ addPendingAttachment(file); });
              toggleAttachMenu(false);
              attachInput.value = '';
            });
            document.addEventListener('click', function(){ toggleAttachMenu(false); });
            /* 滚动/缩放时收起附件菜单：悬空菜单不再钉在旧位置 */
            document.addEventListener('scroll', function(){ toggleAttachMenu(false); }, true);
            window.addEventListener('resize', function(){ toggleAttachMenu(false); });
          }
          var settingsBtn = document.querySelector('[data-dom-id="btn-settings"]');
          if (settingsBtn) settingsBtn.addEventListener('click', openSettings);
          if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSettings);
          else initSettings();

          /* 欢迎提示词 chips 由 renderPromptChips 动态渲染并绑定（按当前角色） */

          /* ---------- 新对话 / 会话列表 ---------- */
          var sessionsBox = document.querySelector('.sessions');
          var SESSIONS_KEY = 'xiaoni_sessions_v1';
          var ACTIVE_KEY = 'xiaoni_active_session_v1';
          var CLIENT_ID_KEY = 'xiaoni_client_id';
          var TOMBSTONE_KEY = 'xiaoni_tombstones_v1';
          /* 会话搜索关键词（侧栏搜索框写入，renderSessions 读取过滤） */
          var sessionSearchQuery = '';
          /* 设备 id：多端同步时服务端广播跳过来源自身，避免自己保存触发自己重拉 */
          var CLIENT_ID = (function(){
            var id = '';
            try { id = localStorage.getItem(CLIENT_ID_KEY) || ''; } catch (e) {}
            if (!id) {
              id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
              try { localStorage.setItem(CLIENT_ID_KEY, id); } catch (e) {}
            }
            return id;
          })();
          /* 删除墓碑：某端删除会话后，其他端合并时不得将其"复活"。
             [{id, ts}] 随 saveSessions 上传，服务端持久化于 sessions.json 的 deleted 字段 */
          function loadTombstones() {
            try {
              var arr = JSON.parse(localStorage.getItem(TOMBSTONE_KEY) || '[]');
              return Array.isArray(arr) ? arr : [];
            } catch (e) { return []; }
          }
          function saveTombstones(list) {
            /* 上限与服务端 _TOMBSTONE_MAX_COUNT 一致；墓碑不过期，只为兜住病态增长 */
            try { localStorage.setItem(TOMBSTONE_KEY, JSON.stringify(list.slice(-2000))); } catch (e) {}
          }
          function addTombstone(id) {
            if (!id) return;
            var list = loadTombstones().filter(function(t){ return t && t.id !== id; });
            list.push({ id: id, ts: Date.now() });
            saveTombstones(list);
          }
          function makeSession(title) {
            return {
              id: 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
              title: title || '新对话',
              updatedAt: Date.now(),
              history: [],
              pinned: false,       /* 置顶会话：列表优先展示 */
              manualTitle: false,  /* 手动重命名后不再被首条消息自动覆盖标题 */
              role: state.activeRole || ''  /* 会话归属角色：2027 分支会话只属于自己，旧角色互不隔离（保持原行为） */
            };
          }
          function normalizeSession(s) {
            return {
              id: s.id || makeSession(s.title).id,
              title: s.title || '新对话',
              updatedAt: s.updatedAt || Date.now(),
              history: Array.isArray(s.history) ? s.history : [],
              pinned: !!s.pinned,
              manualTitle: !!s.manualTitle,
              role: s.role || ''
            };
          }
          /* 旧版曾用 seed-* 假会话填充首次体验，会随自动同步写入服务端形成垃圾记录，一律过滤 */
          function isSeedSession(x) {
            return /^seed-\d+$/.test((x && x.id) || '');
          }
          /* 测试脚本/验证脚本写入的会话（id 以 t- 开头）：服务端 GET 已过滤，前端再兜底一层，
             即使本地缓存里混入也绝不显示、不参与合并 */
          function isTestSession(x) {
            return /^t-/.test((x && x.id) || '');
          }
          /* 空占位会话（与服务端 _is_placeholder_session 同语义）：
             无消息、默认标题、未置顶未改名 —— 不渲染进列表，避免淹没会话栏 */
          function isPlaceholderSession(s) {
            if (!s) return false;
            if (s.history && s.history.length) return false;
            if (s.pinned || s.manualTitle) return false;
            return !s.title || s.title === '新对话';
          }
          function loadSessions() {
            var list = [];
            try {
              var raw = localStorage.getItem(SESSIONS_KEY);
              if (raw) {
                var arr = JSON.parse(raw);
                if (Array.isArray(arr)) list = arr;
              }
            } catch (e) {}
            list = list.map(normalizeSession).filter(function(s){ return !isSeedSession(s) && !isTestSession(s) && !isPlaceholderSession(s); });
            /* 主数据丢失/损坏但快照还在：从快照恢复，避免打开即空白（已删会话由墓碑在同步时继续过滤，不会复活）。
               注意：此处用字面量 key——SYNC_BAK_KEY 的赋值在 loadSessions 首次执行之后才跑（var 提升但赋值不提升）。 */
            if (!list.length) {
              try {
                var bak = localStorage.getItem('xiaoni_sessions_bak');
                if (bak) {
                  var arr = JSON.parse(bak);
                  if (Array.isArray(arr) && arr.length) {
                    var restored = arr.map(normalizeSession).filter(function(s){ return !isSeedSession(s) && !isTestSession(s) && !isPlaceholderSession(s); });
                    if (restored.length) { list = restored; window.__xiaoniRestored = true; }
                  }
                }
              } catch (e) {}
            }
            /* 存储为空或只剩垃圾记录时，自动建一个干净的新对话，保证页面始终有当前会话 */
            if (!list.length) list.push(makeSession('新对话'));
            return list;
          }
          var sessions = loadSessions();
          var currentSessionId = sessions.length ? sessions[0].id : null;
          var saveTimer = null;
          function persistLocal() {
            /* 返回 true/false：Safari 隐私模式配额为 0、配额写满时 setItem 会抛异常，
               旧实现静默吞掉，用户误以为已保存，刷新后记录"消失" */
            try {
              var payload = JSON.stringify(sessions);
              localStorage.setItem(SESSIONS_KEY, payload);
              /* 回读校验：写完读回对不上同样视为失败 */
              if (localStorage.getItem(SESSIONS_KEY) !== payload) throw new Error('回读不一致');
              localStorage.setItem(ACTIVE_KEY, currentSessionId || '');
              return true;
            } catch (e) {
              return false;
            }
          }
          /* ---------- 同步状态机 + 自动重试 + 本地快照兜底 ----------
             状态：unsynced（从未同步）/ saving（同步中）/ ok（已同步）/
                   error（失败，带原因）/ offline（断网）
             state.dirty：有未落盘到服务端的变更。失败/离线时保持 true，
             由 15s 轮询 + online 事件 + 切回前台自动补推，不再依赖用户手动重试。
             上次同步时间持久化（SYNC_KEY），刷新后不回到"未同步"。 */
          var SYNC_KEY = 'xiaoni_last_sync';
          var SYNC_BAK_KEY = 'xiaoni_sessions_bak';
          var SYNC_BAK_AT = 'xiaoni_sessions_bak_at';
          var SYNC_BAK_MAX = 2500000;  /* 快照超 2.5MB 不写备份，防 localStorage 配额爆炸 */
          state.syncState = state.syncState || 'unsynced';
          state.dirty = false;
          state.saving = false;
          (function initLastSync(){
            try {
              var raw = localStorage.getItem(SYNC_KEY);
              if (raw) {
                var o = JSON.parse(raw);
                if (o && o.at) {
                  state.lastSync = o;
                  state.syncState = (o.ok === false) ? 'error' : 'ok';
                }
              }
            } catch (e) {}
          })();
          function isOnlineNow() {
            try {
              if (typeof navigator !== 'undefined' && typeof navigator.onLine === 'boolean') return navigator.onLine;
            } catch (e) {}
            return true;
          }
          function setSyncState(s, err) {
            state.syncState = s;
            if (s === 'ok') state.lastSync = { ok: true, at: Date.now(), err: '' };
            else if (s === 'error') state.lastSync = { ok: false, at: Date.now(), err: err || '同步失败' };
            if (s === 'ok' || s === 'error') {
              try { localStorage.setItem(SYNC_KEY, JSON.stringify(state.lastSync)); } catch (e) {}
            }
            renderSyncStatus();
          }
          /* 侧栏状态行 + 顶栏指示：同一状态，两处一起刷 */
          function renderSyncStatus() {
            var el = document.getElementById('sync-status');
            var chip = document.getElementById('sync-chip');
            var chipText = document.getElementById('sync-chip-text');
            var st = state.syncState || 'unsynced';
            var last = state.lastSync;
            function hm(ms) {
              var t = new Date(ms);
              var pad = function(n){ return (n < 10 ? '0' : '') + n; };
              return pad(t.getHours()) + ':' + pad(t.getMinutes()) + ':' + pad(t.getSeconds());
            }
            function paint(target, cls, txt, title) {
              if (!target) return;
              target.classList.remove('ok', 'err', 'saving');
              if (cls) target.classList.add(cls);
              if (txt !== undefined && 'textContent' in target) target.textContent = txt;
              if (title !== undefined) target.title = title;
            }
            if (st === 'saving') {
              paint(el, 'saving', '同步中…', '正在保存到服务端…');
              paint(chip, 'saving', undefined, '正在保存到服务端…');
              if (chipText) chipText.textContent = '同步中';
            } else if (st === 'offline') {
              var lastTxt = (last && last.at) ? ' · 上次 ' + hm(last.at) : '';
              paint(el, 'err', '离线 · 未同步' + lastTxt, '网络已断开，恢复后自动补推（点击立即重试）');
              paint(chip, 'err', undefined, '网络已断开，恢复后自动补推（点击立即重试）');
              if (chipText) chipText.textContent = '离线';
            } else if (st === 'ok' && last) {
              paint(el, 'ok', '已同步 · ' + hm(last.at), '本地与服务端已同步，点击立即再同步一次');
              paint(chip, 'ok', undefined, '已同步 · ' + hm(last.at) + '（点击立即再同步一次）');
              if (chipText) chipText.textContent = '已同步';
            } else if (st === 'error' && last) {
              paint(el, 'err', '同步失败 · ' + hm(last.at), (last.err || '同步失败') + '（点击重试）');
              paint(chip, 'err', undefined, '同步失败 · ' + hm(last.at) + '：' + (last.err || '') + '（点击重试）');
              if (chipText) chipText.textContent = '同步失败';
            } else {
              paint(el, false, '未同步', '尚未与服务端同步，点击重试');
              paint(chip, false, undefined, '尚未与服务端同步，点击重试');
              if (chipText) chipText.textContent = '未同步';
            }
          }
          /* 成功后节流写快照（最多 1 分钟一次）：主数据丢失/损坏时 loadSessions 可从快照恢复 */
          var _bakAt = 0;
          function writeBackup() {
            try {
              var now = Date.now();
              if (now - _bakAt < 60000) return;
              var payload = JSON.stringify(sessions);
              if (!payload || payload.length < 10 || payload.length > SYNC_BAK_MAX) return;
              localStorage.setItem(SYNC_BAK_KEY, payload);
              localStorage.setItem(SYNC_BAK_AT, String(now));
              _bakAt = now;
            } catch (e) {
              try { console.warn('[xiaoni] 会话快照备份失败：', (e && e.message) || e); } catch (_) {}
            }
          }
          /* keepalive 只用于关页刷盘：Chrome 对 keepalive 请求体限 64KB（按编码后字节计，
             中文多 3 倍体积，超限直接 "Failed to fetch" 且不发包），日常保存绝不能带它 */
          function saveSessions(immediate, keepalive) {
            state.dirty = true;
            state.saveSeq = (state.saveSeq || 0) + 1;
            var localOk = persistLocal();
            if (!localOk && (!saveSessions._lastLocalErrAt || Date.now() - saveSessions._lastLocalErrAt > 10000)) {
              saveSessions._lastLocalErrAt = Date.now();
              try {
                console.error('[xiaoni] 本机保存失败：localStorage 写入或回读校验未通过');
                toast('本机保存失败：浏览器存储不可用，刷新后新消息可能丢失');
              } catch (_) {}
            }
            clearTimeout(saveTimer);
            var doSave = function(){
              /* 在途守卫：已有 PUT 在飞时不并发第二个（旧快照 PUT 会与新快照互相覆盖、
                 加大服务端合并压力）；本次变更留在 dirty，由下方响应回调/15s 轮询补推 */
              if (state.saving) { return; }
              if (!isOnlineNow()) { setSyncState('offline'); return; }
              var seq = state.saveSeq;
              var body;
              try {
                body = JSON.stringify({ sessions: sessions, deleted: loadTombstones() });
              } catch (e) {
                /* 序列化失败必须可见：旧实现在这里被外层 catch 静默吞掉，
                   localStorage 与服务端同时写不进、又没有任何提示 */
                setSyncState('error', '序列化失败：' + ((e && e.message) || '未知错误'));
                try {
                  console.error('[xiaoni] 会话序列化失败：', e);
                  toast('记录保存失败：数据异常无法序列化（' + ((e && e.message) || '未知错误') + '）');
                } catch (_) {}
                return;
              }
              state.saving = true;
              state.dirty = false;
              setSyncState('saving');
              /* 这次 PUT 必须有超时兜底：它没有别的地方会复位 state.saving，
                 而入口第一行就是 `if (state.saving) return`。半开连接（ngrok 抖动、
                 移动网切基站、后端重启未发 RST）会让 fetch 几分钟不 settle，于是
                 「之后每一次保存都早退」——防抖保存、15s 补推、点状态行手动重试
                 全部走同一条早退路径，表现为永久「同步中…」+ 点击无反应，
                 只有刷新页面才能恢复。超时按失败处理，交给既有补推机制自愈。 */
              var putCtl = null, putTimedOut = false, putTimer = 0;
              try { putCtl = new AbortController(); } catch (e2) { putCtl = null; }
              if (putCtl) putTimer = setTimeout(function(){ putTimedOut = true; try { putCtl.abort(); } catch (e3) {} }, 20000);
              var clearPutTimer = function(){ if (putTimer) { clearTimeout(putTimer); putTimer = 0; } };
              try {
                fetch('/api/sessions?client=' + encodeURIComponent(CLIENT_ID), {
                  method: 'PUT', headers: { 'Content-Type': 'application/json' },
                  body: body,
                  keepalive: keepalive === true, /* 仅关页刷盘时 true（页面存活时发包），平时 false */
                  signal: putCtl ? putCtl.signal : undefined,
                }).then(function(r){
                    clearPutTimer();
                    state.saving = false;
                    if (r && r.status === 401) { location.href = '/login'; return; }
                    if (r && !r.ok) throw new Error('HTTP ' + r.status);
                    /* 记录服务端写入后的内容指纹：下一次周期轮询命中即免全量拉取 */
                    try {
                      r.json().then(function(d){ if (d && d.fp) _lastSessionsFp = d.fp; }).catch(function(){});
                    } catch (_e) {}
                    /* 飞行期间又产生了新变更（saveSeq 已自增）：本次 PUT 带的是旧快照，
                       不能清 dirty，立即补推一次最新数据 */
                    if (state.saveSeq !== seq) { state.dirty = true; saveSessions(); }
                    setSyncState('ok');
                    writeBackup();
                }).catch(function(e){
                    /* 保存失败必须可见：服务挂了/网络断时若静默吞错，用户会误以为
                       已保存，重开页面/换设备后聊天记录就"消失"了。限频提示避免刷屏。 */
                    clearPutTimer();
                    state.saving = false;
                    state.dirty = true;  /* 发送前清过 dirty：失败必须恢复，15s 轮询才会补推 */
                    var emsg = putTimedOut ? '请求超时（20s 无响应）' : ((e && e.message) || '网络异常');
                    setSyncState('error', emsg);
                    if (!saveSessions._lastErrAt || Date.now() - saveSessions._lastErrAt > 10000) {
                      saveSessions._lastErrAt = Date.now();
                      try { toast('记录保存失败，仅存本机：' + emsg); } catch (_) {}
                    }
                });
              } catch (e) { clearPutTimer(); state.saving = false; state.dirty = true; }
            };
            if (immediate) doSave();
            else saveTimer = setTimeout(doSave, 300);
          }
          /* 脏数据自动补推：失败/离线的变更每 15s 重试一次（页面可见时），直到落盘 */
          setInterval(function(){
            try {
              if (state.dirty && !state.saving && isOnlineNow() && !document.hidden) saveSessions(true);
            } catch (e) {}
          }, 15000);
          window.addEventListener('online', function(){
            renderSyncStatus();
            if (state.dirty) saveSessions(true);
            /* 网络恢复（如 ngrok 重启后隧道换了域名）：refreshStatus 的失败重试
               计数已到上限不再自试，"后端未连接"会一直挂在界面上，这里清零重探 */
            _statusRetry = 0;
            refreshStatus();
            toast('网络已恢复');
          });
          window.addEventListener('offline', function(){
            state.saving = false; setSyncState('offline');
            toast('网络已断开：变更会暂存本机，恢复后自动补推');
          });
          /* 关页/刷新前兜底刷盘，避免 300ms 防抖窗口内的消息丢失。
             唯一使用 keepalive 的地方：页面存活时的请求一律不用（超 64KB 直接发不出去） */
          window.addEventListener('pagehide', function(){ saveSessions(true, true); saveDraft(true); });
          function findSession(id) {
            for (var i = 0; i < sessions.length; i++) {
              if (sessions[i].id === id) return sessions[i];
            }
            return null;
          }
          function currentSession() {
            var s = findSession(currentSessionId);
            if (s) return s;
            /* 当前 id 已失效（会话被删/合并丢失）：修回主对话并同步选中态，
               否则后续发送静默失败、侧栏无高亮（"点不开"的根源之一） */
            var main = ensureMainSession();
            currentSessionId = main.id;
            persistLocal();
            renderSessions();
            return main;
          }
          /* 会话列表排序：置顶优先，组内按最近活跃时间倒序。
             渲染、删除后选会话、服务端合并三处共用，保证顺序一致 */
          function sortSessions(arr) {
            return arr.slice().sort(function(a, b){
              var pa = a.pinned ? 1 : 0, pb = b.pinned ? 1 : 0;
              if (pa !== pb) return pb - pa;
              return (b.updatedAt || 0) - (a.updatedAt || 0);
            });
          }
          function timeLabel(ts) {
            var diff = Date.now() - (ts || Date.now());
            if (diff < 60 * 1000) return '刚刚';
            if (diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + ' 分钟前';
            if (diff < 24 * 60 * 60 * 1000) return Math.floor(diff / 3600000) + ' 小时前';
            if (diff < 48 * 60 * 60 * 1000) return '昨天';
            return Math.floor(diff / 86400000) + ' 天前';
          }
          function updateChatTitle() {
            var s = currentSession();
            var el = document.querySelector('.chat-title-text');
            if (el && s) el.textContent = s.title;
          }
          function markSessionActivity(id, firstUserText, silent) {
            var s = findSession(id);
            if (!s) return;
            if (firstUserText && s.history.length === 1 && !s.manualTitle) {
              s.title = firstUserText.length > 12 ? firstUserText.slice(0, 12) + '…' : firstUserText;
            }
            if (!silent) {
              /* silent：仅持久化附属数据（如 TTS 音频 URL），不刷新活跃时间、
                 不重排列表——否则在旧会话里点一次朗读就把它顶到侧栏最前 */
              s.updatedAt = Date.now();
            }
            saveSessions(); /* 防抖：连续收发消息时不要每条都 PUT 服务端 */
            if (!silent) {
              renderSessions();
              if (id === currentSessionId) updateChatTitle();
            }
          }
          /* 轻量指纹：同步合并的变更检测用，避免每次同步全量深比较（会话可能有数万字） */
          function sessionFingerprint(s) {
            var last = (s.history && s.history.length) ? s.history[s.history.length - 1] : null;
            return [s.updatedAt || 0, s.history ? s.history.length : 0, s.title || '',
              s.pinned ? 1 : 0, s.manualTitle ? 1 : 0,
              last ? ((last.content || '').length + '|' + (last.role || '') + '|' + ((last.attachments || []).length)) : '-'].join('~');
          }
          /* 消息指纹：role+content+style+附件。同一文本配不同图片/附件
             不得视为同一条（旧实现只比 role+content，同文不同图的并发合并会丢消息）。
             注意 audio（TTS 持久化 URL）故意不进指纹：它是任一端事后独立写入的
             易变元数据，两端同一条消息一方带 audio 一方不带时若视为不同，
             分歧合并会把"同一条"追加两次（相邻双胞胎 bug）。与后端 _msg_key 同规则。 */
          function msgSig(m) {
            if (!m) return '';
            var atts = '';
            try {
              atts = (m.attachments || []).map(function(a){ return (a.name || '') + ':' + (a.size || 0); }).join(',');
            } catch (e) {}
            return (m.role || '') + ' ' + (m.content || '') + ' ' + (m.style || '') + ' ' + atts;
          }
          /* 竞态防御：shorter 是否严格是 longer 的消息序列前缀（逐条指纹一致）。
             多端同会话并发写/时钟偏移时，持旧快照的一端 updatedAt 可能更新但消息更少，
             若允许它覆盖另一端，会把刚保存的消息整段抹掉（与后端 _merge_sessions 同一规则） */
          function isPrefixSeq(shorter, longer) {
            if (!shorter || !shorter.length || !longer) return false;
            if (shorter.length >= longer.length) return false;
            for (var i = 0; i < shorter.length; i++) {
              var a = shorter[i], b = longer[i];
              if (!a || !b) return false;
              if (msgSig(a) !== msgSig(b)) return false;
            }
            return true;
          }
          /* shorter 是否严格是 longer 的有序子序列（允许跳过若干消息）。
             删除消息后提交的剩余序列恰为此形态——用于区分「删除操作」与「并发分歧」。 */
          function isSubsequenceSeq(shorter, longer) {
            if (!shorter || !shorter.length || !longer) return false;
            if (shorter.length >= longer.length) return false;
            var j = 0, n = longer.length;
            for (var i = 0; i < shorter.length; i++) {
              var a = shorter[i], found = false;
              while (j < n) {
                var b = longer[j++];
                if (a && b && msgSig(a) === msgSig(b)) { found = true; break; }
              }
              if (!found) return false;
            }
            return true;
          }
          /* 消息级合并：保留 base 顺序，extra 中不在 base 里的消息按原顺序追加（去重）。 */
          function unionHistory(base, extra) {
            var seen = {}, out = (base || []).slice();
            (base || []).forEach(function(m){ seen[msgSig(m)] = true; });
            (extra || []).forEach(function(m){
              var k = msgSig(m);
              if (!seen[k]) { seen[k] = true; out.push(m); }
            });
            return out;
          }
          var _syncRetry = 0;
          /* 周期轮询先探测内容指纹（响应仅几十字节）：服务端没变就跳过全量拉取。
             sessions.json 可达数 MB，30s 一次的全量 JSON 拉取+解析在手机端是纯浪费；
             fp 来自上次全量拉取/PUT 响应，为空（首启）时必须全量拉一次打底 */
          var _lastSessionsFp = '';
          async function pollRemoteSync() {
            /* 指纹探测也必须有超时：它每 30s 一发，半开连接挂住会一条条占满同源连接池，
               把聊天/保存请求一起拖住（浏览器每源并发连接数很有限）。超时按失败处理。 */
            var fto = apiTimeoutSignal(null, 20000);
            try {
              var r = await fetch('/api/sessions/fingerprint', { signal: fto.signal });
              if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
              if (!r.ok) throw new Error('HTTP ' + r.status);
              var d = await r.json();
              if (d && d.fp && _lastSessionsFp && d.fp === _lastSessionsFp) return;
              /* 刻意不 await：这条链有自己的重试与错误处理，await 会让它的失败
                 冒进本 catch 从而重复触发一次全量拉取（与旧版行为不一致） */
              syncSessionsFromServer();
            } catch (e) {
              /* 探测失败（网络抖动/超时/端点异常）：退回全量拉取，行为与旧版一致 */
              syncSessionsFromServer();
            } finally {
              fto.done();
            }
          }
          function syncSessionsFromServer() {
            /* 全量会话拉取同样半开即永久挂起（并占住同源连接池）。超时按失败处理，
               交给下方既有重试（最多 3 次）自愈。 */
            var sto = apiTimeoutSignal(null, 20000);
            fetch('/api/sessions', { signal: sto.signal }).then(function(r){
              if (r.status === 401) throw new Error('未登录');  /* 不跳页：首启登录可能未就绪，走重试 */
              return r.ok ? r.json() : null;
            }).then(function(data){
              if (!data || !Array.isArray(data.sessions)) return;
              if (data.fp) _lastSessionsFp = data.fp;  /* 记录指纹：后续轮询命中即跳过全量 */
              // 按 id 合并而非直接覆盖：同 id 取 updatedAt 更新的一份，
              // 本地独有的新会话保留，避免服务端旧快照吞掉本地刚产生的消息
              var remote = data.sessions.map(normalizeSession).filter(function(s){ return !isSeedSession(s) && !isTestSession(s); });
              /* 删除墓碑合并：任一端删过的会话，所有端都保持删除（防旧快照复活） */
              var dead = {};
              (Array.isArray(data.deleted) ? data.deleted : []).forEach(function(t){
                if (t && t.id) dead[t.id] = Math.max(dead[t.id] || 0, t.ts || 0);
              });
              var tombMap = {};
              loadTombstones().forEach(function(t){ if (t && t.id) tombMap[t.id] = Math.max(tombMap[t.id] || 0, t.ts || 0); });
              Object.keys(dead).forEach(function(id){ tombMap[id] = Math.max(tombMap[id] || 0, dead[id]); });

              var localById = {};
              sessions.forEach(function(x){ localById[x.id] = x; });
              var changed = false, activeReplaced = false, mergedIds = {};
              var merged = [];
              remote.forEach(function(r){
                mergedIds[r.id] = true;
                var dt = dead[r.id];
                if (dt !== undefined && (r.updatedAt || 0) <= dt) return; /* 最后更新早于删除 → 保持删除 */
                var l = localById[r.id];
                var mergedObj = r;
                if (l) {
                  var lh = l.history || [], rh = r.history || [];
                  var localNewer = (l.updatedAt || 0) > (r.updatedAt || 0);
                  /* 本地是服务端纯旧快照（消息更少且为其前缀）且 updatedAt 比服务端新
                     超过 60s（时钟偏移）→ 用服务端，不允许本地旧副本覆盖；
                     60s 内的前缀变更视为「删除最后一条消息」的快速操作，删除生效 */
                  var localStale = rh.length > lh.length && isPrefixSeq(lh, rh)
                                   && localNewer && ((l.updatedAt || 0) - (r.updatedAt || 0)) > 60000;
                  /* 等长全等 = 同一版本正常演进；必须先于分叉判定排除，
                     否则两端各发一条（前缀相同、长度相同、末尾不同）这种最常见的
                     并发形态会落到"时间新者胜"，一端的消息+回复被整段抹掉 */
                  var sameSeq = lh.length === rh.length && lh.every(function(m, i){
                    var x = rh[i];
                    return m && x && msgSig(m) === msgSig(x);
                  });
                  /* 分歧：两端各有对方没有的消息（并发各发各话，含等长分叉）→ 消息级合并 */
                  var diverge = !localStale && !sameSeq
                                && !isPrefixSeq(lh, rh) && !isPrefixSeq(rh, lh)
                                && !isSubsequenceSeq(lh, rh) && !isSubsequenceSeq(rh, lh);
                  if (localStale) {
                    mergedObj = r;
                  } else if (diverge) {
                    var base = localNewer ? l : r, extra = localNewer ? r : l;
                    mergedObj = normalizeSession({
                      id: r.id, title: (localNewer ? l : r).title,
                      updatedAt: Math.max(l.updatedAt || 0, r.updatedAt || 0),
                      history: unionHistory(base.history || [], extra.history || []),
                      pinned: !!(localNewer ? l : r).pinned,
                      manualTitle: !!(localNewer ? l : r).manualTitle,
                      /* role 必须带走：漏掉会让 2027 分支会话变回 role:''，
                         在所有设备的普通角色会话列表里泄漏出来 */
                      role: (localNewer ? l : r).role || l.role || r.role || ''
                    });
                  } else {
                    mergedObj = localNewer ? l : r; /* 删除/正常演进：时间新者胜 */
                  }
                }
                var localWins = mergedObj === l;
                if (!l) changed = true;
                else if (!localWins && sessionFingerprint(l) !== sessionFingerprint(mergedObj)) changed = true;
                if (!localWins && r.id === currentSessionId && l && sessionFingerprint(l) !== sessionFingerprint(mergedObj)) activeReplaced = true;
                merged.push(mergedObj);
              });
              sessions.forEach(function(l){
                if (mergedIds[l.id]) return;
                var dt = tombMap[l.id];
                if (dt !== undefined && (l.updatedAt || 0) <= dt) { changed = true; return; } /* 已被另一端删除 */
                merged.push(l);
              });
              /* 墓碑维护：会话 updatedAt 已新于墓碑视为被合法重建，移除该墓碑 */
              var newTomb = [];
              Object.keys(tombMap).forEach(function(id){
                var ts = tombMap[id];
                /* 墓碑不按时间过期：会话 id 永不复用，留着只会压住"已删"这一事实，
                   而按 30 天丢弃会让离线一个月的设备重新上线时把已删会话连同历史
                   一起复活（服务端 sessions_merge 同一策略，两边不得漂移） */
                for (var i = 0; i < merged.length; i++) {
                  if (merged[i].id === id && (merged[i].updatedAt || 0) > ts) return;
                }
                newTomb.push({ id: id, ts: ts });
              });
              saveTombstones(newTomb);

              /* 与远端完全一致：不渲染也不回传，避免两端互相 PUT 乒乓；
                 内容一致即服务端已有全量，顺手清掉 dirty（省掉 15s 补推的一次空跑） */
              if (!changed && merged.length === sessions.length) {
                if (state.dirty) { state.dirty = false; if (state.syncState !== 'ok') setSyncState('ok'); }
                return;
              }

              sessions = merged;
              var activeIdBefore = currentSessionId;
              if (!sessions.some(function(x){ return x.id === currentSessionId; })) {
                currentSessionId = sortSessions(sessions)[0].id;
              }
              /* 首次同步落地（新设备/清存储后打开）：当前还是没动过的空草稿时，
                 直接切到最近的真实会话，不再面对空白输入页 */
              var curDraft = findSession(currentSessionId);
              var _ta = document.getElementById('text');
              if (curDraft && isPlaceholderSession(curDraft)
                  && (!_ta || !_ta.value) && !state.pendingAttachments.length) {
                var latest = sortSessions(sessions).find(function(x){ return !isPlaceholderSession(x); });
                if (latest) currentSessionId = latest.id;
              }
              persistLocal();
              renderSessions();
              /* 不要打断进行中的对话：正在等回复（typing）或正在朗读时跳过重渲染，
                 数据已合并进 sessions，切会话/下次渲染自然生效 */
              var busy = !!document.getElementById('typing') || !!state.playing;
              if (currentSessionId !== activeIdBefore || (activeReplaced && !busy)) {
                /* 循环播放不再屏蔽合并（见 isSyncBusy）：朗读中仍要重绘视图时，
                   必须保音频、保视口，否则合并会把正在播的声音掐断 */
                renderCurrentSession(state.playing ? { preserveScroll: true, keepAudio: true } : null);
              }
              /* 合并结果回传服务端，让本地独有的会话/消息在另一端也可见；
                 服务端广播会跳过本设备，不会触发自己再同步 */
              saveSessions();
              _syncRetry = 0;  /* 同步成功，重置重试计数 */
            }).catch(function(){
              /* 失败重试：重装/首启时登录可能尚未就绪（预热登录/代理自动重登进行中），
                 最多重试 3 次，避免会话历史静默丢失（本地只剩"新对话"）。
                 超时也走这里：计时器读完全文才清（见 finally），避免半开挂在 r.json() 阶段 */
              if (_syncRetry < 3) {
                _syncRetry++;
                setTimeout(function(){ syncSessionsFromServer(); }, 2000);
              }
            }).finally(function(){ sto.done(); });
          }
          /* ---------- 多端实时同步：SSE 推送 + 可见性/定时兜底 ---------- */
          function isSyncBusy() {
            /* 生成中/朗读中不执行合并：防止会话对象被中途替换导致
               进行中的消息写入旧引用，也避免重渲染打断用户。
               但循环播放永不触发 ended（state.playing 一直挂着），若一并屏蔽，
               多端同步会永久停摆 —— 循环播放只挡重渲染（见 syncSessionsFromServer 的 busy），
               不挡数据合并 */
            return !!document.getElementById('typing') || !!(state.playing && !state.playing.loop);
          }
          var syncPending = false;
          function requestRemoteSync() {
            if (isSyncBusy()) { syncPending = true; return; }
            syncSessionsFromServer();
          }
          /* 忙时挂起的同步请求：轮询等空闲后补执行 */
          setInterval(function(){
            if (syncPending && !isSyncBusy()) { syncPending = false; syncSessionsFromServer(); }
          }, 3000);
          var syncES = null;
          function startSyncStream() {
            if (!window.EventSource) return;
            try { if (syncES) syncES.close(); } catch (e) {}
            try {
              syncES = new EventSource('/api/sync/stream?client=' + encodeURIComponent(CLIENT_ID));
              syncES.onmessage = function(e){
                var ev = null;
                try { ev = JSON.parse(e.data); } catch (err) {}
                if (ev && ev.type === 'sessions_updated') requestRemoteSync();
              };
              syncES.onerror = function(){
                /* readyState=CLOSED 表示被服务端终止（如 401/网关错误），浏览器不会再
                   自动重连；不手动轮询重连（可见性 + 30s 定时轮询已兜底，避免未登录时刷请求） */
                if (syncES && syncES.readyState === 2) syncES = null;
              };
              /* 断线由 EventSource 自动重连（服务端 retry: 3000）；
                 重连失败期间的变更由下方可见性/定时轮询兜底补齐 */
            } catch (e) {}
          }
          /* 页面卸载/后台冻结时主动断开，避免服务端挂着死连接 */
          window.addEventListener('pagehide', function(){
            try { if (syncES) syncES.close(); } catch (e) {}
          });
          /* ---------- iOS 软键盘遮挡输入栏（visualViewport） ----------
             触发条件：iPhone/iPad 上点输入框。iOS 的键盘是**叠加层**：布局视口
             (innerHeight) 不变、100dvh 不变，而 body 是 overflow:hidden 无法滚动让位，
             于是输入胶囊整块被键盘盖住，用户看不见自己打的字。
             Chromium 被 <meta interactive-widget=resizes-content> 救了，所以只在
             iOS 类浏览器上需要补偿：把"窗口高度 - 可视视口高度"当作键盘遮挡高度写进
             --kb-h，由 CSS 补到 .inputbar 的 padding-bottom（见 css/chat.css）。
             只在变化时写 DOM，且用 rAF 合并 resize 风暴。 */
          (function initKeyboardInset(){
            var vv = window.visualViewport;
            if (!vv) return;  /* 不支持的浏览器保持原样（Chromium 本来也不受影响） */
            var raf = 0;
            function apply(){
              raf = 0;
              var overlap = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
              /* 40px 阈值：地址栏收放/工具栏动画会造成几十像素抖动，不该当成键盘 */
              var h = overlap > 40 ? overlap : 0;
              var cur = document.documentElement.style.getPropertyValue('--kb-h');
              var next = h ? h + 'px' : '0px';
              if (cur !== next) document.documentElement.style.setProperty('--kb-h', next);
            }
            function schedule(){ if (!raf) raf = requestAnimationFrame(apply); }
            vv.addEventListener('resize', schedule);
            vv.addEventListener('scroll', schedule);
            /* 输入框聚焦时键盘动画还没结束，尺寸会陆续变几次；补一次聚焦后的复算 */
            document.addEventListener('focusin', function(e){
              var t = e.target;
              if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT')) {
                schedule();
                setTimeout(schedule, 120);
                setTimeout(schedule, 320);
              }
            });
            apply();
          })();
          /* bfcache 恢复（iOS/Android 前进后退）：JS 环境复活但原 EventSource 已在
             pagehide 关闭且不会自动重建，实时推送会永久失效——persisted 恢复时重连 */
          window.addEventListener('pageshow', function(e){
            if (e.persisted) startSyncStream();
          });
          startSyncStream();
          /* 兜底：切回前台立即补齐后台期间错过的变更；页面可见时每 30s 探测一次
             服务端指纹（没变就跳过全量拉取，代价极低） */
          document.addEventListener('visibilitychange', function(){
            if (document.visibilityState === 'visible') {
              /* 回前台：给状态探针重新 6 次重试的机会（隧道重启后不该永久停在"后端未连接"） */
              _statusRetry = 0;
              pollRemoteSync();
            }
          });
          setInterval(function(){
            if (document.visibilityState === 'visible') pollRemoteSync();
          }, 30000);
          /* 入场动画只播一次：收发消息触发的重渲染不要重放动画 */
          var sessionsAnimatedOnce = false;
          /* 会话列表渲染去重：markSessionActivity 在每次收发消息都会触发 renderSessions，
             但排序/置顶/标题/活跃时间/选中态都没变时 DOM 无需重建。
             用轻量指纹串比对（不依赖深比较），不变直接跳过 → 连续收发消息不再反复重建列表 */
          var sessionsRenderKey = '';
          /* ---- 主对话：每角色一个固定主对话（id=main_{role}），置顶常驻 ----
             首次进入某角色时，若存在旧会话，取最新一个的内容迁移进主对话（旧会话保留在下方列表，可随时切回）。 */
          function mainSessionId(){ return 'main_' + (currentRoleKey || 'role'); }
          function ensureMainSession(){
            var mid = mainSessionId();
            var main = findSession(mid);
            if (main) return main;
            var latest = sortSessions(sessions.filter(function(x){ return x.id !== mid; }))[0];
            var s = makeSession('新对话');
            s.id = mid;
            var roleName = '';
            var rr = (state.roles || []).find(function(x){ return x.key === currentRoleKey; });
            if (rr) roleName = rr.name;
            s.title = (roleName || currentRoleKey) + '·主对话';
            s.manualTitle = true;           /* 主对话标题固定，不被首条消息覆盖 */
            s.role = currentRoleKey || '';  /* 与主对话 id 对应的角色一致 */
            s.history = latest ? latest.history.slice() : [];
            s.updatedAt = latest ? latest.updatedAt : Date.now();
            sessions.push(s);
            persistLocal();
            return s;
          }
          /* 会话按角色可见性：主对话固定排最前；2027 分支会话只归自己；
             其余角色互相可见历史会话（占位空会话不显示） */
          function visibleSessions() {
            var main = ensureMainSession();
            if (currentRoleKey === 'dashuai2027') return [main];
            return [main].concat(sessions.filter(function(x){
              return x.id !== main.id && x.role !== 'dashuai2027' && !isPlaceholderSession(x);
            }));
          }
          /* 角色变化时才把当前会话校正回主对话；同一角色内用户自由切换历史会话，
             不被周期性状态刷新拽回。
             冷启动首轮（_lastSyncedRole 为 null）不硬切：初始化已按 savedActive
             恢复过有效会话，硬切会让"刷新即回到主对话"（历史看起来像被回退）。 */
          var _lastSyncedRole = null;
          function syncSessionForRole() {
            var main = ensureMainSession();
            if (_lastSyncedRole !== currentRoleKey) {
              var first = (_lastSyncedRole === null);
              _lastSyncedRole = currentRoleKey;
              var keep = false;
              if (first) {
                try {
                  var visIds = {};
                  visibleSessions().forEach(function(x){ visIds[x.id] = true; });
                  keep = !!visIds[currentSessionId];
                } catch (e) { keep = false; }
              }
              if (!keep) {
                currentSessionId = main.id;
                renderCurrentSession();
              }
            }
            renderSessions();
          }
          /* 2027 赛季入口：仅 dashuai2027 角色激活时显示（跳赛程表页面），标签实时带今日剧情 */
          var _storyEntryFetchTs = 0;
          function updateStoryEntry(activeRole) {
            var el = document.getElementById('story-entry');
            if (!el) return;
            var on = (activeRole === 'dashuai2027');
            el.style.display = on ? 'flex' : 'none';
            if (on) refreshStoryEntry(null);
          }
          /* 刷新赛程入口标签：storyUpdate 直接来自聊天响应的剧情快照；否则节流拉 /api/story/status */
          function refreshStoryEntry(storyUpdate) {
            var el = document.getElementById('story-entry');
            if (!el || currentRoleKey !== 'dashuai2027') return;
            var label = el.querySelector('.se-label');
            if (!label) return;
            function apply(x) {
              var vd = x.virtual_date || '';
              var title = x.title || ((x.today && x.today.title) || '');
              label.textContent = vd ? ('2027 赛程 · ' + vd.slice(5) + ' ' + title) : '2027 赛季赛程表';
              el.title = '2027 KPL 赛季赛程表：跳转任意日期、查看战绩与剧情进度' + (title ? '（今日：' + title + '）' : '');
            }
            if (storyUpdate && storyUpdate.virtual_date) {
              _storyEntryFetchTs = Date.now();
              apply(storyUpdate);
              return;
            }
            if (Date.now() - _storyEntryFetchTs < 30000) return;
            _storyEntryFetchTs = Date.now();
            api('/api/story/status').then(function(d){ if (d) apply(d); }).catch(function(){});
          }
          function renderSessions() {
            if (!sessionsBox) return;
            /* 主对话固定排最前，其余按置顶/活跃时间排序；key 与渲染共用同一顺序 */
            var vis = visibleSessions();
            var ordered = vis.slice(0, 1).concat(sortSessions(vis.slice(1)));
            /* 会话搜索：标题或正文命中即保留（正文从新往旧扫，命中即停） */
            var q = (sessionSearchQuery || '').trim().toLowerCase();
            if (q) {
              ordered = ordered.filter(function(x){
                if ((x.title || '').toLowerCase().indexOf(q) >= 0) return true;
                var h = x.history || [];
                for (var hi = h.length - 1; hi >= 0; hi--) {
                  var c = h[hi] && h[hi].content;
                  if (c && String(c).toLowerCase().indexOf(q) >= 0) return true;
                }
                return false;
              });
            }
            /* key 里的时间必须**分桶**（分钟粒度），不能用原始 updatedAt：
               markSessionActivity 每收一条消息就调 renderSessions，而 updatedAt 每次都变
               -> key 永远不同 -> 整张侧栏（N 个会话 × 3-4 个监听）每条消息重建一次，
               对已做窗口化的消息区来说这里是漏网的 O(N) 重渲染。
               分桶到 1 分钟与 timeLabel 的展示粒度一致（<1min 显示"刚刚"，不随秒数变）；
               分钟刻度跨过时 key 变化、照常重建，所以显示不会过期。 */
            var key = q + '~~' + ordered.map(function(x){
              return x.id + '|' + (x.pinned ? 1 : 0) + '|' + (x.title || '') + '|'
                + Math.floor((x.updatedAt || 0) / 60000) + '|' + (x.id === currentSessionId ? 1 : 0);
            }).join('~');
            if (key === sessionsRenderKey && sessionsBox.childNodes.length) {
              /* 结构没变时顺便原地刷新已渲染的时间标签（同一分钟内从"刚刚"走到"N 分钟前"
                 这种跨桶边界的情况由上面的 key 变化兜住，这里只做无副作用的同步） */
              ordered.forEach(function(session){
                var node = sessionsBox.querySelector('.session[data-id="' + session.id + '"]');
                var tEl = node && node.querySelector('.s-time');
                if (tEl) {
                  var lbl = timeLabel(session.updatedAt);
                  if (tEl.textContent !== lbl) tEl.textContent = lbl;
                }
              });
              return;
            }
            sessionsRenderKey = key;
            sessionsBox.innerHTML = '';
            if (!ordered.length) {
              var empty = document.createElement('div');
              empty.className = 'sessions-empty';
              empty.textContent = q ? '没有匹配的会话，换个关键词试试' : '还没有会话，点「新对话」开始聊吧';
              sessionsBox.appendChild(empty);
              sessionsAnimatedOnce = true;
              return;
            }
            /* 文档片段批量挂载：几十个会话一次性 append，只触发一次布局/重绘 */
            var frag = document.createDocumentFragment();
            ordered.forEach(function(session, idx){
              var s = document.createElement('button');
              s.className = 'session' + (!sessionsAnimatedOnce ? ' side-in' : '') + (session.id === currentSessionId ? ' active' : '') + (session.pinned ? ' pinned' : '');
              if (!sessionsAnimatedOnce) s.style.animationDelay = Math.min(idx * 30, 300) + 'ms';
              s.dataset.id = session.id;
              s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="s-title"></span><span class="s-pin"></span><span class="s-time"></span>';
              s.querySelector('.s-title').textContent = session.title;
              var pinEl = s.querySelector('.s-pin');
              if (session.pinned) {
                pinEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z"/></svg>';
              } else {
                pinEl.style.display = 'none';
              }
              s.querySelector('.s-time').textContent = timeLabel(session.updatedAt);
              bindSessionClick(s);
              bindSessionMenu(s);
              frag.appendChild(s);
            });
            sessionsBox.appendChild(frag);
            sessionsAnimatedOnce = true;
          }
          /* 历史窗口化参数与「加载更早」按钮（渲染逻辑见 renderCurrentSession） */
          var HISTORY_WINDOW = 60, HISTORY_CHUNK = 40;
          var historyRenderedFrom = 0;  /* 当前已渲染的历史起始下标 */
          function renderHistoryRange(rangeFrom, rangeTo, container) {
            var hist = state.history || [];
            hist.slice(rangeFrom, rangeTo).forEach(function(m, k){
              var i = rangeFrom + k;
              if (m.role === 'assistant' && m.narration) {
                addMsg('narration', m.narration, { noScroll: true, animate: false, container: container });
              }
              var div = addMsg(m.role, m.content, {
                noScroll: true, attachments: m.attachments || [], animate: false, container: container
              });
              div.dataset.hidx = String(i);
              if (m.role === 'assistant') {
                var meta = div.querySelector('.msg-meta');
                meta.appendChild(makeSpeakBtn(m.content, m.style || '', m, currentSessionId));
                meta.appendChild(makeResynthBtn(m.content, m.style || '', m, currentSessionId));
                meta.appendChild(makeRegenBtn(div));
              }
            });
          }
          function makeLoadEarlierBtn() {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'load-earlier';
            btn.textContent = '加载更早消息（还有 ' + historyRenderedFrom + ' 条）';
            btn.addEventListener('click', function(){
              var scroller = document.getElementById('msgs');
              var prevH = scroller ? scroller.scrollHeight : 0;
              var newFrom = Math.max(0, historyRenderedFrom - HISTORY_CHUNK);
              var frag = document.createDocumentFragment();
              renderHistoryRange(newFrom, historyRenderedFrom, frag);
              historyRenderedFrom = newFrom;
              if (newFrom > 0) {
                btn.textContent = '加载更早消息（还有 ' + newFrom + ' 条）';
                msgsInner.insertBefore(frag, btn);
              } else {
                msgsInner.insertBefore(frag, btn);
                btn.remove();
              }
              /* 视口锚定：新内容插在上方会把视口顶走，按高度差补回 scrollTop */
              if (scroller) scroller.scrollTop += (scroller.scrollHeight - prevH);
            });
            return btn;
          }
          function renderCurrentSession(opts) {
            opts = opts || {};
            /* 默认行为不变：切会话/新对话/删当前会话时停掉朗读并作废挂起请求。
               删除单条消息等轻量重绘传 {keepAudio:true}，不断正在听的音频。 */
            if (!opts.keepAudio) {
              stopAudio();
              state.playSeq++; /* 切换会话：作废所有在飞/挂起的朗读请求，防止旧结果晚到抢播 */
            }
            hideTyping();
            /* preserveScroll：删除旧消息时记住"距底部距离"，重绘后恢复，
               不再把正在上翻看历史的用户拽到底部 */
            var scrollerEl = (opts.preserveScroll && document.getElementById('msgs')) || null;
            var keepDist = scrollerEl
              ? (scrollerEl.scrollHeight - scrollerEl.scrollTop - scrollerEl.clientHeight) : 0;
            /* 连 .msg-guide（2027 剧情引导卡）与 .load-earlier（窗口化按钮）一起清：
               类名都不含 .msg，漏清会残留并叠加（多次重渲染出现多张卡/多个按钮） */
            msgsInner.querySelectorAll('.msg, .msg-guide, .load-earlier').forEach(function(m){ m.remove(); });
            var s = currentSession();
            state.history = s ? s.history : [];
            /* 有历史消息时收起欢迎区：landing 状态与聊天记录不该同屏 */
            var welcome = document.querySelector('.welcome');
            if (welcome) welcome.style.display = state.history.length ? 'none' : '';
            syncWelcomeVideo(state.history.length === 0);
            /* 文档片段批量渲染：长历史一次性挂载，只触发一次布局/重绘（切会话零卡顿） */
            var frag = document.createDocumentFragment();
            /* 2027 剧情分支：空主对话时显示今日剧情引导卡（数据来自 /api/story/status） */
            if (currentRoleKey === 'dashuai2027' && !state.history.length) {
              var gc = document.createElement('div');
              gc.className = 'msg-guide';
              gc.textContent = '剧情加载中…';
              frag.appendChild(gc);
              api('/api/story/status').then(function(d){
                if (!d || !gc.parentNode) return;
                var vd = d.virtual_date || '', sn = d.stage_name || '';
                var t = (d.today && d.today.title) || '';
                var body = d.guide || ((d.today && d.today.event && d.today.event.desc) || t);
                var tip = '在下面用（括号）描写动作/环境开始演出，例如：（我拖着行李箱站在 AG 楼下）。旁白会铺场景，大帅会接住你的戏。';
                /* 见面与否按虚拟日期判断（2027-01-05 冬训集结才首次见面），不能用情感阶段——
                   首败前 stage 恒为 0，若用 stage 判断，见面后仍会误提示「还没见过面」 */
                if (d.virtual_date && d.virtual_date < '2026-12-20') {
                  tip = '你们还没见过面，他只是巅峰赛里风头正盛的路人王 ID。用（括号）开场：巅峰赛撞车、刷到他的操作集锦、或者听圈内人议论「岚风是谁」。';
                } else if (d.virtual_date && d.virtual_date < '2027-01-05') {
                  tip = '他官宣当天就进了基地，正坐在一诺的旧工位（左边长生、右边大帅），你俩还没正式说过话。用（括号）开场：训练室报到日的第一面、被起哄「诺哥的位置坐得稳吗」、或围观他开播时弹幕刷屏。';
                }
                var h1 = document.createElement('div'); h1.className = 'g-date';
                h1.textContent = vd + ' · ' + sn;
                var tag = document.createElement('span'); tag.className = 'g-tag'; tag.textContent = t;
                h1.appendChild(tag);
                var h2 = document.createElement('div'); h2.className = 'g-desc'; h2.textContent = body;
                var h3 = document.createElement('div'); h3.className = 'g-tip'; h3.textContent = '开始：' + tip;
                gc.innerHTML = '';
                gc.appendChild(h1); gc.appendChild(h2); gc.appendChild(h3);
                if (stickBottom) snapBottom();
              }).catch(function(){});
            }
            /* 历史窗口化：长会话首屏只渲染最近 HISTORY_WINDOW 条，更早的收进顶部
               「加载更早」按钮（每次补 HISTORY_CHUNK 条）。上千条历史一次性建 DOM
               会卡死切会话/重渲染；hidx 始终用真实下标，编辑/删除/重生成都按它寻址 */
            var hist = state.history || [];
            var from = Math.max(0, hist.length - HISTORY_WINDOW);
            historyRenderedFrom = from;
            if (from > 0) frag.appendChild(makeLoadEarlierBtn());
            hist.slice(from).forEach(function(m, k){
              var i = from + k;
              /* 长历史（>16 条）瞬时渲染：不播入场动画，切会话零等待也不掉帧 */
              var animOn = hist.length <= 16;
              /* 旁白双声部：assistant 消息带 narration 时先渲染旁白气泡（不占 hidx、不带按钮） */
              if (m.role === 'assistant' && m.narration) {
                addMsg('narration', m.narration, {
                  noScroll: true, animate: animOn, container: frag
                });
              }
              var div = addMsg(m.role, m.content, {
                delay: animOn ? Math.min(k * 45, 400) : 0, noScroll: true,
                attachments: m.attachments || [], animate: animOn,
                container: frag
              });
              div.dataset.hidx = String(i);
              if (m.role === 'assistant') {
                var meta = div.querySelector('.msg-meta');
                meta.appendChild(makeSpeakBtn(m.content, m.style || '', m, currentSessionId));
                meta.appendChild(makeResynthBtn(m.content, m.style || '', m, currentSessionId));
                meta.appendChild(makeRegenBtn(div));
              }
            });
            msgsInner.appendChild(frag);
            /* 消息搜索开着时按新视图重跑，保证命中与当前内容一致 */
            try { if (typeof runMsgSearch === 'function' && msgSearchBar && !msgSearchBar.hidden) runMsgSearch(); } catch (e) {}
            /* 历史以用户消息结尾 = 上一轮生成失败/中断，回复没入库（失败气泡不持久化）。
               补一条带「重试」按钮的失败气泡，避免只剩用户消息无法重新生成；
               正在等待回复时不补（此时末尾用户消息属于进行中的请求） */
            var lastM = state.history[state.history.length - 1];
            if (s && lastM && lastM.role === 'user' && !state.awaiting[s.id]) {
              addFailureMsg(s.id, '上次回复没有生成成功', {
                idx: state.history.length - 1,
                text: lastM.content || '',
                atts: lastM.attachments || []
              }, { noScroll: true });
            }
            updateChatTitle();
            if (opts.keepAudio && state.playing) rebindPlayingBtn();
            if (scrollerEl) {
              scrollerEl.scrollTop = Math.max(0, scrollerEl.scrollHeight - scrollerEl.clientHeight - keepDist);
            } else {
              scrollBottom(true);
            }
            restoreDraft();  /* 恢复当前会话未发送的草稿 */
          }
          function switchSession(id) {
            if (id === currentSessionId) return;
            abortChatInFlight();  /* 切走即停掉旧会话的在飞生成：旧流晚到不再写回错误视图 */
            saveDraft(true);  /* 切走前存下旧会话的草稿 */
            currentSessionId = id;
            saveSessions();
            renderSessions();
            renderCurrentSession();
            closeDrawer();
          }
          /* ---------- 移动端抽屉（≤900px 侧边栏滑入滑出） ---------- */
          var sideEl = document.querySelector('.side');
          var sideBackdrop = document.getElementById('side-backdrop');
          var menuBtn = document.getElementById('menu-btn');
          var drawerMq = window.matchMedia ? window.matchMedia('(max-width:900px)') : null;
          function isMobileLayout() { return !!(drawerMq && drawerMq.matches); }
          function openDrawer() {
            if (!sideEl) return;
            sideEl.classList.add('open');
            if (sideBackdrop) sideBackdrop.classList.add('show');
            document.body.classList.add('drawer-open');  /* 锁背景滚动 */
          }
          function closeDrawer() {
            if (!sideEl) return;
            sideEl.classList.remove('open');
            if (sideBackdrop) sideBackdrop.classList.remove('show');
            document.body.classList.remove('drawer-open');
          }
          if (menuBtn) menuBtn.addEventListener('click', function(){
            if (sideEl && sideEl.classList.contains('open')) closeDrawer();
            else openDrawer();
          });
          if (sideBackdrop) sideBackdrop.addEventListener('click', closeDrawer);
          /* 手势开关抽屉：近左边缘（16~56px）右滑开；抽屉打开时左滑关。
             起始区避开屏幕最边缘 ~16px——那是 iOS Safari 系统返回手势的地盘，
             从系统区内起手会同时触发返回和开抽屉。垂直位移占优时让位滚动 */
          (function(){
            if (!sideEl) return;
            var sx = 0, sy = 0, tracking = false, fromEdge = false;
            document.addEventListener('touchstart', function(e){
              tracking = false;
              if (!isMobileLayout() || e.touches.length !== 1) return;
              sx = e.touches[0].clientX; sy = e.touches[0].clientY;
              fromEdge = sx >= 16 && sx <= 56 && !sideEl.classList.contains('open');
              tracking = fromEdge || sideEl.classList.contains('open');
            }, { passive: true });
            document.addEventListener('touchmove', function(e){
              if (!tracking) return;
              var dx = e.touches[0].clientX - sx, dy = e.touches[0].clientY - sy;
              if (Math.abs(dy) > Math.abs(dx)) { tracking = false; return; }
              if (fromEdge && dx > 60) { openDrawer(); tracking = false; }
              else if (!fromEdge && dx < -60 && sideEl.classList.contains('open')) { closeDrawer(); tracking = false; }
            }, { passive: true });
            document.addEventListener('touchend', function(){ tracking = false; }, { passive: true });
          })();
          /* Esc 统一收起浮层：抽屉 / 设置弹窗 / 附件菜单 / 右键菜单 / 消息搜索 / 灯箱 */
          document.addEventListener('keydown', function(e){
            if (e.key !== 'Escape') return;
            /* 确认弹窗打开时只让它自己处理 Esc（取消），别顺手关掉底下的设置/抽屉 */
            try { if (_cfModal && _cfModal.classList.contains('show')) return; } catch (err) {}
            closeDrawer();
            closeSettings();
            toggleAttachMenu(false);
            hideCtxMenu();
            try { closeMsgSearch(); } catch (err) {}
            try { closeLightbox(); } catch (err2) {}
          });
          /* Ctrl/Cmd+K：跳到会话搜索（移动端先开抽屉）；焦点已在搜索框时不抢 */
          document.addEventListener('keydown', function(e){
            if (!(e.ctrlKey || e.metaKey) || e.key !== 'k' && e.key !== 'K') return;
            var t = e.target;
            if (t && t.id === 'sess-search-input') return;
            e.preventDefault();
            var si = document.getElementById('sess-search-input');
            if (!si) return;
            if (isMobileLayout()) {
              openDrawer();
              setTimeout(function(){ si.focus(); }, 220);
            } else {
              si.focus();
              if (si.select) si.select();
            }
          });
          /* 点击抽屉内条目（会话/角色/新对话/引擎切换/导航）后自动收起；
             重命名输入框(.renaming)除外，否则打字时抽屉会被关掉 */
          if (sideEl) sideEl.addEventListener('click', function(e){
            if (!isMobileLayout()) return;
            var t = (e.target.closest) ? e.target.closest('.session, .role-item, [data-dom-id="btn-new"], .provider-row') : null;
            if (!t || (t.closest && t.closest('.renaming'))) return;
            setTimeout(closeDrawer, 160);
          });
          /* 移动端占位符缩短：Enter/Shift+Enter 提示在窄屏会换行，手机上只保留主提示 */
          function refreshMobilePlaceholder() {
            if (!ta) return;
            ta.placeholder = isMobileLayout() ? '说点什么…' : '说点什么吧…（Enter 发送，Shift+Enter 换行）';
          }
          if (drawerMq && drawerMq.addEventListener) drawerMq.addEventListener('change', refreshMobilePlaceholder);
          refreshMobilePlaceholder();
          function bindSessionClick(s){
            s.addEventListener('click', function(){
              switchSession(s.dataset.id);
            });
          }
          /* 会话菜单项：桌面右键与触屏长按共用同一份。
             主对话是固定入口，禁重命名/置顶/删除，仅保留复制与导出 */
          function sessionMenuItems(sess){
            if (sess && sess.id === mainSessionId()) {
              return [
                { label: '复制对话记录', icon: 'copy', onClick: function(){
                  copyToClipboard(sessionTranscript(sess), '已复制对话记录');
                } },
                { label: '导出对话记录', icon: 'download', onClick: function(){ exportSession(sess); } },
              ];
            }
            return [
              { label: '重命名', icon: 'edit', onClick: function(){ startRenameSession(sess); } },
              { label: sess.pinned ? '取消置顶' : '置顶会话', icon: 'pin', onClick: function(){
                sess.pinned = !sess.pinned;
                saveSessions();
                renderSessions();
                toast(sess.pinned ? '已置顶会话' : '已取消置顶');
              } },
              { label: '复制对话记录', icon: 'copy', onClick: function(){
                copyToClipboard(sessionTranscript(sess), '已复制对话记录');
              } },
              { label: '导出对话记录', icon: 'download', onClick: function(){ exportSession(sess); } },
              { divider: true },
              { label: '删除会话', icon: 'trash', danger: true, onClick: function(){
                var wasActive = sess.id === currentSessionId;
                abortChatInFlight();  /* 删会话先停在飞生成，防旧流晚到写回已删会话 */
                stopAudio();
                state.playSeq++; /* 删除会话：作废所有在飞/挂起的朗读请求 */
                addTombstone(sess.id); /* 墓碑：其他设备同步时不得复活该会话 */
                /* 同步清掉该会话的输入草稿，否则 xiaoni_draft_<id> 孤儿键无界累积，
                   长期挤占 localStorage 配额 */
                try { localStorage.removeItem(draftKey(sess.id)); } catch (e) {}
                sessions = sessions.filter(function(x){ return x.id !== sess.id; });
                if (wasActive) {
                  /* 删的是当前会话：优先回主对话（必可见），保证总有有效 currentSessionId；
                     删光（仅剩主对话也被删的极端情况）才建新会话兜底 */
                  var main = ensureMainSession();
                  var next = findSession(main.id) || sortSessions(sessions)[0];
                  if (next) currentSessionId = next.id;
                  else {
                    // 删光后新建会话：必须同步 currentSessionId，否则它仍指向已删除的会话，
                    // 导致侧边栏无高亮、后续消息写不进任何会话
                    var fresh = makeSession('新对话');
                    sessions.push(fresh);
                    currentSessionId = fresh.id;
                  }
                }
                saveSessions();
                renderSessions();
                renderCurrentSession();
              } }
            ];
          }
          function bindSessionMenu(s){
            s.addEventListener('contextmenu', function(e){
              e.preventDefault();
              var sess = findSession(s.dataset.id);
              if (!sess) return;
              showCtxMenu(e.clientX, e.clientY, sessionMenuItems(sess));
            });
            /* 触屏长按 480ms 弹菜单（iOS Safari 不会触发 contextmenu）；
               移动超 12px 视为滚动取消；触发后吞掉 touchend 后续的 click，防止误切换会话 */
            var lpTimer = null, lpFired = false, lpX = 0, lpY = 0;
            s.addEventListener('touchstart', function(e){
              if (e.touches.length !== 1) return;
              lpFired = false;
              lpX = e.touches[0].clientX; lpY = e.touches[0].clientY;
              lpTimer = setTimeout(function(){
                lpFired = true;
                try { if (navigator.vibrate) navigator.vibrate(12); } catch (err) {}
                var sess = findSession(s.dataset.id);
                if (sess) showCtxMenu(lpX, lpY, sessionMenuItems(sess));
              }, 480);
            }, { passive: true });
            s.addEventListener('touchmove', function(e){
              if (!lpTimer) return;
              var t = e.touches[0];
              if (Math.abs(t.clientX - lpX) > 12 || Math.abs(t.clientY - lpY) > 12) {
                clearTimeout(lpTimer); lpTimer = null;
              }
            }, { passive: true });
            s.addEventListener('touchend', function(e){
              clearTimeout(lpTimer); lpTimer = null;
              if (lpFired) { e.preventDefault(); lpFired = false; }
            });
            s.addEventListener('touchcancel', function(){ clearTimeout(lpTimer); lpTimer = null; });
          }
          /* 内联重命名：把会话行换成输入框，回车/失焦保存，Esc 取消 */
          function startRenameSession(sess){
            var btn = sessionsBox.querySelector('.session[data-id="' + sess.id + '"]');
            if (!btn) return;
            var wrap = document.createElement('div');
            wrap.className = 'session renaming' + (sess.id === currentSessionId ? ' active' : '');
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 's-rename';
            input.maxLength = 40;
            input.value = sess.title;
            input.setAttribute('aria-label', '重命名会话');
            wrap.appendChild(input);
            btn.parentNode.replaceChild(wrap, btn);
            var done = false;
            function commit(){
              if (done) return; done = true;
              var v = input.value.replace(/\s+/g, ' ').trim();
              if (v && v !== sess.title) {
                sess.title = v;
                sess.manualTitle = true;
                saveSessions();
                if (sess.id === currentSessionId) updateChatTitle();
                toast('已重命名');
              }
              renderSessions();
            }
            function cancel(){
              if (done) return; done = true;
              renderSessions();
            }
            input.addEventListener('keydown', function(e){
              e.stopPropagation();
              if (e.isComposing || e.keyCode === 229) return; /* IME 合成期 Enter=确认候选词 */
              if (e.key === 'Enter') commit();
              else if (e.key === 'Escape') cancel();
            });
            input.addEventListener('blur', commit);
            requestAnimationFrame(function(){ input.focus(); input.select(); });
          }
          /* 复制文本到剪贴板（含旧浏览器降级） */
          function copyToClipboard(text, okMsg){
            function fallback(){
              var ta = document.createElement('textarea');
              ta.value = text;
              ta.setAttribute('readonly', '');
              ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
              document.body.appendChild(ta);
              ta.select();
              try { document.execCommand('copy'); toast(okMsg || '已复制'); }
              catch (err) { toast('复制失败'); }
              document.body.removeChild(ta);
            }
            if (navigator.clipboard && window.isSecureContext) {
              navigator.clipboard.writeText(text).then(function(){ toast(okMsg || '已复制'); }, fallback);
            } else {
              fallback();
            }
          }
          /* 把会话整理成可读文本（复制/导出共用） */
          function sessionTranscript(sess){
            /* 角色名动态取：切到大帅等角色后导出不再显示占位名 */
            var _r = (state.roles || []).find(function(x){ return x.key === currentRoleKey; });
            var _roleName = (_r && _r.name) || 'AI';
            var lines = [
              _roleName + '对话记录：' + (sess.title || '未命名会话'),
              '时间：' + new Date(sess.updatedAt || Date.now()).toLocaleString('zh-CN'),
              '——————————————'
            ];
            (sess.history || []).forEach(function(m){
              var who = m.role === 'assistant' ? _roleName : '我';
              lines.push('【' + who + '】');
              lines.push(m.content || '');
              (m.attachments || []).forEach(function(a){
                lines.push('（附件：' + (a.name || '未命名文件') + '）');
              });
              lines.push('');
            });
            return lines.join('\n');
          }
          /* 导出会话为 txt 文件 */
          function exportSession(sess){
            var fname = (sess.title || '对话记录').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40) + '.txt';
            var blob = new Blob([sessionTranscript(sess)], { type: 'text/plain;charset=utf-8' });
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = fname;
            document.body.appendChild(a);
            a.click();
            setTimeout(function(){ URL.revokeObjectURL(a.href); a.parentNode.removeChild(a); }, 500);
            toast('已导出：' + fname);
          }
          function newSession(){
            abortChatInFlight();  /* 新对话即停掉旧会话的在飞生成 */
            saveDraft(true);  /* 先存下当前会话的草稿，再切走 */
            var s = makeSession('新对话');
            sessions.unshift(s);
            currentSessionId = s.id;
            persistLocal();
            renderSessions();
            renderCurrentSession();
            /* 占位会话不落库：此时没有消息，saveSessions 会把空「新对话」同步到
               服务端并经并集合并下发到每台设备（会话列表被淹没的根源）。
               发出首条消息时 markSessionActivity → saveSessions 才真正持久化。 */
          }
          var newBtn = document.querySelector('[data-dom-id="btn-new"]');
          if (newBtn) {
            /* 多会话模式：主对话置顶常驻，历史会话列在下方，可自由新建/切换 */
            newBtn.addEventListener('click', function(e){
              e.preventDefault();
              e.stopPropagation();
              newSession();
              closeDrawer();
            });
          }
          var savedActive = null;
          try { savedActive = localStorage.getItem(ACTIVE_KEY); } catch (e) {}
          /* 恢复上次打开的会话（仍在可见列表里才用），否则回落主对话；
             服务端真实角色由 refreshStatus 同步校正（syncSessionForRole） */
          ensureMainSession();
          var _visIds = {};
          visibleSessions().forEach(function(x){ _visIds[x.id] = true; });
          currentSessionId = (savedActive && _visIds[savedActive]) ? savedActive : mainSessionId();
          /* 初始化只写本地：若 localStorage 为空而服务端有真实历史，
             此时 PUT 会把种子会话推上去覆盖历史（GET/PUT 竞态）。
             真正的服务端同步由 syncSessionsFromServer 合并后回传 */
          persistLocal();
          renderSessions();
          renderCurrentSession();
          syncSessionsFromServer();

          /* ---------- 顶部 chip：语音合成开关 ---------- */
          var voiceChip = document.querySelector('.top-chip.voice');
          function syncVoiceChipUI(){
            if (!voiceChip) return;
            voiceChip.classList.toggle('voice', state.soundOn);
            voiceChip.title = state.soundOn ? '点击关闭自动朗读' : '点击开启自动朗读';
            var t = voiceChip.querySelector('span');
            if (t) t.textContent = state.soundOn ? '语音合成' : '语音合成 关';
          }
          /* 语音合成 chip = 自动朗读开关：点击切换并持久化（此前是纯展示，误导以为可操作） */
          if (voiceChip) voiceChip.addEventListener('click', function(){
            state.soundOn = !state.soundOn;
            try { localStorage.setItem('xiaoni_sound_on', state.soundOn ? '1' : '0'); } catch(e) {}
            syncVoiceChipUI();
            toast(state.soundOn ? '自动朗读已开启' : '自动朗读已关闭（仍可手动点喇叭朗读）');
          });
          syncVoiceChipUI();

          /* ---------- 角色栏目折叠 ---------- */
          var roleLabel = document.getElementById('role-label');
          var roleListEl = document.getElementById('role-list');
          var roleCaret = document.getElementById('role-caret');
          if (roleLabel && roleListEl) {
            /* 裸读 localStorage 在存储被禁用的 WebView 里会抛错并中断后续初始化
               （refreshStatus/SW 注册都在它之后），统一防护 */
            var initCollapsed = false;
            try { initCollapsed = localStorage.getItem('role_collapsed') === '1'; } catch (e) {}
            if (initCollapsed) {
              roleLabel.classList.add('collapsed');
              roleListEl.classList.add('collapsed');
            } else {
              // initialize max-height for smooth transition later
              roleListEl.style.maxHeight = roleListEl.scrollHeight + 'px';
            }
            roleLabel.addEventListener('click', function(){
              var collapsed = roleLabel.classList.toggle('collapsed');
              if (collapsed) {
                roleListEl.style.maxHeight = roleListEl.scrollHeight + 'px';
                requestAnimationFrame(function(){ roleListEl.classList.add('collapsed'); });
                try { localStorage.setItem('role_collapsed', '1'); } catch (e) {}
              } else {
                roleListEl.classList.remove('collapsed');
                roleListEl.style.maxHeight = roleListEl.scrollHeight + 'px';
                // after transition, set maxHeight to none to allow dynamic content
                setTimeout(function(){
                  if (!roleLabel.classList.contains('collapsed')) roleListEl.style.maxHeight = 'none';
                }, 380);
                try { localStorage.setItem('role_collapsed', '0'); } catch (e) {}
              }
            });
            // refresh maxHeight when role list changes (called implicitly below via setTimeout fallback)
            var origRenderSideRoles = renderSideRoles;
            renderSideRoles = function(roles, active) {
              origRenderSideRoles(roles, active);
              setTimeout(function(){
                if (roleListEl && !roleLabel.classList.contains('collapsed')) {
                  roleListEl.style.maxHeight = roleListEl.scrollHeight + 'px';
                  setTimeout(function(){ if (!roleLabel.classList.contains('collapsed')) roleListEl.style.maxHeight = 'none'; }, 30);
                }
              }, 0);
            };
          }

          /* ---------- 同步状态行：点击重试 + 初始渲染 + 存储自检 ---------- */
          (function(){
            var el = document.getElementById('sync-status');
            if (el) el.addEventListener('click', function(){ saveSessions(true); });
            var chip = document.getElementById('sync-chip');
            if (chip) chip.addEventListener('click', function(){ saveSessions(true); });
            if (!isOnlineNow()) setSyncState('offline');
            else renderSyncStatus();
            /* 启动自检：localStorage 不可用（隐私模式/配额满/被禁用）立刻告警，
               别等到丢数据才发现 */
            try {
              localStorage.setItem('xiaoni_probe', '1');
              var probeOk = localStorage.getItem('xiaoni_probe') === '1';
              localStorage.removeItem('xiaoni_probe');
              if (!probeOk) throw new Error('probe mismatch');
            } catch (e) {
              setSyncState('error', '浏览器存储不可用');
              setTimeout(function(){ toast('浏览器存储不可用：聊天记录无法保存在本机，请检查隐私模式/存储权限'); }, 1500);
            }
            /* 开机即从快照恢复过：明确告诉用户，而不是静默多出记录 */
            if (window.__xiaoniRestored) {
              setTimeout(function(){ toast('已从本地快照恢复聊天记录'); }, 1200);
            }
          })();

          /* ---------- 诊断入口：Console 里执行 __xiaoniDiag()，把返回贴回来即可定位 ---------- */
          window.__xiaoniDiag = function() {
            var info = { sessions: 0, totalMsgs: 0, current: null,
              lastSync: null, syncState: null, dirty: false, saving: false, online: true,
              localOk: false, localLen: 0, localSessions: -1,
              bakAt: null, bakLen: 0, restored: !!window.__xiaoniRestored,
              serializable: false, serialErr: '' };
            try {
              info.sessions = sessions.length;
              info.totalMsgs = sessions.reduce(function(n, s){ return n + ((s.history || []).length); }, 0);
              info.current = currentSessionId;
              info.lastSync = state.lastSync;
              info.syncState = state.syncState;
              info.dirty = !!state.dirty;
              info.saving = !!state.saving;
              info.online = isOnlineNow();
            } catch (e) {}
            try {
              var raw = localStorage.getItem(SESSIONS_KEY);
              info.localLen = raw ? raw.length : 0;
              info.localSessions = raw ? JSON.parse(raw).length : -1;
              info.localOk = true;
            } catch (e) { info.localOk = false; info.localErr = (e && e.message) || String(e); }
            try { JSON.stringify(sessions); info.serializable = true; }
            catch (e) { info.serializable = false; info.serialErr = (e && e.message) || String(e); }
            try {
              var bat = localStorage.getItem(SYNC_BAK_AT);
              info.bakAt = bat ? new Date(parseInt(bat, 10)).toLocaleString('zh-CN') : null;
              var bak = localStorage.getItem(SYNC_BAK_KEY);
              info.bakLen = bak ? bak.length : 0;
            } catch (e) {}
            return info;
          };

          /* ---------- 会话搜索框（侧栏过滤标题与正文） ---------- */
          (function(){
            var inp = document.getElementById('sess-search-input');
            if (!inp) return;
            /* 每次按键都对所有会话的全部历史做正文扫描，防抖 180ms */
            var sessSearchTimer = 0;
            inp.addEventListener('input', function(){
              var v = inp.value || '';
              clearTimeout(sessSearchTimer);
              sessSearchTimer = setTimeout(function(){
                sessionSearchQuery = v;
                sessionsRenderKey = '';  /* query 已进 key，这里再清一次防旧 key 残留 */
                renderSessions();
              }, 180);
            });
          })();

          /* ---------- 消息内搜索（当前会话，↑/↓ 跳转） ---------- */
          var msgSearchBar = document.getElementById('msg-search');
          var msgSearchInput = document.getElementById('msg-search-input');
          var msgSearchCount = document.getElementById('msg-search-count');
          var msgSearchPrev = document.getElementById('msg-search-prev');
          var msgSearchNext = document.getElementById('msg-search-next');
          var msgSearchClose = document.getElementById('msg-search-close');
          var msgHits = [], msgHitIdx = -1;
          var msgSearchCapped = false;  /* 更早历史还有未载入的匹配（触 600 条加载上限） */
          function clearMsgSearchHi() {
            msgsInner.querySelectorAll('.msg-search-hit,.msg-search-current').forEach(function(el){
              el.classList.remove('msg-search-hit', 'msg-search-current');
            });
          }
          function paintMsgSearchCurrent() {
            msgsInner.querySelectorAll('.msg-search-current').forEach(function(el){ el.classList.remove('msg-search-current'); });
            var el = msgHits[msgHitIdx];
            if (!el) return;
            el.classList.add('msg-search-current');
            try { el.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' }); } catch (e) {
              try { el.scrollIntoView({ block: 'center' }); } catch (e2) {}
            }
            /* 尾缀 + 表示更早历史里还有匹配：点「加载更早消息」继续载入后计数会自动补全 */
            if (msgSearchCount) msgSearchCount.textContent = (msgHitIdx + 1) + '/' + msgHits.length + (msgSearchCapped ? '+' : '');
          }
          function runMsgSearch() {
            var q = msgSearchInput ? (msgSearchInput.value || '').trim().toLowerCase() : '';
            clearMsgSearchHi();
            msgHits = []; msgHitIdx = -1; msgSearchCapped = false;
            if (msgSearchCount) msgSearchCount.textContent = '';
            if (!q || !msgsInner) return;
            /* 全量搜索：窗口化后更早消息不在 DOM 里。先在 state.history 按下标全量匹配，
               把命中所需的最早消息批量补载入（上限约 600 条防长会话卡死），再高亮。
               旧实现只扫已渲染 DOM，长会话搜更早内容永远显示"无匹配"。 */
            try {
              var histAll = state.history || [];
              var earliest = -1;
              for (var hi = 0; hi < histAll.length; hi++) {
                var hc = histAll[hi] && histAll[hi].content;
                if (hc && String(hc).toLowerCase().indexOf(q) >= 0) { if (earliest < 0) earliest = hi; }
              }
              while (earliest >= 0 && earliest < historyRenderedFrom
                  && (histAll.length - historyRenderedFrom) < 600) {
                var nf = Math.max(0, historyRenderedFrom - 120);
                var f2 = document.createDocumentFragment();
                renderHistoryRange(nf, historyRenderedFrom, f2);
                var lb = msgsInner.querySelector('.load-earlier');
                historyRenderedFrom = nf;
                if (lb) {
                  msgsInner.insertBefore(f2, lb);
                  if (nf > 0) lb.textContent = '加载更早消息（还有 ' + nf + ' 条）';
                  else lb.remove();
                } else if (msgsInner.firstChild) {
                  msgsInner.insertBefore(f2, msgsInner.firstChild);
                } else {
                  msgsInner.appendChild(f2);
                }
              }
              msgSearchCapped = earliest >= 0 && earliest < historyRenderedFrom;
            } catch (err) {}
            msgsInner.querySelectorAll('.msg').forEach(function(m){
              var b = m.querySelector('.bubble');
              if (b && (b.textContent || '').toLowerCase().indexOf(q) >= 0) {
                m.classList.add('msg-search-hit');
                msgHits.push(m);
              }
            });
            if (!msgHits.length) {
              if (msgSearchCount) msgSearchCount.textContent = '无匹配';
              return;
            }
            msgHitIdx = 0;
            paintMsgSearchCurrent();
          }
          function stepMsgSearch(d) {
            if (!msgHits.length) return;
            msgHitIdx = (msgHitIdx + d + msgHits.length) % msgHits.length;
            paintMsgSearchCurrent();
          }
          function closeMsgSearch() {
            if (!msgSearchBar || msgSearchBar.hidden) return;
            msgSearchBar.hidden = true;
            clearMsgSearchHi();
            msgHits = []; msgHitIdx = -1;
          }
          (function(){
            var openBtn = document.getElementById('btn-msg-search');
            if (openBtn) openBtn.addEventListener('click', function(){
              if (!msgSearchBar) return;
              if (msgSearchBar.hidden) {
                msgSearchBar.hidden = false;
                if (msgSearchInput) { msgSearchInput.focus(); runMsgSearch(); }
              } else {
                closeMsgSearch();
              }
            });
            if (msgSearchClose) msgSearchClose.addEventListener('click', closeMsgSearch);
            if (msgSearchPrev) msgSearchPrev.addEventListener('click', function(){ stepMsgSearch(-1); });
            if (msgSearchNext) msgSearchNext.addEventListener('click', function(){ stepMsgSearch(1); });
            if (msgSearchInput) {
              /* 防抖：每次按键都全量扫历史 + 可能补建几百条 DOM，
                 长会话连续输入会明显掉帧；停顿 180ms 再跑 */
              var msgSearchTimer = 0;
              msgSearchInput.addEventListener('input', function(){
                clearTimeout(msgSearchTimer);
                msgSearchTimer = setTimeout(runMsgSearch, 180);
              });
              msgSearchInput.addEventListener('keydown', function(e){
                if (e.key === 'Enter') {
                  e.preventDefault();
                  clearTimeout(msgSearchTimer);
                  runMsgSearch();   /* Enter 立即搜最新输入，不等防抖 */
                  stepMsgSearch(e.shiftKey ? -1 : 1);
                }
              });
            }
          })();

          /* ---------- 浅色/深色主题（默认深色，localStorage 持久化） ---------- */
          var THEME_KEY = 'xiaoni_theme';
          /* 主题三态：dark / light / auto（跟随系统）。auto 档由 prefers-color-scheme
             实时解析，系统明暗切换时无需刷新页面 */
          function systemDark() {
            return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
          }
          function resolvedTheme(t) { return t === 'auto' ? (systemDark() ? 'dark' : 'light') : t; }
          function applyTheme(t) {
            if (t !== 'light' && t !== 'auto') t = 'dark';
            document.documentElement.classList.toggle('dark', resolvedTheme(t) === 'dark');
            /* 移动端浏览器顶栏颜色随主题同步（浅色不再挂深色顶栏） */
            var metaThemeColor = document.querySelector('meta[name="theme-color"]');
            if (metaThemeColor) metaThemeColor.setAttribute('content', resolvedTheme(t) === 'dark' ? '#0F0D11' : '#f8f3ea');
            try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
            var btn = document.getElementById('btn-theme');
            if (btn) {
              btn.title = t === 'auto' ? '主题：跟随系统（点击固定为' + (resolvedTheme(t) === 'dark' ? '浅色' : '深色') + '）'
                                        : (t === 'dark' ? '切换到浅色主题' : '切换到深色主题');
            }
            document.querySelectorAll('#theme-tabs .set-tab').forEach(function(b){
              b.classList.toggle('active', b.dataset.theme === t);
            });
          }
          (function(){
            var t = 'dark';
            try { t = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
            applyTheme(t === 'light' || t === 'auto' ? t : 'dark');
            var btn = document.getElementById('btn-theme');
            if (btn) btn.addEventListener('click', function(){
              /* 顶栏按钮：auto 档下点击 = 固定为当前解析结果的相反档；固定档则明暗互切 */
              var cur = 'dark';
              try { cur = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
              if (cur === 'auto') applyTheme(resolvedTheme('auto') === 'dark' ? 'light' : 'dark');
              else applyTheme(cur === 'dark' ? 'light' : 'dark');
            });
            /* 系统明暗切换时，auto 档实时跟随（固定档不受影响） */
            try {
              var mq = window.matchMedia('(prefers-color-scheme: dark)');
              var onScheme = function(){
                var cur = 'dark';
                try { cur = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
                if (cur === 'auto') applyTheme('auto');
              };
              if (mq.addEventListener) mq.addEventListener('change', onScheme);
              else if (mq.addListener) mq.addListener(onScheme);
            } catch (e) {}
          })();

          /* ---------- 消息字号三档（设置面板，仅放大正文与输入框） ---------- */
          var FONT_KEY = 'xiaoni_font';
          function applyFont(f) {
            if (f !== 'lg' && f !== 'xl') f = 'std';
            if (f === 'std') document.documentElement.removeAttribute('data-font');
            else document.documentElement.setAttribute('data-font', f);
            try { localStorage.setItem(FONT_KEY, f); } catch (e) {}
            document.querySelectorAll('#font-tabs .set-tab').forEach(function(b){
              b.classList.toggle('active', b.dataset.font === f);
            });
          }

          /* ---------- 图片灯箱：当前对话图片集合，左右切换/下载，Esc/背景关闭 ---------- */
          var lightboxEl = null, lightboxImg = null;
          var lbList = [], lbIdx = 0, lbReturnFocus = null;
          function lbUpdateNav(){
            var many = lbList.length > 1;
            var p = document.getElementById('lb-prev'), n = document.getElementById('lb-next');
            if (p) p.disabled = !many;
            if (n) n.disabled = !many;
          }
          function lbShow(){
            var it = lbList[lbIdx];
            lightboxImg.src = it.src;
            lightboxImg.alt = it.name || '图片预览';
            lbUpdateNav();
          }
          function openLightbox(src) {
            if (!lightboxEl) lightboxEl = document.getElementById('lightbox');
            if (!lightboxImg) lightboxImg = document.getElementById('lightbox-img');
            if (!lightboxEl || !lightboxImg || !src) return;
            /* 收集当前对话全部图片（按 DOM 顺序），无图则只放当前 src 兜底 */
            lbList = [];
            msgsInner.querySelectorAll('.att-img img').forEach(function(im){
              var s = im.currentSrc || im.src;
              if (s) lbList.push({ src: s, name: im.alt || '' });
            });
            if (!lbList.length) lbList.push({ src: src, name: '' });
            lbIdx = 0;
            for (var i = 0; i < lbList.length; i++){
              if (lbList[i].src === src){ lbIdx = i; break; }
            }
            lbShow();
            lightboxEl.hidden = false;
            lbReturnFocus = document.activeElement;
            try { lightboxEl.focus(); } catch (e) {}
          }
          function lbStep(d){
            if (lbList.length < 2) return;
            lbIdx = (lbIdx + d + lbList.length) % lbList.length;
            lbShow();
          }
          function lbDownload(){
            var it = lbList[lbIdx];
            if (!it) return;
            fetch(it.src).then(function(r){ return r.blob(); }).then(function(b){
              var u = URL.createObjectURL(b);
              var nm = (it.name || 'image').replace(/[\\/:*?"<>|]+/g, '_');
              if (!/\.[a-z0-9]{2,4}$/i.test(nm)) nm += '.jpg';
              var a = document.createElement('a');
              a.href = u; a.download = nm;
              document.body.appendChild(a); a.click();
              setTimeout(function(){ URL.revokeObjectURL(u); a.remove(); }, 400);
            }).catch(function(){ toast('下载失败'); });
          }
          function closeLightbox() {
            if (!lightboxEl) lightboxEl = document.getElementById('lightbox');
            if (!lightboxEl || lightboxEl.hidden) return;
            lightboxEl.hidden = true;
            if (lightboxImg) lightboxImg.removeAttribute('src');
            var ret = lbReturnFocus; lbReturnFocus = null;
            restoreFocusTo(ret);
          }
          /* 字号初始化 + 字号监听 + 灯箱接线统一入口：#font-tabs / #lightbox 都在
             app.js script 标签之后（</main> 后），直接执行时 DOM 还不存在，
             必须等 DOMContentLoaded（与 initSettings 同一原因，注释见其上） */
          function initAppearance(){
            var f = 'std';
            try { f = localStorage.getItem(FONT_KEY) || 'std'; } catch (e) {}
            applyFont(f);
            document.querySelectorAll('#font-tabs .set-tab').forEach(function(b){
              b.addEventListener('click', function(){ applyFont(b.dataset.font); });
            });
            /* 主题三态 tabs：此时 DOM 才就绪，重新 applyTheme 同步一次激活态 */
            document.querySelectorAll('#theme-tabs .set-tab').forEach(function(b){
              b.addEventListener('click', function(){ applyTheme(b.dataset.theme); });
            });
            var t0 = 'dark';
            try { t0 = localStorage.getItem(THEME_KEY) || 'dark'; } catch (e) {}
            applyTheme(t0 === 'light' || t0 === 'auto' ? t0 : 'dark');
            lightboxEl = document.getElementById('lightbox');
            lightboxImg = document.getElementById('lightbox-img');
            if (lightboxEl) {
              /* 点背景/图片关闭；导航与下载按钮不关闭 */
              lightboxEl.addEventListener('click', function(e){
                if (e.target.closest('#lb-prev,#lb-next,#lb-download')) return;
                closeLightbox();
              });
              lightboxEl.addEventListener('keydown', function(e){
                if (lightboxEl.hidden) return;
                if (e.key === 'ArrowLeft'){ e.preventDefault(); lbStep(-1); }
                else if (e.key === 'ArrowRight'){ e.preventDefault(); lbStep(1); }
              });
              var lbPrevB = document.getElementById('lb-prev');
              var lbNextB = document.getElementById('lb-next');
              var lbDlB = document.getElementById('lb-download');
              if (lbPrevB) lbPrevB.addEventListener('click', function(){ lbStep(-1); });
              if (lbNextB) lbNextB.addEventListener('click', function(){ lbStep(1); });
              if (lbDlB) lbDlB.addEventListener('click', lbDownload);
            }
            /* 委托：附件图片点击进灯箱（阻止默认的新标签页跳转） */
            msgsInner.addEventListener('click', function(e){
              var a = e.target && e.target.closest ? e.target.closest('.att-img') : null;
              if (!a) return;
              var img = a.querySelector('img');
              var src = (img && img.currentSrc) || (img && img.src) || a.href;
              if (src) { e.preventDefault(); openLightbox(src); }
            });
          }
          if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAppearance);
          else initAppearance();

          refreshStatus();

          /* ---------- 主动问候：页面加载后按阈值触发 ---------- */
          var GREETING_KEY = 'xiaoni_greeting_enabled';
          var GREETING_DATE_KEY = 'xiaoni_last_greeting_date';
          var LAST_CHAT_KEY = 'xiaoni_last_chat_time';

          function isGreetingEnabled() {
            var v = localStorage.getItem(GREETING_KEY);
            return v === null ? true : v === '1';
          }
          function todayStr() {
            /* 本地时区日期：toISOString 是 UTC，东八区凌晨会算成前一天 */
            var d = new Date();
            return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
          }
          function shouldGreet() {
            if (!isGreetingEnabled()) return false;
            if (localStorage.getItem(GREETING_DATE_KEY) === todayStr()) return false;
            var lastChat = parseInt(localStorage.getItem(LAST_CHAT_KEY) || '0', 10);
            if (lastChat && (Date.now() - lastChat < 2 * 3600 * 1000)) return false;
            return true;
          }
          async function maybeGreet() {
            /* 整段包 try：隐私模式/localStorage 禁用时 shouldGreet 内访问 localStorage
               会抛异常，旧实现是 setTimeout 里的 unhandled rejection */
            try {
              if (!shouldGreet()) return;
              var sessionId = currentSessionId;
              var r = await api('/api/greeting', { method: 'POST' });
              if (r.reply && r.reply.trim()) {
                try { localStorage.setItem(GREETING_DATE_KEY, todayStr()); } catch (e) {}
                setTimeout(function() {
                  /* 等待期间用户可能已切会话：问候只进发起时的会话，不插错视图 */
                  if (currentSessionId !== sessionId) return;
                  /* 用户恰在等待窗口里发了消息：放弃本次问候，避免顺序变成
                     用户消息→问候→真实回复（问候不重试，明天的问候照常触发） */
                  if ((state.sendingCount || 0) > 0 || state.awaiting[sessionId]) return;
                  var s = findSession(sessionId);
                  if (!s) return;
                  /* 问候入场：移除剧情引导卡，让位给真实对话 */
                  var _g = msgsInner.querySelector('.msg-guide');
                  if (_g) _g.remove();
                  var gMsg = { role: 'assistant', content: r.reply, style: r.style || '', narration: r.narration || '' };
                  /* 旁白双声部：先渲染灰色旁白气泡，再渲染大帅的问候气泡 */
                  if (r.narration) addMsg('narration', r.narration);
                  var div = addMsg('assistant', r.reply);
                  var meta = div.querySelector('.msg-meta');
                  var spk = null;
                  if (meta) {
                    spk = makeSpeakBtn(r.reply, r.style, gMsg, sessionId);
                    meta.appendChild(spk);
                    meta.appendChild(makeResynthBtn(r.reply, r.style, gMsg, sessionId));
                    meta.appendChild(makeRegenBtn(div));
                  }
                  /* 入库持久化：刷新不丢、右键可删、后续对话有上下文 */
                  s.history.push(gMsg);
                  div.dataset.hidx = String(s.history.length - 1);
                  markSessionActivity(sessionId);
                  if (state.soundOn && spk) ttsAndPlay(r.reply, spk, r.style, false, gMsg, sessionId).catch(function(){});
                }, 1500);
              }
            } catch(e) { /* 静默失败，不影响使用 */ }
          }
          setTimeout(maybeGreet, 2000);

          /* ===== PWA：安装入口 + Service Worker（网页端接管已停更的安卓 APK） ===== */
          (function pwa(){
            var deferredPrompt = null;
            var installBtn = document.getElementById('btn-install');
            var standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
            window.addEventListener('beforeinstallprompt', function(e){
              e.preventDefault();
              deferredPrompt = e;
              if (installBtn && !standalone) installBtn.classList.add('show');
            });
            if (installBtn) installBtn.addEventListener('click', function(){
              if (!deferredPrompt) return;
              installBtn.classList.remove('show');
              deferredPrompt.prompt();
              deferredPrompt.userChoice.then(function(){ deferredPrompt = null; });
            });
            window.addEventListener('appinstalled', function(){
              if (installBtn) installBtn.classList.remove('show');
              toast('已添加到主屏幕，可像 App 一样使用');
            });
            /* iOS Safari 无安装事件：一次性提示手动添加到主屏幕 */
            try {
              if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone && !localStorage.getItem('xiaoni_ios_pwa_hint')) {
                localStorage.setItem('xiaoni_ios_pwa_hint', '1');
                setTimeout(function(){ toast('Safari 分享菜单 →「添加到主屏幕」，即可像 App 一样使用', 6000); }, 2500);
              }
            } catch(e) { /* 隐私模式下 localStorage 不可用，忽略 */ }
            /* Service Worker：应用壳缓存，秒开 + 弱网可用。
               updateViaCache:'none' —— sw.js 本体绝不走 HTTP 缓存，保证服务端一发新版
               （CACHE 版本号变更）本次加载就能检测到并激活，不让设备卡在旧壳上 */
            if ('serviceWorker' in navigator) {
              /* 新 SW 接管（skipWaiting+claim）后自动刷新一次：否则当前标签仍持有旧
                 HTML 文档，子资源却可能已是新缓存，出现「新 JS + 旧 HTML」错配白屏。
                 仅在页面无生成中请求时刷新，避免打断对话；有请求则等下次加载 */
              var swRefreshing = false;
              /* 注册前已有 controller = 升级；首次安装（null）触发的 controllerchange
                 不需要刷新（页面本就是网络最新版） */
              var swHadController = !!navigator.serviceWorker.controller;
              navigator.serviceWorker.addEventListener('controllerchange', function(){
                if (!swHadController) { swHadController = true; return; }
                if (swRefreshing || (state.sendingCount || 0) > 0) return;
                swRefreshing = true;
                try { window.location.reload(); } catch (e) {}
              });
              window.addEventListener('load', function(){
                navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
                  .catch(function(){ /* 注册失败不影响聊天 */ });
              });
            }
          })();
        })();
