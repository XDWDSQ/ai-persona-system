# AI 拟人系统

本地优先的 AI 拟人对话系统：FastAPI 后端 + 前端交互页面，集成 LLM 对话、TTS 语音合成（音色克隆）、ASR 语音识别，以及角色运行引擎（长期记忆、情绪状态、时间感知、主动问候）。

## 功能

- 多角色人设对话：本地 Qwen3-4B（llama.cpp / CUDA）或云端 OpenAI 兼容 API（MiMo / DeepSeek / 火山方舟 / 自定义）
- 角色引擎：按角色持久化记忆与状态，对话注入时间/状态/记忆上下文，回复后异步更新；大帅支持主动问候
- 图片/文件附件：发送栏可发图片、文档和其他文件，图片会优先走本地视觉模型，让角色真正“看到”
- 联网搜索：模型判断知识过时时会自动搜索最新消息（默认 DuckDuckGo，Bing RSS 兜底），尤其适合查自己的比赛、战队与近况
- 语音合成与音色克隆：支持本地 Qwen3-TTS 与阿里云千问（qwen3-tts-flash）两条链路（MiMo TTS 已下线，不再支持）
- 语音识别：本地 Qwen3-ASR，离线转写
- 人设、语音参数可视化配置

## 快速开始

```bat
setup.bat              # 首次初始化依赖（一次性）
start.bat              # 一键启动（含本地 LLM）
restart_service.bat    # 重启 8000 端口服务
```

启动后访问 http://127.0.0.1:8000

## 配置参考

复制 `config.example.json` 为 `config.json` 后按需修改。主要字段：

| 字段 | 说明 |
| --- | --- |
| `provider` | 当前 LLM 供应商：`local`（本地 llama.cpp）或 `cloud`（云端） |
| `local` | 本地 LLM 的 `base_url` / `model` / `api_key` |
| `cloud` | 当前云端供应商的 `provider` / `base_url` / `api_key` / `model` / `thinking` |
| `cloud_providers` | 各云端供应商（mimo / deepseek / ark / custom）的预设参数，切换供应商时自动套用 |
| `roles` | 角色定义：名称、简介、人设提示词、角色专属语音参数 |
| `voice` | 全局语音合成配置：`provider`（如 `aliyun`）、音色、风格与各供应商子配置 |
| `tts_cache` | TTS 音频缓存清理策略：`max_files`（默认 500）、`max_bytes`（默认 8GB）、`clean_interval`（默认 3600 秒） |

角色引擎默认开启：

```json
"role_engine": { "enabled": true, "memory_limit": 200, "top_k": 5 }
```

`enabled=false` 时完全退回旧行为。记忆与状态按角色持久化在 `data/memory/{role}.json`、`data/state/{role}.json`。

### .env 密钥变量

| 变量 | 用途 |
| --- | --- |
| `MIMO_API_KEY` | 小米 MiMo（LLM 对话） |
| `DEEPSEEK_API_KEY` | DeepSeek（LLM 对话） |
| `ARK_API_KEY` | 火山方舟（LLM 对话） |
| `ALIYUN_API_KEY` | 阿里云（TTS 语音合成） |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（备用/兼容） |

**优先级**：`config.json` 中对应 provider 条目里填写的 `api_key` 优先；未填写时回退读取 `.env` 中的同名变量。

> 安全建议：密钥只写入 `.env`（已被 .gitignore 忽略），不要把真实 key 写进 `config.json` 并提交。

## 密钥与安全说明

- `.env.example` 是密钥模板：首次使用时复制为 `.env` 并填入真实密钥；`.env` 已在 .gitignore 中，不会入库。
- `/api/status` 接口会**明文返回当前配置的密钥**，这是为了前端设置页能回填已配置的 key（设计意图）。因此：
  - 服务只应绑定 `127.0.0.1`（启动脚本已如此配置），**不要**把 8000 端口暴露到局域网或公网；
  - CORS 已收紧为仅允许本机来源访问。

## 运行与环境说明

