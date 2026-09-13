# AI 拟人系统

本地优先的 AI 拟人对话系统：FastAPI 后端 + 前端交互页面，集成 LLM 对话、TTS 语音合成（音色克隆），以及角色运行引擎（长期记忆、情绪状态、时间/位置/天气/现实动态感知、主动问候）。

## 功能

- 多角色人设对话：本地 Qwen3.5-4B（llama.cpp / CUDA，8GB 显存友好）或云端 OpenAI 兼容 API（小米 MiMo / DeepSeek / 火山方舟 / MiniMax / 自定义）
- 角色引擎：按角色持久化记忆与状态，对话注入时间/位置/天气/状态/现实动态/记忆上下文，回复后异步更新；大帅支持主动问候
- 图片/文件附件：发送栏可发图片、文档和其他文件；图片走视觉链路（默认 `auto`：云端多模态优先、本地 Qwen2.5-VL 兜底），让角色真正"看到"
- 联网搜索：模型判断知识过时时会自动搜索最新消息（默认 DuckDuckGo，Bing RSS 兜底），尤其适合查自己的比赛、战队与近况
- 语音合成与音色克隆：支持本地 Qwen3-TTS、阿里云千问（qwen3-tts / qwen3-tts-vc 克隆音色）、MiniMax 海螺、小米 MiMo 四条链路；阿里云与 MiniMax 支持声音克隆，合成采样参数（语速/音量/音调/采样率）可在设置页调整
- 人设、语音参数可视化配置
- **2027 赛季剧情分支**：大帅·2027 独立角色——虚拟 KPL 赛程（55 场预生成）、剧情时钟（现实一天=虚拟一天 + 任意跳转）、情感状态机（暗恋隐忍→相爱相杀→暧昧升温→在一起）、旁白双声部、今日剧情引导卡
- **主对话模式**：每个角色固定一个主对话（不能新建/删除），切角色自动切换，聊天记录零散落

## 快速开始

```bat
setup.bat              # 首次初始化依赖（一次性）
start.bat              # 一键启动服务（端口 8000）
restart_service.bat    # 改代码后重启 8000 端口服务（加载最新代码）
run_tests.bat          # 一键跑全部离线测试
```

启动后访问 http://127.0.0.1:8000

## 2027 赛季剧情分支（大帅·2027）

给「大帅」角色做的独立剧情分支：新增角色 `dashuai2027`（「大帅·2027」），原「大帅」角色完全不受影响。设定：2026 年打完一诺退役，玩家扮演 19 岁高分段路人王「岚风」入队接替一诺，与大帅（游走位）开启虚拟 2027 KPL 赛季——场下暗恋隐忍、场上游戏理解分歧与指挥权争夺，相爱相杀后被玩家攻略。

### 剧情系统（story_kpl2027.py）

- **2027 赛程**：55 场比赛预生成（春季 23 + 夏季 23 + 年总 9），真实队名、固定随机种子（同一天跳转两次结果一致），赛制参照真实 KPL（常规赛 BO5 分组 / 季后赛 BO7 / 年总）
- **剧情时钟**：现实一天 = 虚拟一天（锚点 2026-08-08）；跳转任意日期进入 `override` 模式（剧情暂停），「回到今天」恢复与现实同步
- **比赛模拟**：胜负由岚风在对话中宣布（如「赢了 3:1」），系统自动识别记录战绩并推进剧情（LLM 抽取 + 正则兜底）；也可手动调接口
- **情感状态机**：0 暗恋隐忍 → 1 相爱相杀（首次输球复盘大吵触发）→ 2 暧昧升温（指挥权归岚风）→ 3 在一起（岚风攻略成功）；各阶段行为指令随虚拟日期注入模型
- **今日剧情指引**：按日期/赛段生成详细可演剧情（背景 + 可演选项 + 大帅状态，七套模板），注入模型上下文 + 前端引导卡展示
- **旁白双声部**：模型回复拆分为【旁白】（第三视角叙事，灰色小字气泡）与【大帅】（台词气泡）两段；旁白随消息持久化（刷新/切会话不丢）、不进 LLM 上下文、不参与朗读；旁白只推进剧情、不替大帅说话
- **记忆预热**：剧情节点（巅峰赛撞车、一诺退役、官宣入队等）写入大帅记忆库，保证剧情连贯

