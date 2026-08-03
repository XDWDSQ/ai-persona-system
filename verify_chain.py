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
import urllib.request

BASE = os.getenv("VERIFY_BASE", "http://127.0.0.1:8000")


def post(path, payload=None, binary=False):
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=180)
    body = r.read()
    if binary:
        return body
    return json.loads(body)


# 1) 状态（只读当前契约里真实存在的字段）
d = json.load(urllib.request.urlopen(BASE + "/api/status"))
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
    with open("data/tts_verify.wav", "wb") as f:
        f.write(audio)
    print("tts[%s][%.1fs]: %d bytes wav" % (vp, time.time() - t0, len(audio)))

print("verify_chain: OK")
