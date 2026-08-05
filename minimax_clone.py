# -*- coding: utf-8 -*-
"""MiniMax 声音克隆（大帅音色）一键脚本。

前置条件：MiniMax 账号已完成实名认证（个人/企业认证）。未认证时接口返回
status_code=2038（voice clone user forbidden），请在 platform.minimaxi.com
「账户管理 > 账户信息」完成认证后重跑本脚本。

流程：
1. 读取 .env 的 MINIMAX_API_KEY
2. 检查 data/voice_dashuai.wav 参考音频质量（时长/采样率/声道，超范围给出建议）
3. 上传 data/voice_dashuai.wav 作为复刻音频
4. 调用 /v1/voice_clone 克隆音色（voice_id: dashuai_clone_0804）
5. 用克隆音色试听合成一句（同时激活临时音色，7 天内需使用一次否则会被删除），
   采样参数（speed/vol/pitch/sample_rate）从 config.json 的 voice.minimax 读取，与网页实际合成保持一致
6. 成功后自动把 voice_id 写回 config.json 的 voice.minimax.voice

用法：python minimax_clone.py
"""
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.json"
VOICE_ID = "dashuai_clone_0804"
MODEL = "speech-02-hd"
REF_WAV = DATA_DIR / "voice_dashuai.wav"


def _load_dotenv() -> dict:
    env: dict[str, str] = {}
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def get_key() -> str:
    env = _load_dotenv()
    key = env.get("MINIMAX_API_KEY", "")
    return key or input("请输入 MiniMax API Key: ").strip()


def post_json(url: str, payload: dict, key: str):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:800]
    except Exception as e:
        return -1, {"err": f"{type(e).__name__}: {e}"}


def upload_file(url: str, key: str, purpose: str, path: Path):
    import uuid
    boundary = "----mm" + uuid.uuid4().hex
    data = path.read_bytes()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode() + data + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        url, data=b"".join(parts),
        headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:800]
    except Exception as e:
        return -1, {"err": f"{type(e).__name__}: {e}"}


def update_config_voice_id(voice_id: str) -> None:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"读取 config.json 失败（{e}），请检查文件是否被占用或损坏")
        return
    cfg.setdefault("voice", {}).setdefault("minimax", {})["voice"] = voice_id
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"写入 config.json 失败（{e}），请手动把 voice.minimax.voice 设为 {voice_id}")
        return
    print(f"已写入 config.json: voice.minimax.voice = {voice_id}")


def load_minimax_params() -> dict:
    """从 config.json 读 MiniMax 采样参数（与 server.py 网页实际合成保持一致）。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        m = cfg.get("voice", {}).get("minimax", {})
    except (OSError, json.JSONDecodeError):
        m = {}
    return {
        "speed": min(max(float(m.get("speed", 1.0)), 0.5), 2.0),
        "vol": min(max(int(round(float(m.get("vol", 1.0)))), 0), 10),   # MiniMax vol 要求 int
        "pitch": min(max(int(round(float(m.get("pitch", 0)))), 0), 10),  # MiniMax pitch 要求 int
        "sample_rate": min(max(int(m.get("sample_rate", 32000)), 8000), 48000),
    }


def check_ref_audio(path: Path) -> None:
    """参考音频质量检查：时长/采样率/声道，超范围只给建议不阻断。"""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            nch = w.getnchannels()
            fr = w.getframerate()
            dur = w.getnframes() / fr
    except Exception as e:
        print(f"  [提示] 无法解析参考音频信息（{e}），跳过质量检查")
        return
    print(f"      参考音频: {dur:.1f}s / {fr}Hz / {nch} 声道")
    if dur < 10:
        print("      [建议] 音频不足 10 秒，克隆相似度可能偏低，建议 10~120 秒、内容清晰连贯的人声片段")
    elif dur > 180:
        print("      [建议] 音频超过 3 分钟，建议截取 10~120 秒最像目标音色的片段，过长会稀释克隆特征")
    if nch != 1:
        print("      [建议] 非单声道，建议转成单声道 16bit PCM 后再克隆")
    if fr < 16000:
        print("      [建议] 采样率低于 16kHz，建议用 44.1kHz 单声道高质量音频作为参考")


def main() -> int:
    key = get_key()
    if not key:
        print("缺少 MINIMAX_API_KEY")
        return 1
    if not REF_WAV.exists():
        print(f"缺少参考音频: {REF_WAV}")
        return 1
    base = "https://api.minimaxi.com/v1"

    # 1. 参考音频质量检查
    print(f"[1/5] 检查参考音频 {REF_WAV.name} ...")
    check_ref_audio(REF_WAV)

    # 2. 上传复刻音频
    print(f"[2/5] 上传参考音频 {REF_WAV.name} ...")
    st, data = upload_file(f"{base}/files/upload", key, "voice_clone", REF_WAV)
    file_id = (data or {}).get("file", {}).get("file_id") if isinstance(data, dict) else None
    if not file_id:
        print(f"上传失败: {st} {data}")
        return 1
    print(f"      file_id = {file_id}")

    # 3. 发起克隆
    print(f"[3/5] 克隆音色 voice_id={VOICE_ID} ...")
    st, data = post_json(f"{base}/voice_clone", {
        "file_id": file_id,
        "voice_id": VOICE_ID,
        "text": "兄弟，我是大帅，成都AG超玩会的游走位。这声音，是我自己的。",
        "model": MODEL,
        "need_noise_reduction": True,
        "need_volume_normalization": True,
    }, key)
    br = (data or {}).get("base_resp", {}) if isinstance(data, dict) else {}
    if br.get("status_code") != 0:
        print(f"克隆失败: status_code={br.get('status_code')} {br.get('status_msg')}")
        print("提示：2038 表示账号未完成实名认证，请在 platform.minimaxi.com 完成个人/企业认证后重跑。")
        return 1
    print("      克隆成功")

    # 4. 用克隆音色合成一句（激活临时音色，7 天内需使用一次），采样参数与网页一致
    print("[4/5] 用克隆音色试听合成 ...")
    p = load_minimax_params()
    print(f"      采样参数: speed={p['speed']} vol={p['vol']} pitch={p['pitch']} sample_rate={p['sample_rate']}")
    st, data = post_json(f"{base}/t2a_v2", {
        "model": MODEL,
        "text": "兄弟，游走位是我的主场。",
        "stream": False,
        "language_boost": "Chinese",
        "voice_setting": {"voice_id": VOICE_ID, "speed": p["speed"], "vol": p["vol"], "pitch": p["pitch"]},
        "audio_setting": {"sample_rate": p["sample_rate"], "format": "wav", "channel": 1},
    }, key)
    br = (data or {}).get("base_resp", {}) if isinstance(data, dict) else {}
    audio_hex = (data or {}).get("data", {}).get("audio") if isinstance(data, dict) else None
    if br.get("status_code") != 0 or not audio_hex:
        print(f"试听合成失败: {br.get('status_code')} {br.get('status_msg')}")
        return 1
    out = DATA_DIR / "outputs" / "minimax_clone_dashuai.wav"
    try:
        out.write_bytes(bytes.fromhex(audio_hex.replace(" ", "").replace("\n", "")))
    except ValueError as e:
        print(f"音频 hex 解码失败（{e}），试听文件未保存")
        return 1
    print(f"      试听已保存: {out}")

    # 5. 写回配置
    print("[5/5] 更新 config.json ...")
    update_config_voice_id(VOICE_ID)
    print("\n完成！在网页左下角点语音切换切到 MiniMax 即可用大帅克隆音色。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
