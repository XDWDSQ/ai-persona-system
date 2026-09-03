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
          var state = { history: [], cloudProviders: {}, soundOn: (function(){ try { return localStorage.getItem('xiaoni_sound_on') !== '0'; } catch(e) { return true; } })(), speed: (function(){ try { return parseFloat(localStorage.getItem('xiaoni_speed') || '1.0') || 1.0; } catch(e) { return 1.0; } })(), provider: 'cloud', voiceProvider: 'local', aliyunConfigured: false, minimaxConfigured: false, audioCache: {}, ttsInflight: {}, playing: null, playingBtn: null, playSeq: 0, pendingAttachments: [], attachMenuOpen: false, awaiting: {} };
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
            grow();
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
          /* 返回 Promise：resolve(true) 确认 / resolve(false) 取消。message 为文本时自动转义。 */
          function confirmDialog(message, opts) {
            if (!_cfReady()) return Promise.resolve(window.confirm(message || ''));
            opts = opts || {};
            return new Promise(function(resolve){
              var onOk = function(){ close(); resolve(true); };
              var onCancel = function(){ close(); resolve(false); };
              var onKey = function(e){ if (e.key === 'Escape') { onCancel(); } };
              function close() {
                _cfModal.classList.remove('show');
                _cfOk.removeEventListener('click', onOk);
                _cfCancel.removeEventListener('click', onCancel);
                _cfModal.removeEventListener('click', onOverlay);
                document.removeEventListener('keydown', onKey);
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
          async function api(path, opt) {
            var r = await fetch(path, opt);
            if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
            if (!r.ok) {
              var m = '请求失败(' + r.status + ')';
              try { m = (await r.json()).detail || m; } catch (e) {}
              throw new Error(m);
            }
            return r.status === 200 ? r.json() : null;
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
             返回 {clean, style, searched, vision_used}；中途出错 throw。 */
          async function chatStreamRequest(payload, onDelta) {
            var resp = await fetch('/api/chat', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(Object.assign({ stream: true }, payload)),
            });
            if (!resp.ok) {
              if (resp.status === 401) { location.href = '/login'; }
              var m = '请求失败(' + resp.status + ')';
              try { m = (await resp.json()).detail || m; } catch (e) {}
              throw new Error(m);
            }
            var reader = resp.body.getReader();
            var dec = new TextDecoder();
            var buf = '';
            var fullRaw = '';
            var out = null;
            for (;;) {
              var r = await reader.read();
              if (r.done) break;
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
          async function streamReplyInto(sessionId, snap, payload) {
            var s = findSession(sessionId);
            var div = null;
            var started = false;
            var fullRaw = '';
            var clean = '', style = '';
            var bubble = null;    /* 缓存气泡引用，避免每帧 querySelector */
            var renderer = null;  /* rAF 帧合并 + 增量渲染器 */
            try {
              var res = await chatStreamRequest(payload, function(fullText){
                fullRaw = fullText;
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
                  renderer(fullText); /* 帧合并 + 增量追加，渲染频率锁到屏幕刷新率 */
                }
              });
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
              var aiMsg = { role: 'assistant', content: clean, style: style, narration: res.narration || '' };
              s.history.push(aiMsg);
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
                  /* 以 clean 为准：剥净残留 style/元话语标记；走 fillBubbleText 保留
                     APK 点字跳播的 .tts-seg 分段（直接 textContent 会销毁分段） */
                  fillBubbleText(b2, clean);
                  b2.classList.remove('no-text');
                }
                var meta = div.querySelector('.msg-meta');
                var spk = makeSpeakBtn(clean, style, aiMsg, sessionId);
                meta.appendChild(spk);
                meta.appendChild(makeResynthBtn(clean, style, aiMsg, sessionId));
                meta.appendChild(makeRegenBtn(div));
                if (storyEvText) addMsg('event', storyEvText); /* 分隔线跟在回复后面，像系统结算 */
                if (res.story_update) refreshStoryEntry(res.story_update);
                if (state.soundOn) ttsAndPlay(clean, spk, style, false, aiMsg, sessionId);
              }
            } catch (e) {
              hideTyping();
              /* 流中断但已流出内容（如收尾断连/部署重启/隧道抖动）：把已生成内容入库，
                 否则重进后历史以用户消息结尾，误报"上次回复没有生成成功"。
                 没有任何内容才落失败气泡（可重试）。 */
              var kept = clean || stripStyleTag(fullRaw);
              if (kept && kept.trim()) {
                var f2 = findSession(sessionId);
                if (f2) {
                  f2.history.push({ role: 'assistant', content: kept, style: style || '' });
                  markSessionActivity(sessionId);
                  if (div && div.querySelector('.bubble')) {
                    fillBubbleText(div.querySelector('.bubble'), kept);
                    div.classList.remove('no-text');
                  }
                  toast('网络中断，已保留已生成的内容');
                } else if (div && div.parentNode) {
                  div.remove();
                  addFailureMsg(sessionId, '会话已被删除', snap);
                }
              } else {
                if (div && div.parentNode) div.remove(); /* 流中断：删掉半成品，落失败气泡 */
                addFailureMsg(sessionId, (e && e.message) || '生成失败，请重试', snap);
              }
            }
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
            function flush() {
              rafId = 0;
              if (pending === null) return;
              var raw = pending; pending = null;
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
              /* 收尾竞态防护：done 处理会绕过渲染器直接写整段文本（b2.textContent = clean），
                 此时若还有排队的 rAF flush，按 base 追加增量会把末段重复一遍
                 （delta 与 done 同帧到达时必现）。气泡已等于目标文本 → 只同步 base。 */
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
            return function(fullRaw) {
              pending = fullRaw;
              if (!rafId) rafId = requestAnimationFrame(flush);
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
              var gv = localStorage.getItem('xiaoni_greeting_enabled');
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
            document.querySelectorAll('.p-dot, .m-dot, .c-dot').forEach(function(d){
              d.classList.remove('online', 'offline');
              d.classList.add('checking');
            });
          }
          function applyOnlineState(online) {
            document.querySelectorAll('.m-dot, .c-dot').forEach(function(d){
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
              state.voiceRegistered = !!s.voice_registered;
              state.cloudProviders = s.cloud_providers || {};
              state.voiceManualProvider = !!s.voice_manual_provider;
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
              if (pd) pd.textContent = '—';
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

          /* ---------- 消息渲染 ---------- */
          function aiAvatarHtml() {
            if (currentRoleKey === 'dashuai') return '<img src="avatar_dashuai_64.webp" alt="大帅" width="64" height="64" decoding="async">';
            var r = (state.roles || []).find(function(x){ return x.key === currentRoleKey; });
            return String((r && r.name ? r.name : 'AI').slice(0, 1))
              .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
              .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
          }
          /* 贴底跟随：用户手动上翻时暂停跟随；贴底状态下内容高度变化自动补滚 */
          var stickBottom = true;
          function scrollBottom(instant){
            var m = document.getElementById('msgs');
            if (!m) return;
            stickBottom = true;
            /* instant 场景（切会话/补滚）必须瞬时到位，不能走平滑动画，否则迟滞 */
            if (instant) { snapBottom(); return; }
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
            jump.addEventListener('click', function(){ scrollBottom(); });
            (m.closest('.main') || document.body).appendChild(jump);
            m.addEventListener('scroll', function(){
              stickBottom = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
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
            if (text && role === 'assistant') {
              /* APK 播放中对齐：assistant 消息文字按句切块，点击跳转对应音频位置 */
              fillBubbleText(bubble, text);
            } else if (text) {
              bubble.textContent = text;
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
            if (!isNaN(idx) && state.history[idx] && state.history[idx].role === role) {
              state.history.splice(idx, 1);
              saveSessions();
              renderCurrentSession();
              return;
            }
            var content = div.querySelector('.bubble').textContent;
            for (var i = 0; i < state.history.length; i++) {
              if (state.history[i].role === role && state.history[i].content === content) {
                state.history.splice(i, 1);
                saveSessions();
                /* 必须重渲染：其余消息 DOM 的 dataset.hidx 已失效，不重渲染会导致
                   下次按 hidx 删除时下标错位误删另一条同角色消息 */
                renderCurrentSession();
                return;
              }
            }
          }
          /* 点击其他区域 / 右键非消息处 / 滚动 / 窗口缩放 -> 关闭菜单 */
          document.addEventListener('click', hideCtxMenu);
          document.addEventListener('contextmenu', function(e){
            if (!(e.target.closest && e.target.closest('.msg, .session'))) hideCtxMenu();
          });
          document.addEventListener('scroll', hideCtxMenu, true);
          window.addEventListener('resize', hideCtxMenu);

          /* ---------- 修改消息 ----------
             右键/长按自己发的文本消息 → 就地编辑：更新文本 → 截断其后所有消息（上下文已变，
             旧回复作废，与主流产品一致）→ 自动重新生成回复。 */
          function editMessage(div) {
            var s = currentSession();
            if (!s) return;
            if (div.querySelector('.edit-wrap')) return; /* 已在编辑中，防重复插入 */
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
            b.onclick = function(){ ttsAndPlay(text, b, style, false, msg, sid); };
            /* 循环播放开关：开启后该条音频播完自动从头再播 */
            var lb = document.createElement('button');
            lb.type = 'button';
            lb.className = 'loop-btn';
            lb.title = '循环播放';
            lb.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/></svg>';
            lb.onclick = function(ev) {
              if (ev) { ev.preventDefault(); ev.stopPropagation(); }
              b._loop = !b._loop;
              lb.classList.toggle('on', !!b._loop);
              lb.setAttribute('aria-pressed', b._loop ? 'true' : 'false');
              lb.title = b._loop ? '已开启循环播放' : '循环播放';
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
            b.onclick = function(){ ttsAndPlay(text, b, style, true, msg, sid); };
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
            // 语速由 Audio.playbackRate 控制，不再作为 cache key — 同一句不同语速复用同一份音频
            var key = (style || '') + '\u0001' + text;
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
                  try {
                    var gr = await fetch(msg.audio);
                    if (gr.ok) {
                      var gu = URL.createObjectURL(await gr.blob());
                      if (state.audioCache[key]) URL.revokeObjectURL(state.audioCache[key]);
                      state.audioCache[key] = gu;
                      url = gu;
                    }
                  } catch (e) { /* 落到下面的合成兜底 */ }
                }
                if (!url || force) {
                  if (!force && state.ttsInflight[key]) {
                    // 同一句已有合成请求在飞（自动朗读+手动点朗读撞车），直接等它结果，不重复请求
                    url = await state.ttsInflight[key];
                  } else {
                    var job = (async function(){
                      var r = await fetch('/api/tts', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: text, style: style || '', speed: state.speed, force: force }),
                      });
                      if (r.status === 401) { location.href = '/login'; throw new Error('未登录'); }
                      if (!r.ok) throw new Error('TTS 失败');
                      // 把稳定可回放/下载的音频 URL 持久化进消息，随对话一起保存
                      var cacheHash = r.headers.get('X-TTS-Cache');
                      if (cacheHash && msg) {
                        msg.audio = '/api/tts/file?h=' + cacheHash;
                        try { if (sid) markSessionActivity(sid); saveSessions(); } catch (e) {}
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
                    try { url = await job; } finally { if (!force) delete state.ttsInflight[key]; }
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
             用计数而非布尔，允许同一会话并发生成（发送 + 重新生成）互不干扰 */
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
          async function uploadPendingAttachments() {
            var out = [];
            var jobs = [];
            for (var i = 0; i < state.pendingAttachments.length; i++) {
              var p = state.pendingAttachments[i];
              var fd = new FormData();
              fd.append('files', p.file, p.name);
              /* 并行上传：串行时多文件耗时线性叠加；后端每请求独立写盘，可安全并发 */
              jobs.push(api('/api/upload', { method: 'POST', body: fd }).then(function(r){
                return (r && r.files && r.files.length) ? r.files[0] : null;
              }));
            }
            var results = await Promise.all(jobs);
            results.forEach(function(x){ if (x) out.push(x); });
            return out;
          }
          function friendlySendError(e) {
            var m = (e && e.message) || '';
            if (/405|404|Method Not Allowed|Not Found/.test(m)) return '后端版本太旧，请先重启服务再发送附件';
            return m;
          }

          /* ---------- 对话 ---------- */
          async function send(text) {
            text = (text || '').trim();
            var sendBtn = document.querySelector('.send-btn');
            var pending = state.pendingAttachments.slice();
            if ((!text && !pending.length) || sendBtn.disabled) return;
            toggleAttachMenu(false);
            var sessionId = currentSessionId;
            var s = findSession(sessionId);
            if (!s) return;
            sendBtn.disabled = true;
            sendBtn.classList.add('loading');
            stickBottom = true; /* 自己发消息：强制恢复贴底跟随 */
            var atts = [];
            try {
              if (pending.length) {
                atts = await uploadPendingAttachments();
                if (!atts.length) throw new Error('附件上传失败，请重试');
              }
              var userDiv = addMsg('user', text, { attachments: atts });
              s.history.push({ role: 'user', content: text, attachments: atts });
              userDiv.dataset.hidx = String(s.history.length - 1);
              /* 记录本轮用户消息的定位信息：生成失败时用它挂「重试」按钮 */
              var snap = { idx: s.history.length - 1, text: text, atts: atts };
              markSessionActivity(sessionId, text || (atts[0] && atts[0].name));
              clearPendingAttachments();
              try { localStorage.setItem('xiaoni_last_chat_time', Date.now().toString()); } catch(e) {}
              showTyping();
              markAwaiting(sessionId, true);
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
                /* 消息还没进会话（如附件上传失败）：还原输入文本，方便原样重发 */
                var t2 = document.getElementById('text');
                if (t2 && text && !t2.value) { t2.value = text; t2.dispatchEvent(new Event('input')); }
              }
            } finally {
              sendBtn.classList.remove('loading');
              sendBtn.disabled = false;
            }
          }

          /* ---------- 设置 ---------- */
          /* 模态框 HTML 在 </main> 之后，需等 DOMContentLoaded 才能绑定 */
          function openSettings(){
            var el = document.getElementById('settings'); if (el) { el.classList.add('show'); refreshStatus(); refreshWeather(); refreshRoleNews(); refreshMemories(); }
            var hint = document.getElementById('server-url-hint');
            if (hint) {
              var cur = (window.AndroidBridge && window.AndroidBridge.getServerUrl) ? window.AndroidBridge.getServerUrl() : '';
              hint.textContent = cur ? cur : (window.AndroidBridge ? '未配置' : '网页版（连接本机）');
            }
          }
          function closeSettings(){
            var el = document.getElementById('settings'); if (!el) return;
            el.classList.remove('show');
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
                    voice_provider: document.querySelector('#voice-tabs .set-tab.active').dataset.voice,
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
                if (gt) localStorage.setItem('xiaoni_greeting_enabled', gt.checked ? '1' : '0');
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
                var r = await api('/api/llm-models', {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    base_url: document.getElementById('cloud_base_url').value,
                    api_key: document.getElementById('cloud_api_key').value,
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
              b.addEventListener('click', function(){ pickVoiceProvider(b.dataset.voice); });
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
            var t = document.getElementById('text');
            var v = t.value; t.value = ''; t.style.height = 'auto';
            send(v);
          });
          if (ta) ta.addEventListener('keydown', function(e){
            /* 中文输入法合成期按 Enter 是"确认候选词"不是发送：isComposing 与
               keyCode 229 双守卫（部分 IME 只暴露其中一种信号） */
            if (e.isComposing || e.keyCode === 229) return;
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (sendBtn) sendBtn.click(); }
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
            try { localStorage.setItem(TOMBSTONE_KEY, JSON.stringify(list.slice(-500))); } catch (e) {}
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
            /* 存储为空或只剩垃圾记录时，自动建一个干净的新对话，保证页面始终有当前会话 */
            if (!list.length) list.push(makeSession('新对话'));
            return list;
          }
          var sessions = loadSessions();
          var currentSessionId = sessions.length ? sessions[0].id : null;
          var saveTimer = null;
          function persistLocal() {
            try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions)); } catch (e) {}
            try { localStorage.setItem(ACTIVE_KEY, currentSessionId || ''); } catch (e) {}
          }
          function saveSessions(immediate) {
            persistLocal();
            clearTimeout(saveTimer);
            var doSave = function(){
              try {
                fetch('/api/sessions?client=' + encodeURIComponent(CLIENT_ID), {
                  method: 'PUT', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ sessions: sessions, deleted: loadTombstones() }),
                  keepalive: true, /* 页面关闭时的刷盘请求也能发出去 */
                }).then(function(r){
                    if (r && r.status === 401) { location.href = '/login'; return; }
                    if (r && !r.ok) throw new Error('HTTP ' + r.status);
                }).catch(function(e){
                    /* 保存失败必须可见：服务挂了/网络断时若静默吞错，用户会误以为
                       已保存，重开页面/换设备后聊天记录就"消失"了。限频提示避免刷屏。 */
                    if (!saveSessions._lastErrAt || Date.now() - saveSessions._lastErrAt > 10000) {
                      saveSessions._lastErrAt = Date.now();
                      try { toast('记录保存失败，仅存本机：' + ((e && e.message) || '网络异常')); } catch (_) {}
                    }
                });
              } catch (e) {}
            };
            if (immediate) doSave();
            else saveTimer = setTimeout(doSave, 300);
          }
          /* 关页/刷新前兜底刷盘，避免 300ms 防抖窗口内的消息丢失 */
          window.addEventListener('pagehide', function(){ saveSessions(true); });
          function findSession(id) {
            for (var i = 0; i < sessions.length; i++) {
              if (sessions[i].id === id) return sessions[i];
            }
            return null;
          }
          function currentSession() {
            return findSession(currentSessionId) || ensureMainSession();
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
          function markSessionActivity(id, firstUserText) {
            var s = findSession(id);
            if (!s) return;
            if (firstUserText && s.history.length === 1 && !s.manualTitle) {
              s.title = firstUserText.length > 12 ? firstUserText.slice(0, 12) + '…' : firstUserText;
            }
            s.updatedAt = Date.now();
            saveSessions(); /* 防抖：连续收发消息时不要每条都 PUT 服务端 */
            renderSessions();
            if (id === currentSessionId) updateChatTitle();
          }
          /* 轻量指纹：同步合并的变更检测用，避免每次同步全量深比较（会话可能有数万字） */
          function sessionFingerprint(s) {
            var last = (s.history && s.history.length) ? s.history[s.history.length - 1] : null;
            return [s.updatedAt || 0, s.history ? s.history.length : 0, s.title || '',
              s.pinned ? 1 : 0, s.manualTitle ? 1 : 0,
              last ? ((last.content || '').length + '|' + (last.role || '')) : '-'].join('~');
          }
          /* 竞态防御：shorter 是否严格是 longer 的消息序列前缀（逐条 role+content 一致）。
             多端同会话并发写/时钟偏移时，持旧快照的一端 updatedAt 可能更新但消息更少，
             若允许它覆盖另一端，会把刚保存的消息整段抹掉（与后端 _merge_sessions 同一规则） */
          function isPrefixSeq(shorter, longer) {
            if (!shorter || !shorter.length || !longer) return false;
            if (shorter.length >= longer.length) return false;
            for (var i = 0; i < shorter.length; i++) {
              var a = shorter[i], b = longer[i];
              if (!a || !b) return false;
              if (a.role !== b.role || (a.content || '') !== (b.content || '')) return false;
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
                if (a && b && a.role === b.role && (a.content || '') === (b.content || '')) { found = true; break; }
              }
              if (!found) return false;
            }
            return true;
          }
          /* 消息级合并：保留 base 顺序，extra 中不在 base 里的消息按原顺序追加（去重）。 */
          function unionHistory(base, extra) {
            var seen = {}, out = (base || []).slice();
            (base || []).forEach(function(m){ seen[m.role + '\u0000' + (m.content || '')] = true; });
            (extra || []).forEach(function(m){
              var k = m.role + '\u0000' + (m.content || '');
              if (!seen[k]) { seen[k] = true; out.push(m); }
            });
            return out;
          }
          var _syncRetry = 0;
          function syncSessionsFromServer() {
            fetch('/api/sessions').then(function(r){
              if (r.status === 401) throw new Error('未登录');  /* 不跳页：首启登录可能未就绪，走重试 */
              return r.ok ? r.json() : null;
            }).then(function(data){
              if (!data || !Array.isArray(data.sessions)) return;
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
                  /* 分歧：两端各有对方没有的消息（并发各发各话）→ 消息级合并双方都不丢 */
                  var diverge = !localStale && lh.length !== rh.length
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
                      manualTitle: !!(localNewer ? l : r).manualTitle
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
              /* 墓碑维护：30 天过期；对应会话 updatedAt 已新于墓碑视为复活，移除墓碑 */
              var nowMs = Date.now();
              var newTomb = [];
              Object.keys(tombMap).forEach(function(id){
                var ts = tombMap[id];
                if (nowMs - ts > 30 * 86400000) return;
                for (var i = 0; i < merged.length; i++) {
                  if (merged[i].id === id && (merged[i].updatedAt || 0) > ts) return;
                }
                newTomb.push({ id: id, ts: ts });
              });
              saveTombstones(newTomb);

              /* 与远端完全一致：不渲染也不回传，避免两端互相 PUT 乒乓 */
              if (!changed && merged.length === sessions.length) return;

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
                renderCurrentSession();
              }
              /* 合并结果回传服务端，让本地独有的会话/消息在另一端也可见；
                 服务端广播会跳过本设备，不会触发自己再同步 */
              saveSessions();
              _syncRetry = 0;  /* 同步成功，重置重试计数 */
            }).catch(function(){
              /* 失败重试：重装/首启时登录可能尚未就绪（预热登录/代理自动重登进行中），
                 最多重试 3 次，避免会话历史静默丢失（本地只剩"新对话"） */
              if (_syncRetry < 3) {
                _syncRetry++;
                setTimeout(function(){ syncSessionsFromServer(); }, 2000);
              }
            });
          }
          /* ---------- 多端实时同步：SSE 推送 + 可见性/定时兜底 ---------- */
          function isSyncBusy() {
            /* 生成中/朗读中不执行合并：防止会话对象被中途替换导致
               进行中的消息写入旧引用，也避免重渲染打断用户 */
            return !!document.getElementById('typing') || !!state.playing;
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
          function startSyncStream() {
            if (!window.EventSource) return;
            try {
              var es = new EventSource('/api/sync/stream?client=' + encodeURIComponent(CLIENT_ID));
              es.onmessage = function(e){
                var ev = null;
                try { ev = JSON.parse(e.data); } catch (err) {}
                if (ev && ev.type === 'sessions_updated') requestRemoteSync();
              };
              /* 断线由 EventSource 自动重连（服务端 retry: 3000）；
                 重连失败期间的变更由下方可见性/定时轮询兜底补齐 */
              /* 页面卸载/后台冻结时主动断开，避免服务端挂着死连接 */
              window.addEventListener('pagehide', function(){ try { es.close(); } catch (e) {} });
            } catch (e) {}
          }
          startSyncStream();
          /* 兜底：切回前台立即补齐后台期间错过的变更；页面可见时每 30s 拉一次（服务端 mtime 缓存，代价极低） */
          document.addEventListener('visibilitychange', function(){
            if (document.visibilityState === 'visible') requestRemoteSync();
          });
          setInterval(function(){
            if (document.visibilityState === 'visible') requestRemoteSync();
          }, 30000);
          /* 入场动画只播一次：收发消息触发的重渲染不要重放动画 */
          var sessionsAnimatedOnce = false;
          /* 会话列表渲染去重：markSessionActivity 在每次收发消息都会触发 renderSessions，
             但排序/置顶/标题/活跃时间/选中态都没变时 DOM 无需重建。
             用轻量指纹串比对（不依赖深比较），不变直接跳过 → 连续收发消息不再反复重建列表 */
          var sessionsRenderKey = '';
          /* ---- 主对话模式：每角色一个固定主对话（id=main_{role}），禁新建、不切换 ----
             首次进入某角色时，若存在旧会话，取最新一个的内容迁移进主对话（旧数据保留不显示）。 */
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
             不被周期性状态刷新拽回 */
          var _lastSyncedRole = null;
          function syncSessionForRole() {
            var main = ensureMainSession();
            if (_lastSyncedRole !== currentRoleKey) {
              _lastSyncedRole = currentRoleKey;
              currentSessionId = main.id;
              renderCurrentSession();
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
            var key = ordered.map(function(x){
              return x.id + '|' + (x.pinned ? 1 : 0) + '|' + (x.title || '') + '|' + (x.updatedAt || 0) + '|' + (x.id === currentSessionId ? 1 : 0);
            }).join('~');
            if (key === sessionsRenderKey && sessionsBox.childNodes.length) return;
            sessionsRenderKey = key;
            sessionsBox.innerHTML = '';
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
              /* 主对话模式：不绑定右键/长按菜单（禁删除/重命名/置顶主对话） */
              frag.appendChild(s);
            });
            sessionsBox.appendChild(frag);
            sessionsAnimatedOnce = true;
          }
          function renderCurrentSession() {
            stopAudio();
            state.playSeq++; /* 切换会话：作废所有在飞/挂起的朗读请求，防止旧结果晚到抢播 */
            hideTyping();
            /* 连 .msg-guide（2027 剧情引导卡）一起清：它的类名不含 .msg，
               漏清会残留，切回时叠加一张新卡 */
            msgsInner.querySelectorAll('.msg, .msg-guide').forEach(function(m){ m.remove(); });
            var s = currentSession();
            state.history = s ? s.history : [];
            /* 有历史消息时收起欢迎区：landing 状态与聊天记录不该同屏 */
            var welcome = document.querySelector('.welcome');
            if (welcome) welcome.style.display = state.history.length ? 'none' : '';
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
            scrollBottom(true);
          }
          function switchSession(id) {
            if (id === currentSessionId) return;
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
          /* Esc 统一收起浮层：抽屉 / 设置弹窗 / 附件菜单 / 右键菜单 */
          document.addEventListener('keydown', function(e){
            if (e.key !== 'Escape') return;
            closeDrawer();
            closeSettings();
            toggleAttachMenu(false);
            hideCtxMenu();
          });
          /* 点击抽屉内条目（会话/角色/新对话/引擎切换/导航）后自动收起；
             重命名输入框(.renaming)除外，否则打字时抽屉会被关掉 */
          if (sideEl) sideEl.addEventListener('click', function(e){
            if (!isMobileLayout()) return;
            var t = (e.target.closest) ? e.target.closest('.session, .role-item, [data-dom-id="btn-new"], .side-link, .provider-row') : null;
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
          /* 会话菜单项：桌面右键与触屏长按共用同一份 */
          function sessionMenuItems(sess){
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
                stopAudio();
                state.playSeq++; /* 删除会话：作废所有在飞/挂起的朗读请求 */
                addTombstone(sess.id); /* 墓碑：其他设备同步时不得复活该会话 */
                sessions = sessions.filter(function(x){ return x.id !== sess.id; });
                if (wasActive) {
                  var next = sortSessions(sessions)[0];
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
            var s = makeSession('新对话');
            sessions.unshift(s);
            currentSessionId = s.id;
            renderSessions();
            renderCurrentSession();
            /* 占位会话不落库：此时没有消息，saveSessions 会把空「新对话」同步到
               服务端并经并集合并下发到每台设备（会话列表被淹没的根源）。
               发出首条消息时 markSessionActivity → saveSessions 才真正持久化。 */
          }
          var newBtn = document.querySelector('[data-dom-id="btn-new"]');
          if (newBtn) {
            /* 主对话模式：每角色一个主对话，禁新建（按钮隐藏 + 事件拦截双保险） */
            newBtn.style.display = 'none';
            newBtn.addEventListener('click', function(e){
              e.preventDefault();
              e.stopPropagation();
              toast('主对话模式：所有消息都在与当前角色的主对话里');
            });
          }
          var savedActive = localStorage.getItem(ACTIVE_KEY);
          /* 主对话模式：忽略历史保存的会话 id，统一指向当前角色主对话（服务端真实角色由 refreshStatus 同步校正） */
          ensureMainSession();
          currentSessionId = mainSessionId();
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
            var initCollapsed = localStorage.getItem('role_collapsed') === '1';
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
                localStorage.setItem('role_collapsed', '1');
              } else {
                roleListEl.classList.remove('collapsed');
                roleListEl.style.maxHeight = roleListEl.scrollHeight + 'px';
                // after transition, set maxHeight to none to allow dynamic content
                setTimeout(function(){
                  if (!roleLabel.classList.contains('collapsed')) roleListEl.style.maxHeight = 'none';
                }, 380);
                localStorage.setItem('role_collapsed', '0');
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

          /* ---------- Prompt chips stagger ---------- */
          (function(){
            var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduce) return;
            document.querySelectorAll('.prompt-chip').forEach(function(c, i){
              c.style.transitionDelay = (i * 60).toFixed(0) + 'ms';
            });
          })();

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
                  if (state.soundOn && spk) ttsAndPlay(r.reply, spk, r.style, false, gMsg, sessionId);
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
              window.addEventListener('load', function(){
                navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
                  .catch(function(){ /* 注册失败不影响聊天 */ });
              });
            }
          })();
        })();
