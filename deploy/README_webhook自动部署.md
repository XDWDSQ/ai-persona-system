# Gitee Webhook 自动部署（部署机使用）

开发机 `git push` 后，部署机自动拉取最新代码并重启服务，无需任何手动操作。

## 原理

```
开发机 git push
   └─> Gitee 仓库 Webhook（push 事件）
         └─> cloudflared 隧道（部署机 → 公网）
               └─> webhook_autodeploy.py（部署机，监听 9001）
                     ├─> git pull 拉取最新代码
                     └─> 重启 uvicorn 服务（8000 端口）
```

## 部署机首次配置

1. 克隆仓库（私有仓库需输入 Gitee 账号密码或私人令牌）：

   ```
   git clone https://gitee.com/XDWDSQ/ai-persona-system.git
   cd ai-persona-system
   ```

2. 安装依赖并配置密钥：

   ```
   python -m venv venv
   venv\Scripts\pip install -r requirements.txt     # Windows
   # 或 source venv/bin/pip install -r requirements.txt   # Linux
   copy .env.example .env      # Windows（Linux: cp .env.example .env）
   # 编辑 .env，填入部署机自己的 API 密钥
   ```

3. 安装 cloudflared（首次需要）：

   ```
   winget install --id Cloudflare.cloudflared -e     # Windows
   # Linux: 按 https://developers.cloudflare.com/cloudflared/ 安装
   ```

4. 启动自动部署服务（保持窗口开启即可）：

   ```
   python deploy\webhook_autodeploy.py
   # 若本机未登录 Gitee 凭据，加参数: --gitee-token <私人令牌>
   ```

   脚本会自动完成：
   - 启动 cloudflared 隧道（公网地址每次变化没关系）
   - 把最新隧道地址注册到 Gitee 仓库 Webhook（自动创建/更新）
   - 启动本地接收服务（9001 端口，回调密码在 data/webhook_secret.txt）
   - 收到 push 事件 → `git pull` → 重启 8000 服务

5. 用计划任务/开机自启保持常驻（可选）：
   - Windows：任务计划程序，触发器"登录时"，操作 `python deploy\webhook_autodeploy.py`，工作目录为项目根目录
   - Linux：systemd 服务或 supervisor

## 日常使用

开发机每次改完代码：

```
git add -A && git commit -m "改动说明" && git push
```

部署机数秒内自动更新并重启，无需人工干预。

## 常见问题

- **git pull 失败（本地有改动/冲突）**：脚本会记录日志并跳过重启，需人工处理部署机上的本地改动。
- **隧道频繁变化**：Webhook 地址由脚本在每次启动时自动更新，无需手动改 Gitee 配置。
- **接收端口被占用**：用 `--port 其他端口` 指定。
- **应用服务端口不同**：用 `--app-port 端口` 指定（默认 8000）。
- **日志**：脚本输出即日志；隧道日志在 `data/webhook_tunnel.log`；回调密码在 `data/webhook_secret.txt`。
