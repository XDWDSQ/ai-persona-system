# AI 拟人系统 · 云电脑部署指南

把系统搬到云电脑（联通云 / 腾讯云桌面等 Windows 云电脑），实现 **电脑关机手机也能用**。

## 部署原理（1 分钟理解）

- 云电脑 = 一台永远开机的远程电脑。系统全部跑在云电脑上。
- **数据单向搬家**：本地 `data/`（会话、记忆、状态、音色）一次性拷到云电脑，之后聊天记录只在云电脑上生成。
- 本地电脑退化为开发机（改代码），不再跑服务。手机直接访问云电脑。

## 步骤一：本地打包（已代劳）

本地项目根目录运行：

```
python deploy/pack_cloud.py
```

生成 `cloud_deploy.zip`（约 30MB，已排除本地模型 llm/ 等无用大文件）。

## 步骤二：把包传到云电脑

联通云电脑一般通过客户端/共享盘传文件。把 `cloud_deploy.zip` 拷进去，解压到任意目录（例如 `C:\AI拟人系统`）。

## 步骤三：云电脑上装 Python（只需一次）

1. 打开 https://www.python.org/downloads/ 下载 **Python 3.11**（Windows 版）
2. 安装时 **务必勾选 "Add python.exe to PATH"**
3. 命令行验证：`python --version` 能输出版本即可

## 步骤四：一键安装并启动

在解压目录里双击运行 `install_cloud.bat`（或在命令行执行）：

```
install_cloud.bat
```

它会自动：安装 5 个依赖包 → 后台启动服务（日志 `data/service.log`）→ 检查健康状态。

服务地址：`http://127.0.0.1:8000`（云电脑本机测试用）

## 步骤五：手机访问（关键）

云电脑默认无法从外网直接访问，需要开一条隧道，二选一：

### 方案 A：cloudflared 隧道（无公网 IP 也适用，推荐先用这个）

1. 云电脑上安装 cloudflared：
   ```
   winget install --id Cloudflare.cloudflared -e
   ```
   （winget 不可用就去 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ 下载 exe）

2. 运行 `start_tunnel.bat`，窗口会显示手机访问地址并自动保存到 `data\tunnel_url.txt`：
   ```
   https://xxxx.trycloudflare.com
   ```

3. 手机浏览器打开该地址，输入访问口令：**520TDJ**（见 `config.json` 的 access_token）

### 方案 B：云电脑有公网 IP

云电脑控制台查看是否分配公网 IP，有的话：
手机直接访问 `http://<公网IP>:8000`，输入口令即可。
（需在云电脑防火墙放行 8000 端口；联通云可能需要在控制台安全组放行）

## 云电脑上不可用的功能（已自动降级）

| 功能 | 原因 | 表现 |
| --- | --- | --- |
| 语音输入（ASR） | 本地模型不在云上 | 语音按钮会提示失败，用文字即可 |
| 图片理解 | 本地视觉模型不在云上 | 发图后走纯文本兜底回复 |
| 本地 TTS | 云上无本地音色 | 当前配置已是云端 MiniMax，不受影响 |

文字聊天、语音合成播放、角色记忆、联网搜索、天气位置 **全部正常**。

## 常见问题

- **手机打不开**：隧道地址每次启动会变，先看 `data\tunnel_url.txt` 最新地址；确认云电脑没关机、服务在跑（`curl http://127.0.0.1:8000/api/health`）。
- **重启云电脑后**：重新运行 `install_cloud.bat`（或开机自启），再运行 `start_tunnel.bat` 拿新地址。
- **想保留本地聊天记录**：本地 `data/sessions.json` 已打包带走；如果之后本地又聊了，把本地的 `data/sessions.json`、`data/memory/`、`data/state/` 再拷到云电脑覆盖即可（单向）。
- **云电脑关机**：服务停，手机连不上 —— 保持云电脑在线（包月套餐）。
