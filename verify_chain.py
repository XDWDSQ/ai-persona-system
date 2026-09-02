# -*- coding: utf-8 -*-
"""全链路验证（对齐当前 API 契约）:
  /api/status 现有字段校验 + /api/chat 对话 + /api/tts（按当前 voice.provider 走验证路径）

注意：MiMo TTS 已下线（server 对 voice.provider=mimo 显式返回 400），
因此 TTS 验证按 status.voice_provider 决定路径：
  aliyun → 需 aliyun_configured 后合成；local → 直接合成；mimo → 跳过并说明。
"""
import json
import os
import time
import urllib.error
import urllib.request
import urllib.parse
import ipaddress
import socket
from pathlib import Path

_BASE = os.getenv("VERIFY_BASE", "http://127.0.0.1:8000")
_parsed_base = urllib.parse.urlparse(_BASE)
if _parsed_base.scheme not in ("http", "https") or not _parsed_base.hostname:
    raise SystemExit(f"VERIFY_BASE 必须是 http/https 地址: {_BASE}")
BASE = _BASE.rstrip("/")
# 支持访问口令：VERIFY_TOKEN 环境变量 → Authorization: Bearer
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip()
OUT_DIR = Path(__file__).resolve().parent / "data"   # 基于本文件路径，不依赖 CWD


def _headers():
    h = {"Content-Type": "application/json"}
    if VERIFY_TOKEN:
        h["Authorization"] = f"Bearer {VERIFY_TOKEN}"
    return h


def _http_error(e: urllib.error.HTTPError) -> str:
    body = ""
    try:
        body = e.read(500).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    return f"HTTP {e.code} {body[:300]}"


def post(path, payload=None, binary=False):
    url = BASE + path
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise RuntimeError(f"非法地址: {url}")
    ip = ipaddress.ip_address(socket.gethostbyname(p.hostname))
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise RuntimeError(f"禁止访问内网地址: {url}")
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(url, data=data, headers=_headers())
    try:
        r = urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"POST {path} 失败: {_http_error(e)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"POST {path} 网络错误: {e}") from e
    body = r.read()
    if binary:
        return body
    return json.loads(body)


def get(path):
    url = BASE + path
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise RuntimeError(f"非法地址: {url}")
    ip = ipaddress.ip_address(socket.gethostbyname(p.hostname))
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise RuntimeError(f"禁止访问内网地址: {url}")
    req = urllib.request.Request(url, headers=_headers())
    try:
        r = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GET {path} 失败: {_http_error(e)}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"GET {path} 网络错误: {e}") from e
    return json.loads(r.read())


try:
    # 1) 状态（只读当前契约里真实存在的字段）
    d = get("/api/status")
    print("status: provider=%s cloud=%s active_online=%s cloud_has_key=%s active_role=%s "
          "voice_provider=%s voice_registered=%s aliyun_configured=%s"
          % (d["provider"], d["cloud"]["provider"], d["active_online"], d["cloud_has_key"],
             d["active_role"], d["voice_provider"], d["voice_registered"], d["aliyun_configured"]))
    if not d["cloud_has_key"]:
        print("WARN: 当前云端供应商没有 API Key（检查 .env 注入或 config.json）")

    # 2) 对话（当前 provider，走 /api/chat）
    t0 = time.time()
    r = post("/api/chat", {"message": "大帅，你打王者最拿手的是什么位置？", "history": []})
    print("chat[%.1fs]: %s" % (time.time() - t0, r["reply"][:90]))

    # 3) TTS：按 status.voice_provider 决定验证路径
    vp = d["voice_provider"]
    if vp == "mimo":
        print("tts: 跳过 —— MiMo TTS 已下线（server 显式 400），请在设置里切到 aliyun/local")
    elif vp == "aliyun" and not d["aliyun_configured"]:
        print("tts: 跳过 —— 阿里云 Key 未配置（aliyun_configured=False）")
    else:
        t0 = time.time()
        audio = post("/api/tts", {"text": r["reply"]}, binary=True)
        out_path = OUT_DIR / "tts_verify.wav"
        out_path.write_bytes(audio)
        print("tts[%s][%.1fs]: %d bytes wav -> %s" % (vp, time.time() - t0, len(audio), out_path))

    print("verify_chain: OK")
except RuntimeError as e:
    print(f"verify_chain: FAILED — {e}")
    raise SystemExit(1)
