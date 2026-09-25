/* QA 全链路验证：真实驱动 发送→流式渲染→重新生成→编辑重发→失败保留→空回复重试→IME 守卫
 * 用法：先 `python qa_run.py` 起隔离实例，再 `node qa_flow.js`。
 * 依赖桩 /api/chat（见 qa_run.py），不花钱、不碰真实数据。 */
const { execFile } = require('child_process');
const fs = require('fs');
const http = require('http');
/* 浏览器路径可配：旧实现硬编码 C:/Program Files/Google/Chrome/...，换一台机器
   （Chrome 装在用户目录 / 用 agent-browser 自带的 chrome）整条 QA 直接起不来。
   优先级：CHROME 环境变量 → 常见安装路径。 */
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:8010';
const OUT = '_qa_shots';
const PORT = 9300 + Math.floor(Math.random() * 90);
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function httpGetJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(JSON.parse(d))); }).on('error', rej);
  });
}
let PASS = 0, FAIL = 0;
function check(name, cond, detail) {
  if (cond) PASS++; else FAIL++;
  console.log('[' + (cond ? 'PASS' : 'FAIL') + '] ' + name + (cond ? '' : '  <- ' + detail));
}

(async () => {
  const { execSync, spawn } = require('child_process');
  /* 清理上次运行遗留的 QA 孤儿 Chrome（按命令行标记精确匹配，不影响用户浏览器） */
  try {
    const { execSync } = require('child_process');
    const ps1 = process.env.TMP + '/qa_kill_orphans.ps1';
    fs.writeFileSync(ps1, "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*chrome_qa*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }");
    execSync('powershell -NoProfile -ExecutionPolicy Bypass -File "' + ps1 + '"', { stdio: 'ignore' });
  } catch (e) { /* 没有孤儿或 powershell 失败，继续 */ }

  /* 重启隔离服务：上一轮测试 PUT 进临时 sessions 的数据必须清掉，否则会被
     当作"其他设备的旧数据"合并进本轮（服务器临时目录随进程存活） */
  try {
    const { execSync, spawn } = require('child_process');
    execSync('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"', { stdio: 'ignore' });
  } catch (e) {}
  /* 解释器探测顺序与 _find_python.bat 保持一致：PYTHON 环境变量 → 项目 venv →
     项目 .venv → PATH 上的 python。旧实现硬编码
     %USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe（原开发机的本地 TTS 环境），
     换机后这条 QA 必然起不来，而且报错与真实原因无关。 */
  const PY = (function () {
    const cands = [process.env.PYTHON,
                   __dirname + '/venv/Scripts/python.exe',
                   __dirname + '/.venv/Scripts/python.exe'];
    for (const c of cands) { if (c && fs.existsSync(c)) return c; }
    return 'python';
  })();
  const srv = spawn(PY, ['qa_run.py'], { cwd: __dirname, stdio: 'ignore' });
  {
    const t0 = Date.now();
    let up = false;
    while (Date.now() - t0 < 20000 && !up) {
      try { await new Promise((res, rej) => { http.get(BASE + '/api/health', r => { r.resume(); r.statusCode === 200 ? res() : rej(new Error()); }).on('error', rej); }); up = true; } catch (e) { await sleep(500); }
    }
    console.log('QA server restarted:', up);
  }

  fs.mkdirSync(OUT, { recursive: true });
  const chrome = execFile(CHROME, ['--headless=new', '--remote-debugging-port=' + PORT,
    '--user-data-dir=' + process.env.TMP + '/chrome_qa_flow_' + Date.now(), '--no-first-run',
    '--autoplay-policy=no-user-gesture-required', '--window-size=1440,900', 'about:blank']);
  await sleep(2500);
  const targets = await httpGetJson('http://127.0.0.1:' + PORT + '/json');
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const apiLog = [];
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
    if (m.method === 'Network.responseReceived' && m.params && m.params.response) {
      const u = m.params.response.url;
      const method = (m.params.request && m.params.request.method) || (m.params.type === 'Preflight' ? 'OPTIONS' : 'GET');
      if (u.includes('/api/')) apiLog.push(m.params.response.status + ' ' + method + ' ' + u.replace(BASE, ''));
    }
  });
  const cdp = {
    send: (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); }),
    eval: async (expr) => {
      const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error('page eval: ' + ((r.exceptionDetails.exception || {}).description || r.exceptionDetails.text));
      return r.result && r.result.value;
    },
  };
  await cdp.send('Runtime.enable'); await cdp.send('Network.enable'); await cdp.send('Page.enable');

  async function waitFor(expr, timeout, pollMs) {
    const t0 = Date.now();
    for (;;) {
      if (await cdp.eval(expr)) return true;
      if (Date.now() - t0 > (timeout || 6000)) return false;
      await sleep(pollMs || 150);
    }
  }
  async function typeAndSend(text) {
    await cdp.eval('(() => { const t = document.getElementById("text"); t.value = ' + JSON.stringify(text) + '; t.dispatchEvent(new Event("input", {bubbles:true})); document.querySelector(".send-btn").click(); return "ok"; })()');
  }
  async function dumpMessages(label) {
    const arr = await cdp.eval('Array.from(document.querySelectorAll(".msg")).map(m => m.className.replace("msg ","").replace(" anim","") + ":" + ((m.querySelector(".bubble")||{textContent:""}).textContent || "").slice(0, 24))');
    console.log('  [' + label + '] DOM: ' + JSON.stringify(arr));
  }
  async function shot(name) {
    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(OUT + '/' + name + '.png', Buffer.from(r.data, 'base64'));
    console.log('shot:', name);
  }

  /* 种子：关自动朗读（QA 拦截了 TTS），再进聊天页 */
  await cdp.send('Page.navigate', { url: BASE + '/api/health' });
  await sleep(1200);
  await cdp.eval('localStorage.setItem("xiaoni_sound_on","0"); "ok"');
  await cdp.send('Page.navigate', { url: BASE + '/pages/chat.html' });
  await sleep(3500);

  /* ===== 1) IME 守卫：合成期 Enter 不得发送 ===== */
  await cdp.eval('(() => { const t = document.getElementById("text"); t.value = "正在打字"; t.dispatchEvent(new Event("input",{bubbles:true})); return "ok"; })()');
  await cdp.eval('document.getElementById("text").dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", keyCode: 229, bubbles: true, cancelable: true})); "ok"');
  await sleep(400);
  check('IME 合成期 Enter 不发送（keyCode 229）', (await cdp.eval('document.querySelectorAll(".msg.user").length')) === 0, '用户消息被误发');
  await cdp.eval('document.getElementById("text").dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", isComposing: true, bubbles: true, cancelable: true})); "ok"');
  await sleep(400);
  check('IME 合成期 Enter 不发送（isComposing）', (await cdp.eval('document.querySelectorAll(".msg.user").length')) === 0, '用户消息被误发');

  /* ===== 2) 正常发送 + 流式渲染 ===== */
  await typeAndSend('今晚吃什么');
  check('发送后立即出现 typing 指示', await waitFor("!!document.getElementById('typing')", 1500));
  await waitFor('document.querySelectorAll(".msg.ai .bubble").length > 0 && (document.querySelector(".msg.ai .bubble")||{textContent:""}).textContent.length > 0', 4000);
  const midLen = await cdp.eval('(document.querySelector(".msg.ai .bubble")||{textContent:""}).textContent.length');
  await waitFor('!document.getElementById("typing") && document.querySelector(".msg.ai .msg-meta .speak-btn")', 8000);
  check('流式渲染出现中间增量', midLen >= 0, 'mid=' + midLen);
  check('回复内容剥净样式标记', (await cdp.eval('document.querySelector(".msg.ai .bubble").textContent')) === '好，我在。今天训练刚结束，状态还行。', '实际=' + JSON.stringify(await cdp.eval('document.querySelector(".msg.ai .bubble").textContent')));
  const meta = await cdp.eval('({s: !!document.querySelector(".msg.ai .msg-meta .speak-btn"), r: !!document.querySelector(".msg.ai .msg-meta .resynth-btn"), g: !!document.querySelector(".msg.ai .msg-meta .regen-btn")})');
  check('回复挂 朗读/重新合成/重新生成 按钮', meta.s && meta.r && meta.g, JSON.stringify(meta));
  /* 主对话设计（main_{role} 固定会话）：标题恒为「{角色}·主对话」且 manualTitle，
     不被首条消息覆盖；这里断言标题保持主对话命名而非旧的"首条消息自动命名" */
  check('主对话标题固定、不被首条消息覆盖', /·主对话$/.test(await cdp.eval('document.querySelector(".chat-title-text").textContent') || ''), '标题=' + await cdp.eval('document.querySelector(".chat-title-text").textContent'));
  check('会话已 PUT 持久化到服务端', apiLog.some(l => l.includes('/api/sessions?client=')), JSON.stringify(apiLog.slice(0, 8)));
  await shot('10-flow-sent'); await dumpMessages('发送后');

  /* ===== 3) 重新生成 ===== */
  await cdp.eval('document.querySelector(".msg.ai .msg-meta .regen-btn").click(); "ok"');
  await waitFor('!document.getElementById("typing") && document.querySelectorAll(".msg.ai").length === 1 && document.querySelector(".msg.ai .msg-meta .speak-btn")', 8000);
  check('重新生成后仍只有 1 条回复（旧回复被替换）', (await cdp.eval('document.querySelectorAll(".msg.ai").length')) === 1, '回复数量异常');
  check('重新生成保留了用户消息', (await cdp.eval('document.querySelectorAll(".msg.user").length')) === 1, '用户消息丢失');
  await dumpMessages('重新生成后');

  /* ===== 4) 编辑消息重发 ===== */
  await cdp.eval('document.querySelector(".msg.user").dispatchEvent(new MouseEvent("contextmenu", {bubbles:true, cancelable:true, clientX: 700, clientY: 400})); "ok"');
  await sleep(300);
  const menuItems = await cdp.eval('Array.from(document.querySelectorAll("#ctx-menu button span")).map(s => s.textContent)');
  check('用户消息菜单含 修改消息/复制/删除', JSON.stringify(menuItems) === JSON.stringify(['修改消息', '复制', '删除消息']), JSON.stringify(menuItems));
  await cdp.eval('Array.from(document.querySelectorAll("#ctx-menu button")).find(b => (b.querySelector("span")||{}).textContent === "修改消息").click(); "ok"');
  await waitFor('!!document.querySelector(".edit-ta")', 2000);
  await cdp.eval('(() => { const t = document.querySelector(".edit-ta"); t.value = "明天下雨吗"; t.dispatchEvent(new Event("input",{bubbles:true})); document.querySelector(".edit-ok").click(); return "ok"; })()');
  await waitFor('!document.getElementById("typing") && document.querySelectorAll(".msg.ai").length === 1 && document.querySelector(".msg.ai .msg-meta .speak-btn")', 8000);
  check('编辑后用户消息已更新', (await cdp.eval('document.querySelector(".msg.user .bubble").textContent')) === '明天下雨吗', '用户消息未更新');
  await dumpMessages('编辑后');
  check('编辑后旧回复作废并重新生成', (await cdp.eval('document.querySelector(".msg.ai .bubble").textContent')) === '好，我在。今天训练刚结束，状态还行。', '实际=' + JSON.stringify(await cdp.eval('Array.from(document.querySelectorAll(".msg")).map(m => m.className + ":" + (m.querySelector(".bubble")||{textContent:""}).textContent.slice(0,30))')));

  /* ===== 5) 失败保留（[fail]：流出部分内容后中断） ===== */
  await typeAndSend('[fail] 看看失败表现');
  await waitFor('!document.getElementById("typing")', 8000);
  await sleep(400);
  await dumpMessages('fail后');
  check('流中断后保留已生成部分', (await cdp.eval('(document.querySelector(".msg.ai:last-of-type .bubble")||{textContent:""}).textContent')) === '这部分先流出来，', '部分内容未保留');

  /* ===== 6) 空回复 → 失败气泡 → 重试 ===== */
  await typeAndSend('[empty] 空回复测试');
  await waitFor('!!document.querySelector(".msg.failed")', 8000);
  check('空回复落失败气泡', ((await cdp.eval('(document.querySelector(".msg.failed .bubble")||{textContent:""}).textContent')) || '').includes('模型没有返回内容'), '失败气泡缺失');
  check('失败气泡带重试按钮', !!(await cdp.eval('document.querySelector(".msg.failed .regen-btn")')), '重试按钮缺失');
  await shot('11-flow-failed');
  await cdp.eval('document.querySelector(".msg.failed .regen-btn").click(); "ok"');
  await waitFor('!document.getElementById("typing") && !document.querySelector(".msg.failed") && (() => { const as = document.querySelectorAll(".msg.ai"); return as.length && as[as.length-1].querySelector(".msg-meta .speak-btn"); })()', 8000);
  await dumpMessages('重试后');
  check('重试后失败气泡消失且生成成功', (await cdp.eval('(document.querySelector(".msg.ai:last-of-type .bubble")||{textContent:""}).textContent')) === '好，我在。重试成功。', '实际=' + JSON.stringify(await cdp.eval('Array.from(document.querySelectorAll(".msg")).map(m => m.className + ":" + (m.querySelector(".bubble")||{textContent:""}).textContent.slice(0,30))')));

  /* ===== 7) 服务端持久化核对 ===== */
  const sessDisk = await cdp.eval('fetch("/api/sessions").then(r => r.json()).then(d => ({n: d.sessions.length, msgs: d.sessions[0] ? d.sessions[0].history.length : 0, title: d.sessions[0] ? d.sessions[0].title : ""}))');
  check('服务端会话已持久化（1 会话、主对话标题、消息>3）', sessDisk.n === 1 && sessDisk.msgs >= 3 && /·主对话$/.test(sessDisk.title || ''), JSON.stringify(sessDisk));

  console.log('api log tail:', JSON.stringify(apiLog.slice(-8)));
  console.log('===== 流程验证结果: PASS ' + PASS + ' / FAIL ' + FAIL + ' =====');
  ws.close(); chrome.kill();
  process.exit(FAIL ? 1 : 0);
})().catch(e => { console.error('FLOW FAIL:', e); process.exit(1); });
