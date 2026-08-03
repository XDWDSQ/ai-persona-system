# -*- coding: utf-8 -*-
"""排查 MiMo TTS:先测通用 TTS 格式,再测 VoiceClone 参考音频字段。"""
import base64
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
H = {"Authorization": f"Bearer {KEY}", "api-key": KEY}
REF = Path(r"d:\Users\31557\Desktop-快速访问\AI拟人系统\data\voice_dashuai.wav")

style = "用自然松弛的电竞选手语气，但情绪跟随对话内容自动变化"
text = "兄弟，游走位是我的主场。"


def try_tts(name, payload):
    try:
        r = httpx.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=120)
        if r.status_code == 200:
            data = r.json()["choices"][0]["message"]["audio"]["data"]
            b = base64.b64decode(data)
            print(f"[{name}] OK {len(b)} bytes")
            return b
        print(f"[{name}] HTTP {r.status_code}: {r.text[:400]}")
    except Exception as exc:
        print(f"[{name}] EXC {type(exc).__name__}: {str(exc)[:300]}")
    return None


# 1) 通用 TTS（无参考音频）
try_tts("tts-basic", {
    "model": "mimo-v2.5-tts",
    "messages": [{"role": "user", "content": style}, {"role": "assistant", "content": text}],
    "audio": {"voice": "mimo_default", "format": "wav"},
})

# 2) VoiceClone:参考音频通过 audio.voice 传 DataURL（服务端错误提示确认）
b64 = base64.b64encode(REF.read_bytes()).decode()
data_url = f"data:audio/wav;base64,{b64}"
try_tts("voiceclone-dataurl", {
    "model": "mimo-v2.5-tts-voiceclone",
    "messages": [{"role": "user", "content": style}, {"role": "assistant", "content": text}],
    "audio": {"voice": data_url, "format": "wav",
              "reference_text": "揉碎，再揉一次，一次一次再次揉一下。"},
})
