# AI 拟人系统 · 项目级 Agent 偏好

> 本文件是**权威副本**（随 GitHub 备份、跨机器可用）。工作区根目录 `D:\移动云盘同步盘\AI拟人系统\agents.md`
> 是同内容的便捷副本；两处内容应保持一致，改一处请同步另一处。
> 跨会话生效：每次进入本工作目录时，AI 助手应在回答前先读取本文件，按本文件的约定执行。

## 工作目录

- **当前工作区**：`D:\移动云盘同步盘\AI拟人系统`（移动云盘同步盘）
- **实际运行 / git 仓库根**：`D:\移动云盘同步盘\AI拟人系统\ai-persona-system`（有独立 git 仓库，`origin` 指向 GitHub）
- 旧路径 `C:\Users\8891QZ_H\Desktop\AI拟人系统` 已不是主工作区，遇到不一致以当前工作区为准。

## 运行方式：本机开发、本机启动（2026-09 起）

项目**只在本机跑**，不再做远程 / 公网部署。

- 启动：`ai-persona-system\start.bat`；改完代码用 `restart_service.bat` 重启加载。服务监听 `http://127.0.0.1:8000`。
- 被问"服务器地址 / 访问链接"时，答案就是 **`http://127.0.0.1:8000`** —— 不要再去找隧道地址。
- **ngrok / 隧道流程已退役**：`data/tunnel_url.txt` 不再维护，不要再建议启动隧道、也不要再读那个文件。
- 因此 `config.json` 的 `access_token` 现在是**可选的本地保护**（配了才生效），不再是公网必需的前置条件。

## 安全相关

- `config.json` 顶层的 `access_token`（或环境变量 `ACCESS_TOKEN`）现在是**可选的本地保护**：配了则所有页面/API 都要求口令，不配则本机直连可用。**任何时候要把服务暴露到公网（隧道/局域网）都必须先配上它。**
- `/api/status` 对密钥**一律脱敏**为 `***+尾4`；前端保存时后端识别掩码值并跳过该字段。
- 若只想在本机用，可把启动脚本的 host 从 `0.0.0.0` 改成 `127.0.0.1`（这样连局域网都进不来）。
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
