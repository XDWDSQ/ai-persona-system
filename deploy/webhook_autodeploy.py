#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 拟人系统 · Gitee Webhook 自动部署服务（部署机运行）

一键完成三件事：
  1. 用 cloudflared 开隧道，把本机 hook 端口暴露到公网（隧道地址每次变化也没关系）
  2. 自动把最新的隧道地址注册到 Gitee 仓库的 Webhook（push 事件回调）
  3. 收到 push 事件后自动执行「git pull + 重启应用服务」

用法（部署机，Windows / Linux 均可）：
  python webhook_autodeploy.py [--gitee-token 令牌] [--port 9001]

说明：
  - 依赖仅 Python 标准库 + cloudflared（无需任何第三方 pip 包）
  - Gitee 令牌：可用 --gitee-token 传入，或设置环境变量 GITEE_TOKEN，
    或本机已用 git 登录过 Gitee（从凭据自动读取）
  - 首次运行自动生成回调密码并保存到 data/webhook_secret.txt，
    该密码会同步注册到 Gitee Webhook，用于请求校验
  - 应用重启默认：uvicorn server:app，端口 8000，可用 --app-port 修改

按 Ctrl+C 停止（会自动关闭隧道）。
"""

import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")


def _safe_data_path(name: str) -> str:
    """data 目录内路径校验：规范化后必须位于 DATA_DIR 内（禁止 ../ 越界）。"""
    p = os.path.realpath(os.path.join(DATA_DIR, name))
    data_root = os.path.realpath(DATA_DIR)
    if not (p == data_root or p.startswith(data_root + os.sep)):
        raise ValueError(f"路径越界: {p}")
    return p


SECRET_FILE = _safe_data_path("webhook_secret.txt")
TUNNEL_LOG = _safe_data_path("webhook_tunnel.log")
GITEE_API = "https://gitee.com/api/v5"


def _check_https_api(url: str) -> str:
    """仅允许 HTTPS 的 API 地址，解析后阻断私网/环回/链路本地地址。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"非法 API 地址: {url}")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError):
        raise ValueError(f"无法解析主机: {parsed.hostname}")
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError(f"禁止访问内网地址: {url}")
    return url


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, cwd=None, timeout=120, shell=False):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, shell=shell)


# ---------- Gitee 凭据与 API ----------

def detect_gitee_token(arg_token):
    if arg_token:
        return arg_token
    env = os.environ.get("GITEE_TOKEN")
    if env:
        return env
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=gitee.com\n\n",
            capture_output=True, text=True, timeout=10)
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception:
        pass
    return None


def detect_repo():
    r = run(["git", "remote", "get-url", "origin"], cwd=ROOT)
    m = re.search(r"(?:\.com|\.cn)[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", r.stdout.strip())
    return m.group(1) if m else None


def gitee_api(method, path, token, payload=None):
    url = f"{_check_https_api(GITEE_API)}{path}?access_token={token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=25) as resp:
            body = resp.read().decode() or "{}"
            return resp.status, json.loads(body)
    except HTTPError as e:
        body = e.read().decode() or "{}"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"message": body}


# ---------- 隧道 ----------

def find_cloudflared():
    which = shutil.which("cloudflared")
    if which:
        return which
    candidates = [
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        "/usr/local/bin/cloudflared",
        "/usr/bin/cloudflared",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def start_tunnel(cfd, hook_port, stop_event):
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(TUNNEL_LOG):
        try:
            os.remove(TUNNEL_LOG)
        except OSError:
            pass
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(TUNNEL_LOG, "ab") as lf:
        proc = subprocess.Popen(
            [cfd, "tunnel", "--url", f"http://127.0.0.1:{hook_port}", "--no-autoupdate"],
            stdout=lf, stderr=subprocess.STDOUT, creationflags=flags)
    url = None
    for _ in range(120):
        if proc.poll() is not None:
            break
        try:
            text = open(TUNNEL_LOG, encoding="utf-8", errors="ignore").read()
        except Exception:
            text = ""
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        if m:
            url = m.group(0)
            break
        time.sleep(0.5)
    return proc, url


# ---------- Webhook 注册 ----------

def register_webhook(token, secret, tunnel_url, repo):
    hook_url = tunnel_url.rstrip("/") + "/webhook"
    payload = {
        "url": hook_url,
        "password": secret,
        "push_events": True,
        "issues_events": False,
        "note_events": False,
        "merge_requests_events": False,
        "tag_push_events": False,
        "wiki_events": False,
    }
    status, data = gitee_api("GET", f"/repos/{repo}/hooks", token)
    hooks = data if isinstance(data, list) else []
    for h in hooks:
        if h.get("url", "").rstrip("/").endswith("/webhook"):
            log(f"更新已有 Webhook #{h['id']} -> {hook_url}")
            return gitee_api("PATCH", f"/repos/{repo}/hooks/{h['id']}", token, payload)
    log(f"创建新 Webhook -> {hook_url}")
    return gitee_api("POST", f"/repos/{repo}/hooks", token, payload)


# ---------- 自动部署 ----------

def restart_app(app_port):
    if os.name == "nt":
        out = run(["netstat", "-ano"]).stdout
        pids = set()
        for line in out.splitlines():
            if f":{app_port} " in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    pids.add(parts[-1])
        for pid in pids:
            run(["taskkill", "/F", "/PID", pid])
            log(f"已停止旧服务进程 {pid}")
        time.sleep(2)
        flags = subprocess.CREATE_NEW_CONSOLE
    else:
        run(["pkill", "-f", "uvicorn server:app"])
        time.sleep(2)
        flags = 0
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "0.0.0.0", "--port", str(app_port)],
        cwd=ROOT, creationflags=flags)
    log(f"服务已重启（端口 {app_port}）")


