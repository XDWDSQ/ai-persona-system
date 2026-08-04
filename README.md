# AI 拟人系统

本地优先的 AI 拟人对话系统：FastAPI 后端 + 前端交互页面，集成 LLM 对话、TTS 语音合成（音色克隆）、ASR 语音识别，以及角色运行引擎（长期记忆、情绪状态、时间/位置/天气/现实动态感知、主动问候）。

## 功能

- 多角色人设对话：本地 Qwen3.5-4B（llama.cpp / CUDA，8GB 显存友好）或云端 OpenAI 兼容 API（MiMo / DeepSeek / 火山方舟 / 自定义）
- 角色引擎：按角色持久化记忆与状态，对话注入时间/位置/天气/状态/现实动态/记忆上下文，回复后异步更新；大帅支持主动问候
- 图片/文件附件：发送栏可发图片、文档和其他文件，图片会优先走本地视觉模型，让角色真正“看到”
- 联网搜索：模型判断知识过时时会自动搜索最新消息（默认 DuckDuckGo，Bing RSS 兜底），尤其适合查自己的比赛、战队与近况
- 语音合成与音色克隆：支持本地 Qwen3-TTS、阿里云千问（qwen3-tts-flash）与 MiniMax 海螺（T2A v2）三条链路（MiMo TTS 已下线，不再支持）；MiniMax 支持声音克隆，合成采样参数（语速/音量/音调/采样率）可在设置页调整
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
| `local` | 本地 LLM 的 `base_url` / `model` / `api_key`（本地模型默认 `Qwen3.5-4B-Q4_K_M`） |
| `cloud` | 当前云端供应商的 `provider` / `base_url` / `api_key` / `model` / `thinking` |
| `cloud_providers` | 各云端供应商（mimo / deepseek / ark / custom）的预设参数，切换供应商时自动套用 |
| `roles` | 角色定义：名称、简介、人设提示词、角色专属语音参数 |
| `voice` | 全局语音合成配置：`provider`（如 `aliyun`）、音色、风格与各供应商子配置；`minimax` 子配置含 `speed`（0.5~2）、`vol`（整数 0~10）、`pitch`（整数 0~10）、`sample_rate`（最高 32000） |
| `tts_cache` | TTS 音频缓存清理策略：`max_files`（默认 500）、`max_bytes`（默认 8GB）、`clean_interval`（默认 3600 秒） |

### MiniMax 声音克隆

账号需在 platform.minimaxi.com 完成实名认证（未认证时克隆接口返回 2038）。准备 **10~120 秒、44.1kHz、单声道、无明显背景音乐**的人声片段（推荐采访/直播原声），运行：

```bat
python minimax_clone.py
```

脚本流程：检查参考音频质量（时长/采样率/声道）→ 上传 → 克隆（voice_id 形如 `dashuai_clone_v2_0804`）→ 用克隆音色试听合成一句 → 自动把 voice_id 写回 `config.json` 的 `voice.minimax.voice`。

- 参考音频建议：内容连贯的单人原声，避免背景音乐与多人叠声；过短（<10s）相似度低，过长（>3 分钟）会稀释音色特征
- 试听合成与网页实际合成使用同一组采样参数（从 `voice.minimax` 读取）
- 临时音色 **7 天内至少使用一次**，否则会被 MiniMax 删除
- 参数约束（speech-02-hd 实测）：`vol` / `pitch` 必须是整数，`sample_rate` 最高 32000，超出会返回 2013

角色引擎默认开启：

```json
"role_engine": { "enabled": true, "memory_limit": 200, "top_k": 5 }
```

`enabled=false` 时完全退回旧行为。记忆与状态按角色持久化在 `data/memory/{role}.json`、`data/state/{role}.json`。

位置感知（角色知道用户在哪，聊位置/天气/通勤能自然接话）：

```json
"location": { "enabled": true, "manual": "", "weather": true }
```

- 来源优先级：`manual`（手动填写，如「广东省深圳市南山区」）> 浏览器 GPS 上报 > IP 自动定位（城市级）。
- 手动位置存 `config.json`；GPS/IP 自动结果存 `data/location.json`（24h 内不重复定位）。
- 设置面板「我的位置」可手动填写、📡 浏览器定位、🌐 IP 定位、清除；`enabled=false` 或位置未知时完全不注入。
- 位置属隐私信息，仅本机保存；IP 定位会把请求发到 ip-api.com（免费接口，无 key，仅返回城市级位置）。

天气感知（`location.weather=true` 时开启）：根据当前位置查询实时天气，注入对话上下文。

- 数据源 Open-Meteo（免费无 key）：内置 300+ 中国城市经纬度表直接命中，表外城市走 geocoding 兜底。
- 天气缓存 30 分钟（内存 + `data/weather.json`），对话主链路只读缓存、绝不等待外部 API；位置变化自动作废缓存。
- 聊天时角色知道「多云 26°C」这类实时天气，问带伞/穿衣/冷不冷能自然回应；天气查询不发送经纬度给第三方。

角色现实动态（角色知道自己现实世界的位置和最近在忙什么，非扮演设定）：

```json
"roles": {
  "dashuai": {
    "name": "大帅",
    "news": { "enabled": true, "keyword": "成都AG超玩会 大帅 孟家俊" }
  }
}
```

- 给有现实身份的角色配 `news.keyword`（战队名 + ID + 本名），系统会用联网搜索抓取真实最新动态，LLM 总结成 2-3 句（最近在忙什么、现在可能在哪），注入对话上下文。
- 缓存 12 小时（`data/role_news.json`）；启动/切角色/手动刷新时后台更新，对话主链路只读缓存不阻塞。
- 搜索/总结失败时静默降级（不注入），角色配置未配关键词的（如泛化角色）完全跳过。
- 设置面板「角色现实动态」可查看最新动态并 🔄 手动刷新（首次搜索+总结约十几秒）。
- 另支持角色静态自况（可选）：`roles.{key}.self = { location: "…", recent: "…" }`，作为现实动态的补充基线。

