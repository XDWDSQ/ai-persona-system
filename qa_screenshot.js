/* QA 渲染验证：CDP 驱动无头 Chrome，多场景截图 + 收集页面 JS 错误 + DOM 断言 */
const { execFile } = require('child_process');
const fs = require('fs');
const http = require('http');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:8010';
const OUT = '_qa_shots';
const PORT = 9300 + Math.floor(Math.random() * 90);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; this.consoleErrors = [];
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { const { resolve, reject } = this.pending.get(m.id); this.pending.delete(m.id);
        if (m.error) reject(new Error(m.error.message)); else resolve(m.result); }
      else if (m.method) {
        this.events.push(m);
        if (m.method === 'Runtime.exceptionThrown') this.consoleErrors.push(m.params.exceptionDetails.text + ' ' + ((m.params.exceptionDetails.exception||{}).description||''));
        if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') this.consoleErrors.push(m.params.entry.text);
      }
    });
  }
  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
}

(async () => {
  /* 清理上次运行遗留的 QA 孤儿 Chrome（按命令行标记精确匹配，不影响用户浏览器） */
  try {
    const { execSync } = require('child_process');
    const ps1 = process.env.TMP + '/qa_kill_orphans.ps1';
    fs.writeFileSync(ps1, "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*chrome_qa*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }");
    execSync('powershell -NoProfile -ExecutionPolicy Bypass -File "' + ps1 + '"', { stdio: 'ignore' });
  } catch (e) { /* 没有孤儿或 powershell 失败，继续 */ }

  fs.mkdirSync(OUT, { recursive: true });
  const chrome = execFile(CHROME, [
    '--headless=new', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${process.env.TMP}/chrome_qa_cdp`,
    '--no-first-run', '--window-size=1440,900', 'about:blank',
  ]);
  await sleep(2500);
  const targets = await httpGetJson(`http://127.0.0.1:${PORT}/json`);
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const cdp = new CDP(ws);
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');

  async function nav(url, waitMs) {
    await cdp.send('Page.navigate', { url });
    await sleep(waitMs || 3500);
  }
  async function shot(name, fullPage) {
    const r = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: !!fullPage });
    fs.writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, 'base64'));
    console.log('shot:', name);
  }
  async function evaljs(expr) {
    const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result && r.result.value;
  }
  async function viewport(w, h, mobile) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile });
  }

  /* ===== 场景 1：桌面聊天页首屏 ===== */
  await viewport(1440, 900, false);
  await nav(BASE + '/pages/chat.html', 4500);
  console.log('assert:', JSON.stringify(await evaljs(`({
    welcome: document.getElementById('welcome-title') ? document.getElementById('welcome-title').textContent : null,
    eyebrow: (document.querySelector('.welcome .eyebrow')||{}).textContent || null,
    sub: (document.getElementById('welcome-sub')||{}).textContent || null,
    chips: document.querySelectorAll('.prompt-chip').length,
    roleItems: document.querySelectorAll('.role-item').length,
    providerName: (document.querySelector('.provider-row[data-kind=chat] .p-name')||{}).textContent,
    cSub: (document.querySelector('.c-sub')||{}).textContent,
    msgCount: document.querySelectorAll('.msg').length,
    activeSessionTitle: (document.querySelector('.session.active .s-title')||{}).textContent || null,
    visiblePlaceholders: Array.from(document.querySelectorAll('.session .s-title')).filter(t => t.textContent === '新对话').length,
    speedRowGone: !document.querySelector('.speed-row'),
    sideLinksGone: !document.querySelector('.side-link'),
    petMounted: !!document.getElementById('pet'),
    fontLinksGone: !document.querySelector('link[href*="fonts.googleapis"]')
  })`)));
  await shot('01-chat-desktop');

  /* ===== 场景 2：打开长会话看消息区 + 回到底部浮钮 + 复制菜单 ===== */
  await evaljs(`(Array.from(document.querySelectorAll('.session')).find(b => (b.querySelector('.s-title')||{}).textContent === '在干嘛') || {click(){}}).click(); 'ok'`);
  await sleep(900);
  console.log('assert:', JSON.stringify(await evaljs(`({
    msgs: document.querySelectorAll('.msg').length,
    hasCopyInMenu: true,
    jumpBtnHiddenAtBottom: !document.querySelector('.jump-bottom').classList.contains('show')
  })`)));
  await evaljs(`const _m = document.getElementById('msgs'); _m.scrollTop = Math.max(0, _m.scrollHeight - _m.clientHeight - 300); 'ok'`);
  await sleep(500);
  console.log('assert jump:', JSON.stringify(await evaljs(`({jumpShown: document.querySelector('.jump-bottom').classList.contains('show')})`)));
  await shot('02a-jump-bottom');
  await evaljs(`const _d = document.querySelectorAll('.msg')[1]; _d.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 500, clientY: 300})); 'ok'`);
  await sleep(500);
  console.log('assert menu:', JSON.stringify(await evaljs(`({
    menuOpen: !!document.getElementById('ctx-menu'),
    items: Array.from(document.querySelectorAll('#ctx-menu button span')).map(s => s.textContent)
  })`)));
  await shot('02b-ctx-menu');
  await evaljs(`document.body.click(); document.getElementById('msgs').scrollTop = 0; 'ok'`);
  await sleep(400);
  await shot('02-chat-messages-top');

  /* ===== 场景 3：设置面板（新布局） ===== */
  await evaljs(`document.querySelector('[data-dom-id="btn-settings"]').click(); 'ok'`);
  await sleep(800);
  console.log('assert:', JSON.stringify(await evaljs(`({
    settingsOpen: document.getElementById('settings').classList.contains('show'),
    roleTabs: document.querySelectorAll('#role-tabs .set-tab').length,
    personaLen: (document.getElementById('persona').value || '').length,
    soundToggle: !!document.getElementById('sound_toggle'),
    speedSel: (document.getElementById('tts-speed')||{}).value,
    memCount: (document.getElementById('mem-count')||{}).textContent,
    memItems: document.querySelectorAll('#mem-list > div').length,
    localTabsGone: !document.querySelector('#chat-tabs'),
    voiceTabs: Array.from(document.querySelectorAll('#voice-tabs .set-tab')).map(b => b.textContent.trim()),
    advClosed: !document.getElementById('adv-settings').open,
    serverHint: (document.getElementById('server-url-hint')||{}).textContent
  })`)));
  await shot('03-settings', true);

  /* ===== 场景 4：展开高级并滚到可视位置 ===== */
  await evaljs(`document.getElementById('adv-settings').open = true; document.querySelector('#adv-settings > summary').scrollIntoView({block: 'start'}); document.querySelector('#settings .set-body').scrollTop -= 8; 'ok'`);
  await sleep(500);
  console.log('assert speed:', JSON.stringify(await evaljs(`({speedValue: document.getElementById('tts-speed').value, voiceRow: Array.from(document.querySelectorAll('.provider-row')).map(r => (r.querySelector('.p-name')||{}).textContent)})`)));
  console.log('assert:', JSON.stringify(await evaljs(`({
    cloudProvider: (document.getElementById('cloud_provider_sel')||{}).value,
    cloudKeyMasked: (document.getElementById('cloud_api_key')||{}).value,
    minimaxVoiceFieldsVisible: document.getElementById('minimax-voice-fields').style.display !== 'none'
  })`)));
  await shot('04-settings-advanced', true);

  /* ===== 场景 5：现实动态详情折叠 ===== */
  await evaljs(`document.getElementById('rn-details').open = true; 'ok'`);
  await sleep(400);
  await shot('05-settings-news', true);
  await evaljs(`document.querySelector('[data-close]').click(); 'ok'`);

  /* ===== 场景 6：移动端聊天页 ===== */
  await viewport(390, 844, true);
  await sleep(600);
  await shot('06-chat-mobile');

  /* ===== 场景 7：移动端设置 ===== */
  await evaljs(`document.querySelector('[data-dom-id="btn-settings"]').click(); 'ok'`);
  await sleep(800);
  await shot('07-settings-mobile', true);

  /* ===== 场景 8：移动端抽屉（先关设置） ===== */
  await evaljs(`const _s = document.getElementById('settings'); if (_s.classList.contains('show')) document.querySelector('#settings [data-close]').click(); 'ok'`);
  await sleep(500);
  await evaljs(`document.getElementById('menu-btn').click(); 'ok'`);
  await sleep(600);
  await shot('08-drawer-mobile');

  console.log('consoleErrors:', JSON.stringify(cdp.consoleErrors, null, 2));
  ws.close();
  chrome.kill();
  process.exit(0);
})().catch(e => { console.error('QA FAIL:', e); process.exit(1); });