- **日志级别**：可用环境变量 `LOG_LEVEL` 控制（默认 `WARNING`，可设 `INFO` / `DEBUG`），例如 `set LOG_LEVEL=DEBUG` 后启动。
- **健康检查**：`GET /api/health` 可用于探活与依赖状态检查。
- **TTS 缓存**：合成音频缓存在 `data/tts_cache/`，清理阈值由 `config.json` 的 `tts_cache` 节配置（`max_files` 默认 500、`max_bytes` 默认 8GB、`clean_interval` 默认 3600 秒）。
- **本地 TTS/ASR 依赖的 venv 路径**（硬编码默认值，换机器需按此布局准备，风险已知、暂不可配）：
  - 本地 TTS：`~/.trae-cn/skills/local-tts`
  - Python 虚拟环境：`~/.openvino/venv/*`（如 `~/.openvino/venv/t2i-tts`）

## API

- `POST /api/chat` 对话（注入角色上下文，返回 `{reply, style}`）
- `POST /api/greeting` 主动问候；`GET/DELETE /api/state` 查看/重置角色状态
- `POST /api/tts` 语音合成；`POST /api/asr` 语音识别
- `POST /api/upload` 附件上传；`GET /uploads/...` 附件访问
- `POST /api/search` 联网搜索；对话中模型输出 `[search:关键词]` 会自动触发
- `GET /api/status`、`GET/POST /api/config`、`GET /api/roles`、`POST /api/roles/apply`
- `GET/PUT /api/sessions` 会话持久化；`POST /api/llm-models` 拉取云端模型列表
- `GET /api/health` 健康检查

## 目录

```
server.py               FastAPI 后端主程序（端口 8000）
role_engine.py          角色运行引擎（记忆/状态/时间/后处理）
config.example.json     配置模板（复制为 config.json 使用）
llm/                    llama.cpp 运行时、模型与启动脚本
adapters/asr/           本地 ASR 适配服务
xiaoni-ai-persona/      前端页面源码
docs/                   GitHub Pages 静态托管副本
data/                   运行时数据（会话、音频、记忆、状态，不入库）
```

## 测试

以下 4 个测试可离线运行（无需密钥、无需启动服务）：

```bat
REM 角色引擎：记忆/状态/时间感知/后处理
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_role_engine.py

REM 服务端辅助函数（配置读写、路径处理等）
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_server_helpers.py

REM 联网搜索（DuckDuckGo / Bing RSS 解析逻辑）
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_search.py

REM 附件上传与处理
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_attachments.py
```

需要真实云端密钥才能运行的脚本：

- `test_mimo.py`：MiMo 云端对话/TTS 联调，需 `.env` 中配置 `MIMO_API_KEY`
- `test_tts.py`：TTS 合成联调，需对应云端密钥与参考音频 `data/voice_dashuai.wav`

端到端链路验证：

- `verify_chain.py`：串联 LLM → TTS 全链路验证，**需服务已在 8000 端口启动**后运行

## 故障排查

- **本地 LLM 未启动 / 连接拒绝**：确认 `llm\start_llm.bat` 已运行且 11434 端口在监听；或改用云端 provider。
- **云端返回 401**：密钥无效或过期，检查 `.env` 对应变量（以及 `config.json` 中 provider 条目是否误填了错误 key）。
- **云端限流（429 / 限流提示）**：降低请求频率或切换其他云端供应商。
- **TTS 合成超时**：本地 TTS 首次加载模型较慢，稍后重试；云端超时检查网络与密钥额度。
- **依赖缺失报错**：运行 `setup.bat` 重新安装依赖（`start.bat` 启动时会做轻量导入校验并提示）。
- **查看更详细日志**：设置 `LOG_LEVEL=INFO` 或 `DEBUG` 后重启服务定位问题。

## 文档

- 角色引擎设计与实现：[docs/superpowers/specs/2026-08-03-role-engine-design.md](docs/superpowers/specs/2026-08-03-role-engine-design.md)
- 本地视觉模型（VLM）使用说明：[llm/README_VL.md](llm/README_VL.md)
- 在线静态展示：https://xdwdsq.github.io/ai-persona-system/
