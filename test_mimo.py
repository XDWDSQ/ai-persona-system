# -*- coding: utf-8 -*-
"""验证 MiMo key:拉模型列表 + 对话 + 观察 TTS 相关模型名。"""
import json
import os
import httpx
from pathlib import Path

# 加载 .env 文件（若存在）
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

KEY = os.getenv("MIMO_API_KEY", "")
BASE = "https://api.xiaomimimo.com/v1"
H = {"Authorization": f"Bearer {KEY}"}

r = httpx.get(f"{BASE}/models", headers=H, timeout=30)
print("models status:", r.status_code)
if r.status_code == 200:
    models = [m["id"] for m in r.json().get("data", [])]
    print("count:", len(models))
    print("tts/clone:", [m for m in models if "tts" in m.lower()])
    print("chat-ish:", [m for m in models if not any(k in m.lower() for k in ("tts", "asr", "voice", "audio", "embed", "image", "whisper"))][:10])

r = httpx.post(f"{BASE}/chat/completions", headers=H, json={
    "model": "mimo-v2.5",
    "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
    "max_tokens": 60,
}, timeout=60)
print("chat status:", r.status_code)
if r.status_code == 200:
    print("reply:", r.json()["choices"][0]["message"]["content"][:80])
else:
    print("chat err:", r.text[:300])