### 使用

1. 聊天页左侧切到「大帅·2027」（新角色 tab 自动出现，头像/音色复用大帅）
2. 侧栏点「2027 赛季赛程表」打开 `pages/story.html`：月历视图（比赛日/训练日/休赛/团综标记），点任意日期跳转，「回到今天」恢复同步；页面含今日剧情卡、战绩与情感阶段
3. 空主对话打开时自动显示「剧情引导卡」（今日日期/事件/可演剧情/开场提示），发第一条消息后自动消失
4. 每个角色固定一个主对话（不能新建/删除/改名，旧记录自动迁移），切角色自动切换

### 配置

```json
"roles": {
  "dashuai2027": {
    "name": "大帅·2027",
    "story": { "enabled": true },
    "news": { "enabled": false }
  }
}
```

- 角色含 `story.enabled=true` 时启用剧情系统；剧情数据存 `data/story/`（日历 + 状态，首次启动自动生成；改赛程/事件后需删 `data/story/kpl2027_calendar.json` 并重启重建）
- 剧情日期以「虚拟日历」块注入 system prompt，与真实时间明确区分，避免模型混淆
- 剧情接口（仅 story 角色可用，其他角色 404）：
  - `GET /api/story/status` 今日剧情/战绩/情感阶段/剧情指引
  - `GET /api/story/calendar` 全年赛程
  - `POST /api/story/jump` 跳转日期（`{"date":"2027-01-14"}`）
  - `POST /api/story/resume` 回到现实今天
  - `POST /api/story/result` 记录比赛结果（`{"date","win","score","mvp"}`）
  - `POST /api/story/flag` 推进剧情标志位（如 `{"flag":"command_win"}`）
- 分支人设与剧情设定（用户逐条确认定稿）：[docs-specs/大帅2027人设档案.md](docs-specs/大帅2027人设档案.md)（全网搜集的大帅真实资料 + 分支人设 + 玩家卡「岚风」+ 训练室排布 + 情感线设定）

## 手机端使用（PWA 网页版）

安卓 APK 已**停止开发**，手机端统一使用网页版（PWA），体验对齐原生 App：

- 手机浏览器打开隧道地址，登录后在浏览器菜单选「**添加到主屏幕 / 安装应用**」
- 主屏幕打开即独立窗口运行（无地址栏），应用壳由 Service Worker 缓存，**秒开、弱网可用**
- 服务器重启后隧道地址变了，在页面「⚙ 设置 → 服务器连接」里更新即可
- 旧 APK 工程保留在 `android/` 目录仅作存档，不再构建更新

## 配置参考

复制 `config.example.json` 为 `config.json` 后按需修改。主要字段：

| 字段 | 说明 |
| --- | --- |
| `provider` | 当前 LLM 供应商：`local`（本地 llama.cpp）或 `cloud`（云端） |
| `local` | 本地 LLM 的 `base_url` / `model` / `api_key`（本地模型默认 `Qwen3.5-4B-Q4_K_M`） |
| `cloud` | 当前云端供应商的 `provider` / `base_url` / `api_key` / `model` / `thinking` / `billing_mode` |
| `cloud_providers` | 各云端供应商（mimo / deepseek / ark / minimax / custom）的预设参数，切换供应商时自动套用 |
| `roles` | 角色定义：名称、简介、人设提示词、角色专属语音参数 |
| `voice` | 全局语音合成配置：`provider`（如 `aliyun`）、音色、风格与各供应商子配置；`minimax` 子配置含 `speed`（0.5~2）、`vol`/`pitch`（官方 speech-02-hd 需整数 0~10，GMI 接口支持小数如 2.0/0.0）、`sample_rate`（最高 32000）、`api_schema`（`official` / `gmi`） |
| `tts_cache` | TTS 音频缓存清理策略：`max_files`（代码默认 500，模板推荐 200）、`max_bytes`（代码默认 8GB，模板推荐 1GB）、`clean_interval`（默认 3600 秒） |

