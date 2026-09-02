# -*- coding: utf-8 -*-
"""端到端验证：上传 HEIC 伪装 jpg -> 服务端应转码为真 JPEG -> 前端可显示 + 视觉链路可用"""
import json
import pathlib
import urllib.request
import http.cookiejar
import io
import mimetypes

BASE = "http://127.0.0.1:8000"
TOKEN = "520TDJ"
SRC = pathlib.Path("data/uploads/att_527b2237967a.jpg")  # 用户之前失败的 HEIC 伪装 jpg

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. 登录
req = urllib.request.Request(BASE + "/api/login", data=json.dumps({"token": TOKEN}).encode(),
                             headers={"Content-Type": "application/json"})
resp = opener.open(req, timeout=20)
print("登录:", resp.status)

# 2. 上传 HEIC（模拟手机：文件名 9775.jpg，内容 HEIC）
raw = SRC.read_bytes()
print(f"源文件: {SRC.name} {len(raw)} 字节, magic={raw[:12].hex()}")
boundary = "----wbTest" + "123456"
body = io.BytesIO()
body.write(f"--{boundary}\r\n".encode())
body.write(f'Content-Disposition: form-data; name="files"; filename="9775.jpg"\r\n'.encode())
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
    print("✅ 存盘已是标准 JPEG（浏览器可直接显示）")
elif stored[4:8] == b"ftyp":
    print("❌ 仍是 HEIC，转码失败")
else:
    print("⚠️ 未知格式")

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
