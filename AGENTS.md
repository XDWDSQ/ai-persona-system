# AI 拟人系统 · 项目级 Agent 偏好

> 本文件是**权威副本**（随 GitHub 备份、跨机器可用）。工作区根目录 `D:\移动云盘同步盘\AI拟人系统\agents.md`
> 是同内容的便捷副本；两处内容应保持一致，改一处请同步另一处。
> 跨会话生效：每次进入本工作目录时，AI 助手应在回答前先读取本文件，按本文件的约定执行。

## 工作目录

- **当前工作区**：`D:\移动云盘同步盘\AI拟人系统`（移动云盘同步盘）
- **实际运行 / git 仓库根**：`D:\移动云盘同步盘\AI拟人系统\ai-persona-system`（有独立 git 仓库，`origin` 指向 GitHub）
- 旧路径 `C:\Users\8891QZ_H\Desktop\AI拟人系统` 已不是主工作区，遇到不一致以当前工作区为准。

## 远程访问地址：用 ngrok

用户日常用 **ngrok 免费版** 做公网隧道。

**真实数据源（唯一真值，不要硬编码）**：

```
ai-persona-system/data/tunnel_url.txt
```

ngrok 免费版每次重启会换域名。被问到"服务器地址 / 远程访问链接"时，先读这个文件。

### ngrok 启动约定

- 用 `ngrok http 8000` 启动。
- 启动后 URL 写入 `ai-persona-system/data/tunnel_url.txt`。
- ngrok 自带管理界面：<http://127.0.0.1:4040>（看流量、检查状态）。
- **免费版警告页**：新浏览器/无痕窗口首次访问免费 ngrok 域名必现 `ERR_NGROK_6024` 提示页，需点 "Visit Site" 才进应用。用户报"页面空白/记录不见"时先怀疑此页；本机 `http://127.0.0.1:8000` 与 curl/API 无此问题。

### 检查 / 更新命令

```bat
REM 查看当前 URL
type "D:\移动云盘同步盘\AI拟人系统\ai-persona-system\data\tunnel_url.txt"

REM 看 ngrok 是否还在跑
curl -s http://127.0.0.1:4040/api/tunnels

REM 重启 ngrok（先关掉旧进程）
taskkill /IM ngrok.exe /F
ngrok http 8000
```

## 安全相关

- 远程访问**必须**先在 `config.json` 顶层设 `access_token`（或环境变量 `ACCESS_TOKEN`），否则所有页面/API 裸奔在公网上。
- `/api/status` 对密钥**一律脱敏**为 `***+尾4`；前端保存时后端识别掩码值并跳过该字段。
- ngrok 免费版是公网可访问，无 access_token 等于把服务敞开。
- **历史泄露口令视为已作废**：曾在公共仓库与旧文档里出现过明文口令，那段历史无法靠改代码撤回。仓库内不得再出现任何口令字面值（含注释与文档正文）。

## 已废弃 / 不要再引用

- **安卓 APK 已停止开发**：手机端统一用 PWA 网页版（浏览器"添加到主屏幕"）。`android/` 仅作存档，不再构建更新；其 `app/src/main/assets/pages/` 里的前端副本已删除（真源是 `xiaoni-ai-persona/pages/`）。`data/tunnel_url_apk.txt` 已废弃。
- **cloudflared / start_tunnel.bat 已退役**：仓库内没有 `start_tunnel.bat` / `start_tunnel.ps1`，历史 cloudflared 流程不再使用，不要建议用户运行。
- **代码托管统一走 GitHub**：远程 `origin` = https://github.com/XDWDSQ/ai-persona-system.git（公共仓库）；**Gitee 已停用，不要再推送或引用**。本机直连 GitHub 不通时走本地代理 7897：`git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push`。
- `deploy/webhook_autodeploy.py` 与 `deploy/README_webhook自动部署.md`（Gitee/cloudflared 时代的自动部署方案）**已删除**；部署机用 ngrok + 任务计划。
- **本地 LLM / 本地 TTS 已从界面下线**（2026-08-29）：后端代码保留（换机可能复活），但 UI 与启动流程都不再涉及。

## 运行 / 编辑入口速记

- **实际运行/编辑入口**：`ai-persona-system/`（`server.py` `role_engine.py` `config.json` `start.bat` 都在这里）
- **前端页面**：`ai-persona-system/xiaoni-ai-persona/pages/`（单页 `chat.html` 纯标记 + `css/chat.css` 全部样式 + `js/app.js` 主逻辑 + `pet.js`/`pet/` 桌宠；2026-09-02 由 265KB 单文件拆分，「暖夜陪伴」主题）
- **运行时数据**：`ai-persona-system/data/`（`sessions.json` `memory/` `state/` `tts_cache/` `uploads/` `weather.json` `role_news.json`）
- **测试门**：`ai-persona-system/run_tests.bat`（通配发现 `test_*.py`，需真实密钥的套件在 `SKIP_LIST` 里跳过）；裸 `pytest` 也可用，`pytest.ini` 已限定收集边界。
- **不要把根目录**（`D:\移动云盘同步盘\AI拟人系统`）当成运行根目录，那里主要是设计资产与文档。