### MiniMax 云端文字模型（双计费模式）

MiniMax 作为云端文字生成供应商已接入统一 OpenAI 兼容调用链（模型列表 / 生成 / 流式 / 错误处理与其他供应商一致），在设置页「供应商」下拉选择 **MiniMax** 即可，调用方业务代码零改动。支持两种计费模式（`cloud_providers.minimax.billing_mode`，两种 Key 互不混用）：

| 模式 | 值 | 密钥来源 | 计费方式 |
| --- | --- | --- | --- |
| 按量付费（默认） | `payg` | 开放平台「账户管理 > API 管理」的 **API Key** | 按实际 token 用量实时计费，每次响应 `usage`（prompt/completion/total tokens）即本次用量，进程内自动累计 |
| Token Plan | `token_plan` | 「订阅管理」的 **订阅 Key** | 扣减套餐额度 / 已购积分；余额不足（错误码 1008）或超出资源限制（2056）时返回明确中文错误 |

配置方式（任选其一）：

- **配置文件**：`config.json` 的 `cloud_providers.minimax` 条目填 `base_url` / `api_key` / `model` / `billing_mode`，切换供应商自动套用
- **环境变量**：密钥用 `MINIMAX_API_KEY`（与 MiniMax 语音 TTS 共用），计费模式可用 `MINIMAX_BILLING_MODE=token_plan` 兜底（config 未配置时生效）
- **设置页**：云端 API 面板选择 MiniMax 后出现「计费模式」下拉，保存即写入配置

```json
"minimax": {
  "label": "MiniMax",
  "base_url": "https://api.minimaxi.com/v1",
  "model": "MiniMax-M3",
  "api_key": "",
  "thinking": true,
  "billing_mode": "payg"
}
```

- 默认模型 `MiniMax-M3`，可在设置页「🔄 获取模型列表」拉取平台全量模型后选择
- **Token Plan 配额查询**：POST `/api/llm-quota`（或调用 `minimax_llm.query_quota`），用订阅 Key 查官方 `/v1/token_plan/remains` 套餐额度 / 积分余额；payg 模式返回按量计费提示。未配置密钥时给出明确 400 提示
- 错误处理与其他云端供应商一致（HTTPException 502 + 中文提示）：鉴权失败（1004/2049，含按量 Key 与订阅 Key 混用提示）、余额不足（1008/402）、Token Plan 超限（2056）、限流（429/1002）与临时错误（1000/1001/1024/1033）指数退避自动重试；空回复自动重试并降级思考模式；MiniMax 不支持 `thinking` 字段时自动移除重试
- 流式输出带 OpenAI 标准计费参数 `stream_options.include_usage`，流式用量从末尾 chunk 解析并累计

### MiniMax 声音克隆

账号需在 platform.minimaxi.com 完成实名认证（未认证时克隆接口返回 2038）。准备 **10~120 秒、44.1kHz、单声道、无明显背景音乐**的人声片段（推荐采访/直播原声），运行：

```bat
python minimax_clone.py
```

脚本流程：检查参考音频质量（时长/采样率/声道）→ 上传 → 克隆（voice_id 形如 `dashuai_clone_v2_0804`）→ 用克隆音色试听合成一句 → 自动把 voice_id 写回 `config.json` 的 `voice.minimax.voice`。

- 参考音频建议：内容连贯的单人原声，避免背景音乐与多人叠声；过短（<10s）相似度低，过长（>3 分钟）会稀释音色特征
- 试听合成与网页实际合成使用同一组采样参数（从 `voice.minimax` 读取）
- 临时音色 **7 天内至少使用一次**，否则会被 MiniMax 删除
- 参数约束（speech-02-hd 实测）：`vol` / `pitch` 必须是整数（GMI 接口无此限制，支持小数），`sample_rate` 最高 32000，超出会返回 2013

角色引擎默认开启：