### .env 密钥变量

| 变量 | 用途 |
| --- | --- |
| `MIMO_API_KEY` | 小米 MiMo（LLM 对话） |
| `DEEPSEEK_API_KEY` | DeepSeek（LLM 对话） |
| `ARK_API_KEY` | 火山方舟（LLM 对话） |
| `ALIYUN_API_KEY` | 阿里云（TTS 语音合成） |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（备用/兼容） |
| `MINIMAX_API_KEY` | MiniMax 海螺（TTS 语音合成，voice.provider=minimax 时使用） |

**优先级**：`config.json` 中对应 provider 条目里填写的 `api_key` 优先；未填写时回退读取 `.env` 中的同名变量。

> 安全建议：密钥只写入 `.env`（已被 .gitignore 忽略），不要把真实 key 写进 `config.json` 并提交。

## 密钥与安全说明

- `.env.example` 是密钥模板：首次使用时复制为 `.env` 并填入真实密钥；`.env` 已在 .gitignore 中，不会入库。
- `/api/status` 接口会**明文返回当前配置的密钥**，这是为了前端设置页能回填已配置的 key（设计意图）。
- **访问口令保护**：在 `config.json` 顶层配置 `access_token`（或环境变量 `ACCESS_TOKEN`）后，所有页面与 API 均需口令才能访问，未登录请求会被重定向到 `/login` 或返回 401。未配置口令时保持仅本机可访问的旧行为。
- 开放远程访问（局域网 / Cloudflare Tunnel）前**必须先配置访问口令**，否则同网络下任何人可查看 `/api/status` 中的明文密钥。
- 服务绑定 `0.0.0.0` 以支持局域网直连与隧道接入，安全边界由访问口令兜底。

## 手机远程访问（Cloudflare Tunnel）

电脑跑本地服务、手机随时随地访问：

1. 在 `config.json` 顶层配置访问口令：`"access_token": "你的口令"`（或设置环境变量 `ACCESS_TOKEN`）。
2. 启动服务：`start.bat`（或 `restart_service.bat` 重启）。
3. 安装并登录 cloudflared：`winget install cloudflare.cloudflared`，然后 `cloudflared tunnel login`。
4. 创建隧道并绑定域名：

   ```bat
   cloudflared tunnel create ai-persona
   cloudflared tunnel route dns ai-persona ai.yourdomain.com
   ```

5. 写 `cloudflared.yml`（隧道名、凭据路径、url 指向 `http://127.0.0.1:8000`）后运行 `cloudflared tunnel run ai-persona`。
6. 手机浏览器打开 `https://ai.yourdomain.com`，输入访问口令即可使用（自动走 HTTPS）。

没有自有域名时，可临时用 `cloudflared tunnel --url http://127.0.0.1:8000` 获取 trycloudflare.com 随机网址（重启会失效，仅适合临时演示）；也可直接运行 `start_tunnel.bat` 一键启动，窗口内会显示本次的访问地址。

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
role_engine.py          角色运行引擎（记忆/状态/时间/位置/天气/后处理）
config.example.json     配置模板（复制为 config.json 使用）
llm/                    llama.cpp 运行时（llm/bin）、模型（llm/models）与启动脚本
adapters/asr/           本地 ASR 适配服务
xiaoni-ai-persona/      前端页面源码
docs-specs/             角色引擎设计文档
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

## 本地 LLM 说明

- **模型**：`llm/models/Qwen3.5-4B-Q4_K_M.gguf`（Q4_K_M 量化约 2.6GB，8GB 显存可全层 GPU 推理）。旧 Qwen3-4B 已移除。
- **思考模式**：Qwen3.5 系列默认开启思考模式，长人设下思考过程会吃光 `max_tokens` 导致空回复。`server.py` 已在 local 分支自动下发 `chat_template_kwargs.enable_thinking=false` 关闭思考，无需手动配置。
- **模型缺失时**：`start.bat` 会自动调用 `llm/get_llm.py` 从多镜像（hf-mirror → HuggingFace → ModelScope）下载；也可手动下载同名 GGUF 放入 `llm/models/`。
- **采样参数**：`llm/start_llm.bat` 中已针对人设特调（temp 0.85、top-k 40、top-p 0.92、repeat-penalty 1.25 等）。

## 故障排查

- **本地 LLM 未启动 / 连接拒绝**：确认 `llm\start_llm.bat` 已运行且 11434 端口在监听；或改用云端 provider。
- **云端返回 401**：密钥无效或过期，检查 `.env` 对应变量（以及 `config.json` 中 provider 条目是否误填了错误 key）。
- **云端限流（429 / 限流提示）**：降低请求频率或切换其他云端供应商。
- **TTS 合成超时**：本地 TTS 首次加载模型较慢，稍后重试；云端超时检查网络与密钥额度。
- **依赖缺失报错**：运行 `setup.bat` 重新安装依赖（`start.bat` 启动时会做轻量导入校验并提示）。
- **查看更详细日志**：设置 `LOG_LEVEL=INFO` 或 `DEBUG` 后重启服务定位问题。

## 文档

- 角色引擎设计与实现：[docs-specs/2026-08-03-role-engine-design.md](docs-specs/2026-08-03-role-engine-design.md)
- 本地视觉模型（VLM）使用说明：[llm/README_VL.md](llm/README_VL.md)