def deploy(app_port):
    try:
        r = run(["git", "pull", "--ff-only"], cwd=ROOT, timeout=300)
        tail = (r.stdout + r.stderr).strip().splitlines()
        log(f"git pull 退出码 {r.returncode}: {tail[-1] if tail else '无输出'}")
        if r.returncode != 0:
            log("git pull 失败（可能有本地改动或冲突），跳过重启，请人工检查")
            return
        if "Already up to date" in r.stdout:
            log("代码已是最新，无需重启")
            return
        restart_app(app_port)
    except Exception as e:
        log(f"自动部署异常: {e}")


# ---------- HTTP 接收 ----------

class HookHandler(BaseHTTPRequestHandler):
    secret = ""
    app_port = 8000

    def do_POST(self):
        if urlparse(self.path).path != "/webhook":
            self.send_error(404)
            return
        got = self.headers.get("X-Gitee-Token", "")
        if not got or got != self.secret:
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
        log("收到 push 事件，开始自动部署")
        threading.Thread(target=deploy, args=(self.app_port,), daemon=True).start()

    def log_message(self, fmt, *args):
        pass


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="AI 拟人系统 Webhook 自动部署")
    ap.add_argument("--gitee-token", default=None, help="Gitee 私人令牌")
    ap.add_argument("--port", type=int, default=9001, help="hook 监听端口（默认 9001）")
    ap.add_argument("--app-port", type=int, default=8000, help="应用服务端口（默认 8000）")
    args = ap.parse_args()

    repo = detect_repo()
    if not repo:
        log("[X] 无法识别 Gitee 仓库地址，请确认在项目目录内运行")
        return 1

    token = detect_gitee_token(args.gitee_token)
    if not token:
        log("[X] 未找到 Gitee 令牌。请用 --gitee-token 传入，或设置环境变量 GITEE_TOKEN")
        return 1

    os.makedirs(DATA_DIR, exist_ok=True)
    secret_file = Path(DATA_DIR) / "webhook_secret.txt"
    if secret_file.exists():
        secret = secret_file.read_text(encoding="utf-8").strip()
    else:
        secret = secrets.token_hex(16)
        secret_file.write_text(secret + "\n", encoding="utf-8")
        log(f"已生成回调密码（保存于 data/webhook_secret.txt）")

    cfd = find_cloudflared()
    if not cfd:
        log("[X] 未找到 cloudflared，请先安装：winget install --id Cloudflare.cloudflared -e")
        return 1

    log("正在启动 cloudflared 隧道 ...")
    stop_event = threading.Event()
    proc, tunnel_url = start_tunnel(cfd, args.port, stop_event)
    if not tunnel_url:
        log("[X] 隧道地址解析失败，请检查 data/webhook_tunnel.log")
        return 1
    log(f"隧道地址: {tunnel_url}")

    status, data = register_webhook(token, secret, tunnel_url, repo)
    if status in (200, 201):
        log(f"Webhook 注册成功（HTTP {status}），push 后自动部署已就绪")
    else:
        log(f"[!] Webhook 注册返回 {status}: {data}")

    HookHandler.secret = secret
    HookHandler.app_port = args.app_port
    server = HTTPServer(("0.0.0.0", args.port), HookHandler)
    log(f"Webhook 接收服务已启动: http://0.0.0.0:{args.port}/webhook")
    log("按 Ctrl+C 停止（自动关闭隧道）")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if proc and proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.terminate()
        log("已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