```json
"role_engine": { "enabled": true, "memory_limit": 200, "top_k": 5 }
```

`enabled=false` 时完全退回旧行为。记忆与状态按角色持久化在 `data/memory/{role}.json`、`data/state/{role}.json`。

位置感知（角色知道用户在哪，聊位置/天气/通勤能自然接话）：

```json
"location": { "enabled": true, "manual": "", "weather": true }
```

- 位置来源：仅 `manual`（手动配置，如「广东省深圳市南山区」）；自动定位（浏览器 GPS / IP）已下线。
- 手动位置存 `config.json` 的 `location.manual`，天气感知与对话注入只读该值；为空时位置/天气均不注入。
- 位置属隐私信息，仅本机保存；不向任何第三方发送位置数据（天气查询走 Open-Meteo，不含位置）。

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
| `MIMO_API_KEY` | 小米 MiMo（LLM 对话 + TTS 语音合成） |
| `DEEPSEEK_API_KEY` | DeepSeek（LLM 对话） |
| `ARK_API_KEY` | 火山方舟（LLM 对话） |
| `ALIYUN_API_KEY` | 阿里云（TTS 语音合成） |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope（备用/兼容） |
| `MINIMAX_API_KEY` | MiniMax（LLM 对话 + TTS 语音合成，voice.provider=minimax 时使用） |
| `GMI_API_KEY` | GMI 云（MiniMax TTS 的 `api_schema=gmi` 模式专用，与官方 Key 互不混用） |
| `MINIMAX_BILLING_MODE` | MiniMax 计费模式兜底（`payg` / `token_plan`，config 未配置时生效） |

**优先级**：`config.json` 中对应 provider 条目里填写的 `api_key` 优先；未填写时回退读取 `.env` 中的同名变量。

> 安全建议：密钥只写入 `.env`（已被 .gitignore 忽略），不要把真实 key 写进 `config.json` 并提交。

## 密钥与安全说明

- `.env.example` 是密钥模板：首次使用时复制为 `.env` 并填入真实密钥；`.env` 已在 .gitignore 中，不会入库。**密钥只存 `.env`，config.json 不再保存明文 key**（运行时由 `_apply_env_overrides` 自动注入）。
- `/api/status` 接口对密钥一律返回 `***+尾4` 掩码；设置页保存时后端识别掩码值并跳过该字段，不会覆盖已有配置。
- **访问口令保护**：在 `config.json` 顶层配置 `access_token`（或环境变量 `ACCESS_TOKEN`）后，所有页面与 API 均需口令才能访问，未登录请求会被重定向到 `/login` 或返回 401。口令请使用足够长度的随机串，弱口令有被爆破风险。
- **登录防护**：`/api/login` 口令比对使用 `hmac.compare_digest` 防时序侧信道；失败时固定 1 秒退避，拖慢公网在线爆破。会话 cookie 为 `HttpOnly` + `SameSite=Lax`（HTTPS 下 `Secure`），有效期 30 天。口令仍是唯一防线，务必使用足够长度的随机串。
- 开放远程访问（局域网 / ngrok）前**必须先配置访问口令**。
- 服务绑定 `0.0.0.0` 以支持局域网直连与隧道接入，安全边界由访问口令兜底。

## 手机远程访问（ngrok）

电脑跑本地服务、手机随时随地访问（当前实际使用 ngrok 免费版，不是 Cloudflare Tunnel）：

1. 在 `config.json` 顶层配置访问口令：`"access_token": "你的口令"`（或设置环境变量 `ACCESS_TOKEN`）。
2. 启动服务：`start.bat`（或 `restart_service.bat` 重启）。
3. 另开窗口启动隧道：`ngrok http 8000`。ngrok 免费版每次重启会换域名，启动后 URL 自动写入 `data/tunnel_url.txt`。
4. 手机浏览器打开该地址，输入访问口令即可使用（自动 HTTPS）；可「添加到主屏幕」像 App 一样运行。
5. ngrok 自带管理界面 <http://127.0.0.1:4040>，可查看流量与状态。

