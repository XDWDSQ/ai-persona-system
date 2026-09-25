# -*- coding: utf-8 -*-
"""端到端验证：上传 HEIC 伪装 jpg -> 服务端应转码为真 JPEG -> 前端可显示 + 视觉链路可用

用法（需要服务已在 127.0.0.1:8000 运行）：
    set ACCESS_TOKEN=<口令>            &  rem 也可只读 config.json 顶端 access_token
    set HEIC_TEST_SRC=data\\uploads\\att_xxx.jpg
    python ops\\heic_upload_e2e_test.py

安全说明（2026-09 第九轮修正）：
  本脚本此前把访问口令 "520TDJ" 明文写死并已提交进公共仓库
  （GitHub XDWDSQ/ai-persona-system，见 cb34f91 / 041da2f）。口令一律改成
  从环境变量 / config.json 读取；若该口令仍在使用，请在 config.json 里改掉它。
"""
import http.cookiejar
import io
import json
import os
import pathlib
import sys
import urllib.request

BASE = os.getenv("HEIC_TEST_BASE", "http://127.0.0.1:8000").rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(os.getenv("HEIC_TEST_SRC", ROOT / "data/uploads/att_527b2237967a.jpg"))


def _load_token() -> str:
    """访问口令：环境变量 ACCESS_TOKEN 优先，其次本机 config.json 顶层"""
    token = (os.getenv("ACCESS_TOKEN") or "").strip()
    if token:
        return token
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("access_token") or "").strip()
    except Exception:
        return ""


TOKEN = _load_token()
if not SRC.exists():
    sys.exit(f"[SKIP] 测试源文件不存在：{SRC}（用 HEIC_TEST_SRC 指定一个 HEIC 伪装 jpg）")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. 登录（未设访问口令时服务端不校验，跳过登录）
if TOKEN:
    req = urllib.request.Request(BASE + "/api/login", data=json.dumps({"token": TOKEN}).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=20)
    print("登录:", resp.status)
else:
    print("[WARN] 未取到访问口令（ACCESS_TOKEN / config.json 均空），按无门禁模式继续")

# 2. 上传 HEIC（模拟手机：文件名 9775.jpg，内容 HEIC）
raw = SRC.read_bytes()
print(f"源文件: {SRC.name} {len(raw)} 字节, magic={raw[:12].hex()}")
boundary = "----wbTest" + "123456"
body = io.BytesIO()
body.write(f"--{boundary}\r\n".encode())
body.write(b'Content-Disposition: form-data; name="files"; filename="9775.jpg"\r\n')
body.write(b"Content-Type: image/jpeg\r\n\r\n")
body.write(raw)
body.write(f"\r\n--{boundary}--\r\n".encode())
req = urllib.request.Request(BASE + "/api/upload", data=body.getvalue(),
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
resp = opener.open(req, timeout=60)
up = json.loads(resp.read().decode())
print("上传返回:", json.dumps(up, ensure_ascii=False))
f = up["files"][0]
url = f["url"]
print(f"附件 URL: {url}, mime={f['mime']}, size={f['size']}")

# 3. 下载该 URL，检查存盘内容是否为真 JPEG
req = urllib.request.Request(BASE + url)
resp = opener.open(req, timeout=20)
stored = resp.read()
print(f"服务器返回文件: {len(stored)} 字节, magic={stored[:12].hex()}")
if stored[:3] == b"\xff\xd8\xff":
    print("OK 存盘已是标准 JPEG（浏览器可直接显示）")
elif stored[4:8] == b"ftyp":
    print("[FAIL] 仍是 HEIC，转码失败")
else:
    print("[WARN] 未知格式")

# 4. 带该附件调 /api/chat，验证视觉链路
payload = json.dumps({
    "message": "这张照片里是什么场景？请仔细描述",
    "history": [],
    "attachments": [{"name": f["name"], "url": url, "kind": "image"}],
    "stream": False,
}).encode()
req = urllib.request.Request(BASE + "/api/chat", data=payload,
                             headers={"Content-Type": "application/json"})
resp = opener.open(req, timeout=120)
chat = json.loads(resp.read().decode())
print("视觉回复:", str(chat.get("reply"))[:200])
print("vision_used:", chat.get("vision_used"))
