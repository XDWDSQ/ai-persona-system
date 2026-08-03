# -*- coding: utf-8 -*-
"""全链路验证: /api/chat (MiMo 对话, 大帅人设) + /api/tts (MiMo VoiceClone 大帅音色)"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, payload=None, binary=False):
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=180)
    body = r.read()
    if binary:
        return body
    return json.loads(body)


# 1) 状态
d = json.load(urllib.request.urlopen(BASE + "/api/status"))
print("status: provider=%s cloud=%s active_role=%s voice_provider=%s voice_registered=%s mimo_configured=%s"
      % (d["provider"], d["cloud"]["provider"], d["active_role"], d["voice_provider"], d["voice_registered"], d["mimo_configured"]))

# 2) 对话（大帅人设 + mimo-v2.5）
t0 = time.time()
r = post("/api/chat", {"message": "大帅，你打王者最拿手的是什么位置？", "history": []})
print("chat[%.1fs]: %s" % (time.time() - t0, r["reply"][:90]))

# 3) TTS（VoiceClone，带 voice_ref 参考音频）
t0 = time.time()
audio = post("/api/tts", {"text": r["reply"]}, binary=True)
with open("data/tts_verify.wav", "wb") as f:
    f.write(audio)
print("tts[%.1fs]: %d bytes wav" % (time.time() - t0, len(audio)))