## 运行与环境说明

- **日志级别**：可用环境变量 `LOG_LEVEL` 控制（默认 `WARNING`，可设 `INFO` / `DEBUG`），例如 `set LOG_LEVEL=DEBUG` 后启动。
- **健康检查**：`GET /api/health` 可用于探活与依赖状态检查。
- **TTS 缓存**：合成音频缓存在 `data/tts_cache/`，清理阈值由 `config.json` 的 `tts_cache` 节配置（`max_files` 代码默认 500、`max_bytes` 默认 8GB、`clean_interval` 默认 3600 秒；`config.example.json` 给出同步盘友好的推荐值 200 / 1GB）。
- **本地 TTS 依赖的 venv 路径**（硬编码默认值，换机器需按此布局准备，风险已知、暂不可配）：
  - 本地 TTS：`~/.trae-cn/skills/local-tts`
  - Python 虚拟环境：`~/.openvino/venv/*`（如 `~/.openvino/venv/t2i-tts`）

## API

- `POST /api/chat` 对话（注入角色上下文，返回 `{reply, narration?, style}`；旁白分支角色返回 `narration`）
- `POST /api/greeting` 主动问候；`GET /api/state` / `DELETE /api/state` 查看/重置角色状态；`GET /api/roles/memories` 查看记忆；`GET /api/activity` 活动
- `POST /api/tts` 语音合成；`GET /api/tts/file` 取缓存音频
- `POST /api/upload` 附件上传；`GET /uploads/...` 附件访问
- `POST /api/search` 联网搜索；对话中模型输出 `[search:关键词]` 会自动触发
- `GET /api/status` 状态（密钥一律脱敏）；`GET /api/config` / `POST /api/config` 读写配置
- `GET /api/roles` 角色列表；`POST /api/roles/apply` 切换角色
- `GET /api/sessions` / `PUT /api/sessions` 会话持久化；`GET /api/sync/stream` 多端 SSE 同步
- `GET /api/story/status`、`GET /api/story/calendar`、`POST /api/story/jump|resume|result|flag` 2027 剧情分支（仅 story 角色）
- `POST /api/llm-models` 拉取云端模型列表；`POST /api/llm-quota` 查询 MiniMax Token Plan 额度
- `GET /api/weather` / `POST /api/weather/refresh` 天气；`GET /api/role-news` / `POST /api/role-news/refresh` / `POST /api/role-news/update` 角色现实动态
- `GET /login`、`POST /api/login`、`POST /api/logout` 访问口令登录
- `GET /api/health` 健康检查

## 目录

```
server.py               FastAPI 后端主程序（端口 8000；纯函数已下沉 server_pkg，本文件保留装配与有状态逻辑）
server_pkg/             后端纯函数包（text_utils 文本/标记/校验 + sessions_merge 多端合并；server.py 重导出，import server 引用不变）
role_engine.py          角色运行引擎（记忆/状态/时间/位置/天气/后处理）
story_kpl2027.py        2027 赛季剧情分支引擎（赛程生成/剧情时钟/情感状态机/剧情指引）
minimax_llm.py          MiniMax 云端文字生成适配器（OpenAI 兼容，payg / token_plan 双计费）
minimax_clone.py        MiniMax 声音克隆脚本
pet_process.py          桌宠视频 → 透明循环 WebP 批处理工具
config.example.json     配置模板（复制为 config.json 使用）
requirements.txt        Python 依赖
setup.bat / start.bat / restart_service.bat / run_tests.bat   初始化 / 启动 / 重启 / 测试
llm/                    llama.cpp 运行时（llm/bin）、模型（llm/models）与启动脚本
xiaoni-ai-persona/      前端页面源码（pages/ 单页 chat.html + css/chat.css + js/app.js + pet.js + 桌宠素材、pages/story.html 赛程表、PWA manifest/sw）
deploy/                 打包与部署脚本（pack_cloud / pack_update / webhook 自动部署）
download_site/          下载引导站
android/                安卓 APK 工程（已停止开发，仅存档）
docs-specs/             设计文档（角色引擎设计、大帅2027人设档案）与优化报告
data/                   运行时数据（会话、记忆、状态、TTS 缓存、上传，不入库）
```

## 测试

一键运行全部离线测试（无需密钥、无需启动服务）：

```bat
run_tests.bat
```

单独运行某个套件（用本地 TTS 的 venv python）：

```bat
REM 角色引擎：记忆/状态/时间感知/后处理
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_role_engine.py

REM 服务端辅助函数（配置读写、路径处理等）
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_server_helpers.py

REM 联网搜索（DuckDuckGo / Bing RSS 解析逻辑）
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_search.py

REM 附件上传与处理
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_attachments.py

REM 2027 赛季剧情分支（赛程生成/种子确定性/跳转/情感状态机，12 用例）
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_story_kpl2027.py

REM 配置读写 API
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_config_api.py

REM TTS 缓存清理策略
"%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" test_tts_cache.py
```

需要真实云端密钥才能运行的脚本：

- `test_mimo.py`：MiMo 云端对话/TTS 联调，需 `.env` 中配置 `MIMO_API_KEY`
- `test_tts.py`：TTS 合成联调，需对应云端密钥与参考音频 `data/voice_dashuai.wav`

端到端链路验证：

- `verify_chain.py`：串联 LLM → TTS 全链路验证，**需服务已在 8000 端口启动**后运行

### 测试协议：防止测试会话污染真实前端

历史教训：模拟器/脚本测试曾把"新对话"、假会话直接写进真实 `data/sessions.json`，
导致手机端会话列表多出几个空会话。现在按以下协议测试，可完全避免：

1. **模拟器 / 联调测试优先连隔离实例**（真正的"新通道"）：
   - 运行 `start_test_server.bat` 起测试后端（端口 **8010**，数据目录 `data-test/`，
     与真实服务完全隔离）；
   - 模拟器 App 设置页服务器地址填 `http://10.0.2.2:8010`；
   - 脚本测试设置 `VERIFY_BASE=http://127.0.0.1:8010`。测试数据只进 `data-test/`，
     真实 `data/` 与前端零污染，无需清理。
2. **确需直接打真实后端（8000/ngrok）时**：
   - 写入的会话 id 一律以 `t-` 开头（服务端 GET 默认过滤 + 前端兜底过滤，
     真实前端永远不会显示）；
   - 测完运行 `python cleanup_test_sessions.py --base http://127.0.0.1:8000`，
     以墓碑方式删除这些测试会话，明细记入 `data/test_cleanup.log`（只留日志记录）。
3. 查看被过滤的测试会话：`GET /api/sessions?include_test=1`。

## 本地 LLM 说明

> **2026-08-29 起，本地引擎已从界面下线**（设置面板与侧栏不再提供本地/云端切换，当前唯一引擎为云端 API）。后端 local 分支代码保留，换机复用时手动运行 `llm/start_llm.bat` 并把 config.json 的 `provider` 改回 `local` 即可恢复。

- **模型**：`llm/models/Qwen3.5-4B-Q4_K_M.gguf`（Q4_K_M 量化约 2.6GB，8GB 显存可全层 GPU 推理）。旧 Qwen3-4B 已移除。
- **思考模式**：Qwen3.5 系列默认开启思考模式，长人设下思考过程会吃光 `max_tokens` 导致空回复。`server.py` 已在 local 分支自动下发 `chat_template_kwargs.enable_thinking=false` 关闭思考，无需手动配置。
- **模型缺失时**：运行 `llm/start_llm.bat` 会调用 `llm/get_llm.py` 从多镜像（hf-mirror → HuggingFace → ModelScope）下载（模型缺失时脚本自动跳过，不会报错）；也可手动下载同名 GGUF 放入 `llm/models/`。
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
- 大帅·2027 分支人设与剧情设定（用户逐条确认定稿）：[docs-specs/大帅2027人设档案.md](docs-specs/大帅2027人设档案.md)
- 本地视觉模型（VLM）使用说明：[llm/README_VL.md](llm/README_VL.md)
