# -*- coding: utf-8 -*-
"""AI 拟人系统后端：聊天(LLM) + 声音克隆(TTS) + 语音输入(ASR)

调用链：
  对话 → LLM Provider(本地 llama.cpp / 云端 OpenAI 兼容 API)
  朗读 → local-tts 的 client.py（绕过 AIPC 门禁直接驱动 Qwen3-TTS，参考音频克隆音色）
  听写 → local-asr 的 client.py（本地离线转写）
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
TTS_CACHE_DIR = DATA_DIR / "tts_cache"
SESSIONS_PATH = DATA_DIR / "sessions.json"
CONFIG_PATH = BASE_DIR / "config.json"
FRONTEND_DIR = BASE_DIR / "xiaoni-ai-persona"


def _load_dotenv() -> None:
    """简单加载项目根目录 .env 文件到环境变量（不引入 python-dotenv 依赖）。
    仅设置尚未存在的环境变量，不覆盖已设置的值。"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        logging.getLogger("server").warning("_load_dotenv failed: %s", exc)


_load_dotenv()

USER_HOME = Path.home()
TTS_SKILL = USER_HOME / ".trae-cn" / "skills" / "local-tts"
ASR_SKILL = BASE_DIR / "adapters" / "asr"
TTS_VENV_PY = USER_HOME / ".openvino" / "venv" / "t2i-tts" / "Scripts" / "python.exe"
ASR_VENV_PY = USER_HOME / ".openvino" / "venv" / "asr-cu" / "Scripts" / "python.exe"

for d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, TTS_CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# 日志：避免 print 吞错误，统一 WARNING+ 输出到 stderr
logging.basicConfig(level=logging.WARNING, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("server")

os.environ.setdefault("INTEL_SKILL_DOG_NO_EVICTION", "1")
skill_lock = asyncio.Lock()
# 并发写保护：sessions / config / tts_cache_cleanup 分别独立锁
_io_locks = {
    "config": asyncio.Lock(),
    "sessions": asyncio.Lock(),
    "cache_cleanup": asyncio.Lock(),
}

_HTTP_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)
# ponytail: 分阶段超时（connect 5s 防止 TLS 握手指纹打满，read 按场景在调用处覆盖）
_HTTP_TIMEOUT = httpx.Timeout(5.0, read=180.0, write=30.0, pool=10.0)
httpx_client = httpx.AsyncClient(limits=_HTTP_LIMITS, timeout=_HTTP_TIMEOUT)

# config 内存缓存 + 写时直接更新缓存，省一次 stat+read
_cfg_cache: dict = {"_mtime_ns": 0, "_value": {}}

# TTS 缓存 LRU 清理阈值（首次合成后懒触发一次清理，避免每请求都扫描）
# ponytail: 全局简单 O(n) 扫描足够（万级文件以内不成为瓶颈），升级路径用 sqlite/有序集合
_TTS_CACHE_MAX_FILES = 500
_TTS_CACHE_MAX_BYTES = 8 * 1024 * 1024 * 1024  # 8GB
_TTS_CACHE_LAST_CLEAN = 0.0
_TTS_CACHE_CLEAN_INTERVAL = 3600.0  # 1h 内不重复扫

# sessions 缓存（和 config 同样 mtime 失效策略）
_sess_cache: dict = {"_mtime_ns": 0, "_value": []}

# fire-and-forget 后台任务引用集：asyncio.create_task 的返回值若无人持有会被 GC 提前回收，
# 导致清理任务中途消失；这里持有强引用，任务结束后自动从集合移除。
_bg_tasks: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

# ASR 上传安全限制
_MAX_ASR_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB，约 2h 48kHz 立体声 PCM
_ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".webm", ".mp4"}


def _apply_env_overrides(cfg: dict) -> dict:
    """把 .env 里的 API 密钥合并进配置副本（.env 优先于 config.json 明文）。

    只在副本上改，不碰缓存本体 —— 否则 update_config/roles_apply 保存时会把
    密钥明文持久化进 config.json，/api/status 也会把密钥回传给前端。
    注意：env key 只注入「当前生效的 cloud provider」对应的位置（按 cloud.provider
    匹配），绝不能拿 A 家平台的 key 去覆盖 B 家的接口 —— 否则切到 deepseek 后会用
    MIMO_API_KEY 去调 deepseek，401 导致状态灯常红。"""
    mimo_key = os.getenv("MIMO_API_KEY")
    ark_key = os.getenv("ARK_API_KEY")
    aliyun_key = os.getenv("ALIYUN_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not (mimo_key or ark_key or aliyun_key or deepseek_key):
        return cfg
    out = dict(cfg)
    cloud_cfg = out.get("cloud") or {}
    cloud_provider = cloud_cfg.get("provider", "")
    # 仅当当前云端 provider 是 mimo/deepseek 时，才用对应 env key 覆盖 cloud.api_key
    if cloud_provider == "mimo" and mimo_key:
        out["cloud"] = {**cloud_cfg, "api_key": mimo_key}
    elif cloud_provider == "deepseek" and deepseek_key:
        out["cloud"] = {**cloud_cfg, "api_key": deepseek_key}
    if mimo_key or ark_key:
        cp = {k: dict(v) for k, v in out.get("cloud_providers", {}).items()}
        if mimo_key:
            cp.setdefault("mimo", {})["api_key"] = mimo_key
        if ark_key:
            cp.setdefault("ark", {})["api_key"] = ark_key
        out["cloud_providers"] = cp
    if mimo_key or aliyun_key:
        voice = dict(out.get("voice", {}))
        if mimo_key:
            voice["mimo"] = {**voice.get("mimo", {}), "api_key": mimo_key}
        if aliyun_key:
            voice["aliyun"] = {**voice.get("aliyun", {}), "api_key": aliyun_key}
        out["voice"] = voice
    return out


def load_config(with_env: bool = True) -> dict:
    """读取配置（mtime 失效缓存）。with_env=False 返回磁盘原值，
    供 update_config/roles_apply 这类「改完要写盘」的路径使用，避免把 env 密钥持久化。"""
    _default_config = {
        "provider": "cloud",
        "local": {"base_url": "http://localhost:11434/v1", "model": "qwen3-4b"},
        "cloud": {"base_url": "", "model": "", "api_key": ""},
        "voice": {"provider": "mimo", "manual_provider": False},
        "roles": {},
    }
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        _log.warning("load_config: config.json not found, using default config")
        return _default_config
    except OSError:
        # stat 失败（文件刚创建/删除中），直接读一次；不再写缓存，下次请求重试
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _log.warning("load_config: config.json not found during fallback read, using default config")
            return _default_config
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("load_config fallback read failed: %s, using default config", exc)
            return _default_config
    if mtime != _cfg_cache["_mtime_ns"]:
        try:
            _cfg_cache["_value"] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _log.warning("load_config: JSON parse error: %s, using default config", exc)
            return _default_config
        _cfg_cache["_mtime_ns"] = mtime
    cfg = _cfg_cache["_value"]
    return _apply_env_overrides(cfg) if with_env else cfg


async def save_config(cfg: dict) -> None:
    """并发安全的配置写入：写锁 + 原子 replace + 写后直接更新内存缓存，省一次 stat+read。"""
    async with _io_locks["config"]:
        data = json.dumps(cfg, ensure_ascii=False, indent=2)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(CONFIG_PATH)
        try:
            _cfg_cache["_mtime_ns"] = CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            _cfg_cache["_mtime_ns"] = 0
        _cfg_cache["_value"] = cfg


def current_persona(cfg: dict) -> str:
    """生效人设：当前激活角色的 persona 优先，回退到顶层 persona。"""
    role = cfg.get("roles", {}).get(cfg.get("active_role", ""), {})
    return role.get("persona") or cfg.get("persona", "")


# provider 在线探测 TTL 缓存：前端连续操作（切角色/保存设置）会短时间多次 refreshStatus，
# 每次都发真实 HTTP 探测既慢又给对端打无谓流量；同一 (provider, base_url) 3s 内复用结果。
_probe_cache: dict[tuple[str, str], tuple[float, bool, str]] = {}
_PROBE_TTL = 3.0


async def probe_active_provider(cfg: dict) -> tuple[bool, str]:
    """探测当前 provider 的 OpenAI 兼容 /models 接口，返回 (在线, 错误信息)。"""
    provider = cfg.get("provider", "local")
    conf = cfg.get(provider, {})
    base_url = (conf.get("base_url") or "").rstrip("/")
    ck = (provider, base_url)
    now = time.monotonic()
    cached = _probe_cache.get(ck)
    if cached is not None and now - cached[0] < _PROBE_TTL:
        return cached[1], cached[2]
    if not base_url:
        _probe_cache[ck] = (now, False, "未配置 API 地址")
        return False, "未配置 API 地址"
    api_key = conf.get("api_key") or "none"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = await httpx_client.get(f"{base_url}/models", headers=headers, timeout=3.5)
        r.raise_for_status()
        _probe_cache[ck] = (now, True, "")
        return True, ""
    except httpx.HTTPError as exc:
        _probe_cache[ck] = (now, False, str(exc))
        return False, str(exc)


# 风格标记解析：LLM 在回复开头用 [style:xxx] 标注朗读风格，让 TTS 按情绪动态合成。
# LLM 不总是把标记放最开头（实测有放在中间/末尾的情况），因此匹配全文任意位置的
# [style:x] / 【风格：x】 标记（兼容全角括号/冒号、大小写）：第一个匹配作为风格，
# 并把所有标记从正文剥离，避免标记残留进展示文本和 TTS 朗读。
_STYLE_RE = re.compile(r"[\[【](?:style|风格)\s*[:：]\s*([^\]】\r\n]+?)\s*[\]】]", re.I)


def parse_style_prefix(text: str, fallback: str = "") -> tuple[str, str]:
    """从 LLM 输出中拆出 (style, reply)。

    匹配任意位置的 [style:xxx] / 【风格：xxx】 标记：第一个匹配作为 style，
    所有匹配从正文剥离（标记独占一行时留下的多余空行一并折叠）。
    无标记时 style=fallback，reply=原文。
    """
    text = (text or "").strip()
    if not text:
        return fallback, ""
    style = fallback
    matched = False
    for m in _STYLE_RE.finditer(text):
        if not matched:
            style = m.group(1).strip() or fallback
            matched = True
    if not matched:
        return fallback, text
    reply = _STYLE_RE.sub("", text)
    reply = re.sub(r"[ \t]*\n[ \t]*", "\n", reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply)
    return style, reply.strip()


_TT_PUNCT_TRANS = str.maketrans(
    {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；", "(": "（", ")": "）"}
)


# 标点规整：原 7 条正则全部预编译，逐个 sub 调用，语义严格等价。
#   ponytail: 7 次 sub 调用的 Python 开销 <1μs/次，合并会引入交替正则的漏匹配 bug；
#   真正的性能收益来自「预编译 + 避免每次 re.sub 查模块级 _cache 字典」。
_TT_RE_WS_COLLAPSE = re.compile(r"[ \t]+")
_TT_RE_NEWLINES    = re.compile(r"\s*\n+\s*")
_TT_RE_PUNCT_DUP   = re.compile(r"([，。！？；：]){2,}")
_TT_RE_PUNCT_WS    = re.compile(r"\s*([，。！？；：、])\s*")
_TT_RE_PUNCT_LSTRIP = re.compile(r"^[，。！？；：、]+")
_TT_RE_PUNCT_RSTRIP = re.compile(r"[，。！？；：、]+$")


def normalize_tts_text(text: str) -> str:
    """规整朗读文本：统一中文标点、折叠换行、补齐句末标点，改善断句。"""
    text = (text or "").strip()
    if not text:
        return ""
    text = text.translate(_TT_PUNCT_TRANS)
    text = _TT_RE_WS_COLLAPSE.sub(" ", text)
    text = _TT_RE_NEWLINES.sub("。", text)
    text = _TT_RE_PUNCT_DUP.sub(r"\1", text)
    text = _TT_RE_PUNCT_WS.sub(r"\1", text)
    text = _TT_RE_PUNCT_LSTRIP.sub("", text)
    text = _TT_RE_PUNCT_RSTRIP.sub("", text)
    if text and not text.endswith(("。", "！", "？")):
        text += "。"
    return text


def ref_paths_for_role(cfg: dict) -> tuple[Path, Path]:
    """按 active_role 取角色专属参考音频 (wav, txt)；无角色音色时回退全局 voice_ref。

    ponytail: 文件名约定 `voice_<role>.wav`/`voice_<role>.txt`，不在 config 里写路径，
    避免角色配置膨胀；切角色即换音色，全局 voice_ref.wav 作为未克隆角色时的回退。"""
    role = cfg.get("active_role", "")
    if role:
        wav = DATA_DIR / f"voice_{role}.wav"
        txt = DATA_DIR / f"voice_{role}.txt"
        if wav.exists():
            return wav, txt
    return DATA_DIR / "voice_ref.wav", DATA_DIR / "voice_ref.txt"


def role_voice_registered(cfg: dict) -> bool:
    """当前角色是否有专属克隆音色，无角色音色时回退检查全局音色。"""
    role = cfg.get("active_role", "")
    if role and (DATA_DIR / f"voice_{role}.wav").exists():
        return True
    return (DATA_DIR / "voice_ref.wav").exists()


def cloud_ref_paths_for_role(cfg: dict) -> tuple[Path, str]:
    """云端 VoiceClone 参考音频：优先角色专属 mp3，其次角色 wav，最后全局回退。

    返回 (音频路径, MIME)。MiMo 官方示例使用 mp3 DataURL，mp3 样本的音高稳定度
    实测优于 wav，因此云端克隆单独优先读取 voice_<role>_cloud.mp3。"""
    role = cfg.get("active_role", "")
    candidates: list[Path] = []
    if role:
        candidates += [
            DATA_DIR / f"voice_{role}_cloud.mp3",
            DATA_DIR / f"voice_{role}.mp3",
            DATA_DIR / f"voice_{role}.wav",
        ]
    candidates += [
        DATA_DIR / "voice_ref_cloud.mp3",
        DATA_DIR / "voice_ref.mp3",
        DATA_DIR / "voice_ref.wav",
    ]
    for path in candidates:
        if path.exists():
            mime = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
            return path, mime
    return DATA_DIR / "voice_ref.wav", "audio/wav"


# 音色指纹缓存：同一请求内 _voice_fingerprint 常被调 2+ 次，按 (active_role, provider, preset, voice, model) 短缓存
# ponytail: 用进程级 dict，key 只取 cfg 中会变的字段，value 是 (指纹值, 所有参考文件的 (path, mtime_ns, size) 快照)
# 如果下一次调用时参考文件的 stat 没变，直接复用；否则重算。
_fp_cache: dict[str, tuple[str, tuple[tuple[str, int, int], ...]]] = {}


def _voice_fingerprint(cfg: dict) -> str:
    """当前音色指纹：音色配置 + 参考音频 mtime/size，换音色后 TTS 缓存自动失效。"""
    voice = cfg.get("voice", {})
    base_parts = [
        str(cfg.get("active_role", "")),
        str(voice.get("provider", "")),
        str(voice.get("preset", "")),
        str(voice.get("voice", "")),
        str(voice.get("model", "")),
        str(voice.get("language", "")),
        str(voice.get("base_url", "")),
    ]
    if voice.get("provider") == "aliyun":
        # 阿里云音色/模型在子配置里，必须进 cache_key，否则换音色缓存不失效
        a = voice.get("aliyun", {})
        base_parts += [str(a.get("model", "")), str(a.get("voice", ""))]
    cache_key = "|".join(base_parts)

    # 收集参考文件的 stat 快照，对比缓存看是否命中
    ref_stats: list[tuple[str, int, int]] = []
    ref_audio, _ = ref_paths_for_role(cfg)
    if ref_audio.exists():
        st = ref_audio.stat()
        ref_stats.append((str(ref_audio), st.st_mtime_ns, st.st_size))
    if voice.get("provider") == "mimo":
        cloud_ref, cloud_mime = cloud_ref_paths_for_role(cfg)
        if cloud_ref.exists():
            st = cloud_ref.stat()
            ref_stats.append((f"cloud:{cloud_mime}:{cloud_ref}", st.st_mtime_ns, st.st_size))
    stat_tuple = tuple(ref_stats)

    cached = _fp_cache.get(cache_key)
    if cached is not None and cached[1] == stat_tuple:
        return cached[0]

    parts = list(base_parts)
    for _p, mt, sz in stat_tuple:
        parts += [str(mt), str(sz)]
    if voice.get("provider") == "mimo":
        parts += ["no_user_style"]
        for tag in [x[0] for x in stat_tuple if x[0].startswith("cloud:")]:
            parts.append(tag)
    fp = "|".join(parts)
    # ponytail: 缓存大小不设上限（角色数 < 100 可忽略），有需求再加 LRU
    _fp_cache[cache_key] = (fp, stat_tuple)
    return fp


async def _maybe_cleanup_tts_cache() -> None:
    """懒触发 TTS 缓存 LRU 清理：超阈值时删最旧（atime），1h 内最多扫一次。"""
    global _TTS_CACHE_LAST_CLEAN
    now = time.monotonic()
    if now - _TTS_CACHE_LAST_CLEAN < _TTS_CACHE_CLEAN_INTERVAL:
        return
    async with _io_locks["cache_cleanup"]:
        # double-check：持锁后用最新时间再看，避免等锁期间别人刚扫完又跑一遍
        now = time.monotonic()
        if now - _TTS_CACHE_LAST_CLEAN < _TTS_CACHE_CLEAN_INTERVAL:
            return
        entries: list[tuple[float, int, Path]] = []
        total_bytes = 0
        try:
            for p in TTS_CACHE_DIR.iterdir():
                if not p.is_file() or p.suffix != ".wav":
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                entries.append((st.st_atime_ns, st.st_size, p))
                total_bytes += st.st_size
        except OSError as exc:
            _log.warning("tts cache scan failed: %s", exc)
            return
        if len(entries) <= _TTS_CACHE_MAX_FILES and total_bytes <= _TTS_CACHE_MAX_BYTES:
            _TTS_CACHE_LAST_CLEAN = now
            return
        # 按 atime 升序（最旧的先删）
        entries.sort(key=lambda x: x[0])
        removed = 0
        target_files = max(0, len(entries) - _TTS_CACHE_MAX_FILES)
        target_bytes = max(0, total_bytes - _TTS_CACHE_MAX_BYTES)
        bytes_removed = 0
        for _at, sz, p in entries:
            if removed >= target_files and bytes_removed >= target_bytes:
                break
            try:
                p.unlink()
                removed += 1
                bytes_removed += sz
            except OSError as exc:
                _log.warning("tts cache unlink failed %s: %s", p, exc)
        _TTS_CACHE_LAST_CLEAN = now
        if removed:
            _log.info("tts cache cleanup: removed %d files (%d bytes)", removed, bytes_removed)


def tts_cache_path(text: str, style: str, cfg: dict) -> Path:
    """TTS 缓存 key 不含 speed — 语速由前端 Audio.playbackRate 控制，
    原速 1.0 合成一次即可复用所有语速，缓存体积大幅下降。"""
    key = f"{_voice_fingerprint(cfg)}\x00{style}\x00{text}"
    return TTS_CACHE_DIR / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.wav"


def _save_tts_cache(cache: Path, src: Path) -> Path:
    """原子保存缓存，返回本次可服务的音频路径。

    同分区优先 rename（一次 syscall），否则 copy+replace；写缓存失败时保留 src
    并返回它 —— 若把 src 删了又返回旧 cache，force 重合成会播旧音频。"""
    try:
        # 先试直接 rename（OUTPUT_DIR 和 TTS_CACHE_DIR 通常都在 data/ 下，同分区）
        try:
            src.replace(cache)
            return cache
        except OSError:
            # 跨分区或目标被占用（如 Windows 上正被 FileResponse 读取）时回退 copy+replace
            pass
        tmp = cache.with_suffix(".tmp")
        try:
            shutil.copyfile(src, tmp)
            tmp.replace(cache)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        src.unlink(missing_ok=True)
        return cache
    except OSError as exc:
        # 缓存写入失败不影响本次请求返回（返回 src 照常播放），但要留痕
        _log.warning("tts cache save failed: cache=%s src=%s err=%s", cache, src, exc)
        return src


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await httpx_client.aclose()


app = FastAPI(title="AI 拟人系统", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ---------------------------------------------------------------- LLM --------
async def llm_chat(messages: list[dict]) -> str:
    """按 config 里的 provider 调用本地 llama-server 或云端 OpenAI 兼容 API。"""
    cfg = load_config()
    provider = cfg.get("provider", "cloud")
    if provider not in ("local", "cloud"):
        raise HTTPException(400, f"未知 provider: {provider}")
    conf = cfg.get(provider) or {}
    base_url = (conf.get("base_url") or "").rstrip("/")
    api_key = conf.get("api_key") or "none"
    model = conf.get("model", "")
    if not base_url or not model:
        raise HTTPException(400, f"provider 配置不完整: {provider}")
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 768,  # 512 会截断长回复（sessions.json 里多条回复半句话被掐断），截断的回复进历史更易被模型重复续写
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    if api_key and api_key != "none":
        headers["api-key"] = api_key
    # 更精确的分阶段超时：connect/write 短，read 留给模型推理
    timeout = httpx.Timeout(5.0, connect=5.0, write=10.0, read=180.0, pool=15.0)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            r = await httpx_client.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"].get("content")
            content = (content or "").strip()
            if not content:
                # Qwen 系模型偶发只返回 reasoning_content 或空正文；先重试一次，
                # 仍为空时再明确报错，避免前端渲染空白气泡。
                if attempt == 0:
                    last_err = HTTPException(502, "模型返回了空回复")
                    continue
                raise HTTPException(502, "模型返回了空回复，请重试；若持续失败请检查云端模型配置")
            return content
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == 1:
                if exc.response.status_code == 401:
                    raise HTTPException(502, "云端 API 鉴权失败：请检查 API Key")
                raise HTTPException(502, f"LLM 调用失败: {exc}")
            last_err = exc
        except httpx.TransportError as exc:
            if attempt == 1:
                if provider == "local":
                    raise HTTPException(502, "本地模型未启动：请先运行 start_llm.bat 或用云端 API")
                raise HTTPException(502, f"云端 API 连接失败: {exc}")
            last_err = exc
        except (KeyError, IndexError, ValueError) as exc:
            raise HTTPException(502, f"LLM 响应解析失败: {exc}")
    raise HTTPException(502, f"LLM 调用失败: {last_err}")


# ---------------------------------------------------------------- TTS ---------
def _run_sync(*args: str, timeout: float = 660) -> tuple[int, str, str]:
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return proc.returncode, proc.stdout, proc.stderr


# TTS 合成 singleflight：同一文本+音色的并发请求只合成一次，其余等待复用结果。
# 云端 TTS 每次合成耗秒级+花配额，前端自动朗读+用户点朗读很容易撞车。
_tts_inflight: dict[str, asyncio.Future] = {}


async def tts_synthesize(text: str, style: str = "", speed: float = 1.0, force: bool = False) -> Path:
    """按 voice.provider 合成语音（原速 1.0）。

    speed 参数保留签名以兼容老调用方，但后端不再处理 —— 语速由前端
    Audio.playbackRate 控制，零延迟、不占缓存。原速合成一次即可复用所有语速。
    """
    text = normalize_tts_text(text)
    cfg = load_config()
    cache = tts_cache_path(text, style, cfg)
    if not force:
        # 单条 stat 替代 exists()+stat()：与清理任务并发时文件可能刚被删，exists 后 stat 会抛 OSError
        try:
            if cache.stat().st_size:
                return cache
        except OSError:
            pass
        # 已有同 key 合成在飞，直接等它的结果（shield 防止等待方被取消时连带取消合成方）
        inflight = _tts_inflight.get(cache.name)
        if inflight is not None:
            return await asyncio.shield(inflight)
    _spawn_bg(_maybe_cleanup_tts_cache())
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    if not force:
        _tts_inflight[cache.name] = fut
    try:
        path = await _tts_do_synthesize(text, style, cfg, cache)
        if not fut.done():
            fut.set_result(path)
        return path
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _tts_inflight.pop(cache.name, None)


async def _tts_do_synthesize(text: str, style: str, cfg: dict, cache: Path) -> Path:
    """实际执行合成并写缓存，返回最终可服务的音频路径。"""
    if cfg.get("voice", {}).get("provider") == "mimo":
        out_path = await mimo_tts_synthesize(text, style, cfg)
    elif cfg.get("voice", {}).get("provider") == "aliyun":
        out_path = await aliyun_tts_synthesize(text, style, cfg)
    else:
        async with skill_lock:
            out_path = OUTPUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"
            ref_audio, ref_text = ref_paths_for_role(cfg)
            args = [
                str(TTS_VENV_PY), str(TTS_SKILL / "scripts" / "client.py"),
                "-i", text,
                "--language", cfg.get("voice", {}).get("language", "Chinese"),
                "--output", str(out_path),
            ]
            if ref_audio.exists() and ref_text.exists():
                try:
                    ref_content = ref_text.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    _log.warning("ref_text read failed %s: %s", ref_text, exc)
                    ref_content = ""
                if ref_content:
                    args += ["--ref-audio", str(ref_audio), "--ref-text", ref_content]
            if not any(a == "--ref-audio" for a in args) and cfg.get("voice", {}).get("preset"):
                args += ["--voice", cfg["voice"]["preset"]]
            code, out, err = await asyncio.to_thread(_run_sync, *args)
            if code == 3:  # 模型下载中，按 client 协议 --continue 续跑
                code, out, err = await asyncio.to_thread(_run_sync, str(TTS_VENV_PY), str(TTS_SKILL / "scripts" / "client.py"), "--continue")
            if code != 0 or not out_path.exists():
                detail = re.search(r"❌.*?(?:\n|$)", out + err, re.S)
                raise HTTPException(500, f"语音合成失败: {(detail.group(0).strip() if detail else (err or out)[-500:])}")
    return _save_tts_cache(cache, out_path)


# MiMo VoiceClone 的参考音频放在 audio.voice（DataURL）里，官方示例不传 reference_text；
# 没有克隆样本时回退通用 TTS，audio.voice 传平台音色名（如 mimo_default）。


async def mimo_tts_synthesize(text: str, style: str = "", cfg: dict | None = None) -> Path:
    """调用小米 MiMo 开放平台 TTS（OpenAI 兼容 /v1/chat/completions）。

    认证同时带 Authorization: Bearer 与 api-key 两种 header（兼容不同版本文档）；
    user 消息为风格指令（style 入参优先，否则用 cfg.voice.style），assistant 消息为待合成文本；
    音频以 base64 返回。参考音频按 active_role 取角色专属文件，注册过则自动切换 VoiceClone 模型。
    """
    cfg = cfg or load_config()
    m = cfg.get("voice", {}).get("mimo", {})
    base_url = (m.get("base_url") or "").rstrip("/")
    api_key = m.get("api_key") or ""
    if not base_url or not api_key:
        raise HTTPException(400, "MiMo 音色未配置：请在设置里填写 MiMo API Key")
    voice = m.get("voice", "") or "mimo_default"
    # VoiceClone 对 user 风格指令极敏感，实测会把参考音高从 116Hz 拉到 180Hz+；
    # 官方示例也留空 user 消息，因此云端克隆不传风格，本地合成再保留风格控制。
    use_style = ""
    ref_audio, ref_mime = cloud_ref_paths_for_role(cfg)
    has_ref = ref_audio.exists()
    if has_ref:
        model = m.get("clone_model") or "mimo-v2.5-tts-voiceclone"
    else:
        model = m.get("model")
    audio_obj = {"format": m.get("format", "wav")}
    if has_ref:
        try:
            raw_bytes = ref_audio.read_bytes()
        except OSError as exc:
            raise HTTPException(500, f"读取参考音频失败: {exc}")
        try:
            b64 = base64.b64encode(raw_bytes).decode("ascii")
        except (ValueError, UnicodeEncodeError) as exc:
            raise HTTPException(500, f"参考音频编码失败: {exc}")
        audio_obj["voice"] = f"data:{ref_mime};base64,{b64}"
    else:
        audio_obj["voice"] = voice
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": use_style},
            {"role": "assistant", "content": text},
        ],
        "audio": audio_obj,
    }
    headers = {"Authorization": f"Bearer {api_key}", "api-key": api_key}
    timeout = httpx.Timeout(5.0, connect=5.0, write=10.0, read=180.0, pool=15.0)
    try:
        r = await httpx_client.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()["choices"][0]["message"]["audio"]["data"]
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"MiMo TTS 调用失败: {exc}")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"MiMo TTS 响应解析失败（若含参考音频，请核对官方文档字段）: {exc}")
    out_path = OUTPUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"
    try:
        out_path.write_bytes(base64.b64decode(data, validate=False))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(502, f"MiMo TTS 返回的不是有效音频数据: {exc}")
    if out_path.stat().st_size < 128:
        raise HTTPException(502, "MiMo TTS 返回的音频文件过小（可能请求失败）")
    return out_path


async def aliyun_tts_synthesize(text: str, style: str = "", cfg: dict | None = None) -> Path:
    """调用阿里云百炼（DashScope）语音合成。

    model 支持 qwen3-tts-flash（快）/ qwen3-tts-instruct-flash（指令控制）/
    qwen3-tts-vc（声音克隆，需在"音色"填复刻的 voice id）。
    voice 传系统音色名（如 Cherry）或声音复刻返回的 voice id。
    """
    cfg = cfg or load_config()
    a = cfg.get("voice", {}).get("aliyun", {})
    api_key = a.get("api_key") or ""
    if not api_key:
        raise HTTPException(400, "阿里云音色未配置：请在设置里填写阿里云 API Key")
    model = a.get("model") or "qwen3-tts-flash"
    voice = a.get("voice") or "Cherry"
    base = (a.get("base_url") or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    payload = {
        "model": model,
        "input": {"text": text, "voice": voice, "language_type": "Chinese"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = await httpx_client.post(
            f"{base}/services/aigc/multimodal-generation/generation",
            json=payload, headers=headers, timeout=120,
        )
        r.raise_for_status()
        data = r.json()["output"]["audio"]
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"阿里云 TTS 调用失败: {exc}")
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, f"阿里云 TTS 响应解析失败: {exc}")
    out_path = OUTPUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"
    audio_data = data.get("data")
    if audio_data:
        try:
            out_path.write_bytes(base64.b64decode(audio_data))
        except (ValueError, TypeError):
            raise HTTPException(502, "阿里云 TTS 返回的不是有效音频数据")
    elif data.get("url"):
        try:
            resp = await httpx_client.get(data["url"], timeout=120)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"阿里云 TTS 音频下载失败: {exc}")
    else:
        raise HTTPException(502, "阿里云 TTS 未返回音频数据")
    return out_path


# ---------------------------------------------------------------- ASR ---------
async def asr_transcribe(audio_path: Path) -> str:
    args = [str(ASR_VENV_PY), str(ASR_SKILL / "scripts" / "client.py"),
            "--audio", str(audio_path), "--language", "auto"]
    code, out, err = await asyncio.to_thread(_run_sync, *args)
    if code == 3:
        code, out, err = await asyncio.to_thread(_run_sync, str(ASR_VENV_PY), str(ASR_SKILL / "scripts" / "client.py"), "--continue")
    if code != 0:
        raise HTTPException(500, f"语音识别失败: {(err or out)[-500:]}")
    m = re.search(r"=== RESULT ===\s*(\{.*\})", out, re.S)
    if not m:
        raise HTTPException(500, "语音识别没有返回结果")
    try:
        data = json.loads(m.group(1))
        return data.get("text", "")
    except json.JSONDecodeError:
        raise HTTPException(500, "语音识别结果解析失败")


async def asr_transcribe_serial(audio_path: Path) -> str:
    """ASR 与 TTS 共用一个串行锁（共用 server-dog 不能并发）。"""
    async with skill_lock:
        return await asr_transcribe(audio_path)


# ---------------------------------------------------------------- 接口 ---------
@app.post("/api/llm-models")
async def llm_models(req: dict):
    """从云端 API 的 /models 接口拉取可用模型列表（用于"获取模型列表"按钮）。"""
    cfg = load_config()
    base_url = (req.get("base_url") or cfg.get("cloud", {}).get("base_url", "")).rstrip("/")
    api_key = req.get("api_key") or cfg.get("cloud", {}).get("api_key", "")
    if not base_url:
        raise HTTPException(400, "请先填写云端 API 地址")
    try:
        r = await httpx_client.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=30,
        )
        r.raise_for_status()
        models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"获取模型列表失败: {exc}")
    if req.get("all"):
        # 克隆引擎区：返回全量模型（含 tts/voiceclone 等语音模型）
        return {"models": models}
    # 对话区：过滤非文本对话模型（TTS/ASR/语音克隆/embedding 等），避免聊天选错模型
    _non_chat_kw = (
        "tts", "asr", "voiceclone", "voicedesign", "voice", "audio",
        "embedding", "image", "whisper", "transcri", "rerank",
    )
    models = [m for m in models if not any(k in m.lower() for k in _non_chat_kw)]
    return {"models": models}


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "pages" / "index.html")


@app.get("/api/status")
async def status():
    cfg = load_config()
    roles = cfg.get("roles", {})
    local_cfg = cfg.get("local", {})
    cloud_cfg = cfg.get("cloud", {})
    active_online, active_error = await probe_active_provider(cfg)
    return {
        "provider": cfg.get("provider", "local"),
        "active_online": active_online,
        "active_error": active_error,
        "local": local_cfg,
        "cloud": {k: v for k, v in cloud_cfg.items() if k != "api_key"},
        # cloud_providers 里可能有 env 注入/磁盘残留的 api_key，统一脱敏，不能回传前端
        "cloud_providers": {
            k: {kk: vv for kk, vv in v.items() if kk != "api_key"}
            for k, v in cfg.get("cloud_providers", {}).items()
        },
        "cloud_has_key": bool(cloud_cfg.get("api_key")),
        "voice_registered": role_voice_registered(cfg),
        "persona": current_persona(cfg),
        "voice_language": cfg.get("voice", {}).get("language", "Chinese"),
        "active_role": cfg.get("active_role", ""),
        "roles": [
            {"key": k, "name": v.get("name", k), "full_name": v.get("full_name", ""),
             "desc": v.get("desc", ""), "voice_provider": v.get("voice", {}).get("provider", "local"),
             "voice_registered": (DATA_DIR / f"voice_{k}.wav").exists()}
            for k, v in roles.items()
        ],
        "voice_provider": cfg.get("voice", {}).get("provider", "local"),
        "mimo_configured": bool(cfg.get("voice", {}).get("mimo", {}).get("api_key")),
        "voice_mimo": {k: v for k, v in cfg.get("voice", {}).get("mimo", {}).items() if k != "api_key"},
        "aliyun_configured": bool(cfg.get("voice", {}).get("aliyun", {}).get("api_key")),
        "voice_aliyun": {k: v for k, v in cfg.get("voice", {}).get("aliyun", {}).items() if k != "api_key"},
        "voice_style": cfg.get("voice", {}).get("style", ""),
        "voice_manual_provider": bool(cfg.get("voice", {}).get("manual_provider")),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    cfg = load_config()
    # 在 persona 末尾追加风格前缀约定，让 LLM 输出 [style:xxx] 标注朗读情绪，
    # server 解析后剥离前缀只把纯回复存 history，避免污染对话上下文。
    persona = current_persona(cfg)
    style_hint = (
        "\n\n【输出格式】请在回复最开头用 [style:风格] 标注本句朗读风格（如 自然/激动/低沉/温柔/活泼/沉稳/俏皮 等），"
        "然后写回复正文。示例：[style:自然]今天天气不错。"
    )
    system = {"role": "system", "content": persona + style_hint}
    # 旧版前端会把新消息 push 进 history 后整体发送，message 字段又带一份 → 模型看到连续两条相同的 user 消息，
    # 容易陷入重复回复（回和上一条一样的话）。这里去掉历史中与当前消息重复的尾项，保证当前消息只出现一次。
    history = req.history[-20:]
    if history and history[-1].get("role") == "user" and (history[-1].get("content") or "").strip() == req.message.strip():
        history = history[:-1]
    messages = [system] + history + [{"role": "user", "content": req.message}]
    raw = await llm_chat(messages)
    fallback_style = cfg.get("voice", {}).get("style") or "自然"
    style, reply = parse_style_prefix(raw, fallback=fallback_style)
    return {"reply": reply, "style": style}


@app.post("/api/tts")
async def tts(req: dict):
    text = (req.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "文本不能为空")
    style = (req.get("style") or "").strip()
    try:
        speed = float(req.get("speed") or 1.0)
    except (TypeError, ValueError):
        raise HTTPException(400, "speed 必须是数字")
    if not 0.5 <= speed <= 2.0:
        raise HTTPException(400, "speed 需在 0.5 到 2.0 之间")
    force = req.get("force") in (True, "true", 1, "1")
    path = await tts_synthesize(text, style, speed, force)
    return FileResponse(path, media_type="audio/wav", filename=path.name)


class SessionsRequest(BaseModel):
    sessions: list[dict] = []


@app.get("/api/sessions")
async def get_sessions():
    """读取持久化的会话历史（data/sessions.json），文件不存在或损坏时返回空列表。
    带 mtime 缓存：不频繁读盘，且并发安全。"""
    if not SESSIONS_PATH.exists():
        return {"sessions": []}
    try:
        mtime = SESSIONS_PATH.stat().st_mtime_ns
    except OSError:
        return {"sessions": []}
    if mtime != _sess_cache["_mtime_ns"]:
        try:
            data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            sessions = data.get("sessions")
            _sess_cache["_value"] = sessions if isinstance(sessions, list) else []
        except (json.JSONDecodeError, OSError, AttributeError):
            _sess_cache["_value"] = []
        _sess_cache["_mtime_ns"] = mtime
    return {"sessions": list(_sess_cache["_value"])}


@app.put("/api/sessions")
async def put_sessions(req: SessionsRequest):
    """覆盖保存会话历史：并发安全写锁 + 原子 replace + 写后更新内存缓存。"""
    # 单条记录上限 64KB，500 条就是 32MB 上限，防止异常 payload
    sessions = req.sessions[-500:]
    try:
        payload_str = json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        raise HTTPException(400, "sessions 数据非法，无法序列化")
    if len(payload_str.encode("utf-8")) > 32 * 1024 * 1024:
        raise HTTPException(400, "sessions 数据过大")
    async with _io_locks["sessions"]:
        tmp = SESSIONS_PATH.with_suffix(".tmp")
        tmp.write_text(payload_str, encoding="utf-8")
        tmp.replace(SESSIONS_PATH)
        try:
            _sess_cache["_mtime_ns"] = SESSIONS_PATH.stat().st_mtime_ns
        except OSError:
            _sess_cache["_mtime_ns"] = 0
        _sess_cache["_value"] = sessions
    return {"ok": True}


@app.post("/api/asr")
async def asr(file: UploadFile = File(...)):
    """ASR 语音识别：后缀白名单 + 大小上限 50MB，避免恶意上传打满磁盘。"""
    suffix = (Path(file.filename or "audio.webm").suffix or ".webm").lower()
    if suffix not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(400, f"不支持的音频格式: {suffix}，仅支持 {sorted(_ALLOWED_AUDIO_SUFFIXES)}")
    # 流式边收边写盘，超上限立刻拒绝；相比先全量进内存再 join 写盘，峰值内存不翻倍
    audio_path = UPLOAD_DIR / f"asr_{uuid.uuid4().hex[:8]}{suffix}"
    total = 0
    try:
        with audio_path.open("wb") as fout:
            while True:
                chunk = await file.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_ASR_UPLOAD_BYTES:
                    raise HTTPException(413, f"上传文件过大（上限 {_MAX_ASR_UPLOAD_BYTES // 1024 // 1024}MB）")
                fout.write(chunk)
    except HTTPException:
        audio_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(500, f"上传保存失败: {exc}")
    if total == 0:
        audio_path.unlink(missing_ok=True)
        raise HTTPException(400, "上传文件为空")
    try:
        text = await asr_transcribe_serial(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
    return {"text": text}


class ConfigUpdate(BaseModel):
    provider: str = "local"
    local_base_url: str = ""
    local_model: str = ""
    cloud_provider: str = ""
    cloud_base_url: str = ""
    cloud_api_key: str = ""
    cloud_model: str = ""
    persona: str = ""
    voice_language: str = "Chinese"
    voice_provider: str = ""
    voice_style: str = ""
    mimo_api_key: str = ""
    mimo_base_url: str = ""
    mimo_model: str = ""
    mimo_voice: str = ""
    aliyun_api_key: str = ""
    aliyun_base_url: str = ""
    aliyun_model: str = ""
    aliyun_voice: str = ""


@app.post("/api/config")
async def update_config(upd: ConfigUpdate):
    # with_env=False + deepcopy：在磁盘原值副本上改，避免把 .env 密钥明文写进 config.json；
    # 副本也避免并发请求在 save_config 等锁期间读到改了一半的缓存。
    cfg = copy.deepcopy(load_config(with_env=False))
    # setdefault 防止手改 config.json 缺 local/cloud 键时 KeyError 500
    local_cfg = cfg.setdefault("local", {})
    cloud_cfg = cfg.setdefault("cloud", {})
    if upd.provider in ("local", "cloud"):
        cfg["provider"] = upd.provider
    if upd.local_base_url:
        local_cfg["base_url"] = upd.local_base_url
    if upd.local_model:
        local_cfg["model"] = upd.local_model
    if upd.cloud_provider:
        cloud_cfg["provider"] = upd.cloud_provider
    if upd.cloud_base_url:
        cloud_cfg["base_url"] = upd.cloud_base_url
    if upd.cloud_api_key:
        cloud_cfg["api_key"] = upd.cloud_api_key
    if upd.cloud_model:
        cloud_cfg["model"] = upd.cloud_model
    if upd.persona:
        cfg["persona"] = upd.persona
    if upd.voice_language:
        cfg.setdefault("voice", {})["language"] = upd.voice_language
    # 声音克隆引擎（与对话 API 完全独立的另一套配置）
    if upd.voice_provider in ("local", "mimo", "aliyun"):
        voice_cfg = cfg.setdefault("voice", {})
        voice_cfg["provider"] = upd.voice_provider
        voice_cfg["manual_provider"] = True
    if upd.voice_style:
        cfg.setdefault("voice", {})["style"] = upd.voice_style
    if upd.mimo_api_key:
        cfg.setdefault("voice", {}).setdefault("mimo", {})["api_key"] = upd.mimo_api_key
    if upd.mimo_base_url:
        cfg.setdefault("voice", {}).setdefault("mimo", {})["base_url"] = upd.mimo_base_url
    if upd.mimo_model:
        cfg.setdefault("voice", {}).setdefault("mimo", {})["model"] = upd.mimo_model
    if upd.mimo_voice:
        cfg.setdefault("voice", {}).setdefault("mimo", {})["voice"] = upd.mimo_voice
    if upd.aliyun_api_key:
        cfg.setdefault("voice", {}).setdefault("aliyun", {})["api_key"] = upd.aliyun_api_key
    if upd.aliyun_base_url:
        cfg.setdefault("voice", {}).setdefault("aliyun", {})["base_url"] = upd.aliyun_base_url
    if upd.aliyun_model:
        cfg.setdefault("voice", {}).setdefault("aliyun", {})["model"] = upd.aliyun_model
    if upd.aliyun_voice:
        cfg.setdefault("voice", {}).setdefault("aliyun", {})["voice"] = upd.aliyun_voice
    await save_config(cfg)
    return {"ok": True}


@app.get("/api/roles")
async def roles_list():
    """内置角色库列表（含当前激活角色）。"""
    cfg = load_config()
    roles = cfg.get("roles", {})
    return {
        "active_role": cfg.get("active_role", ""),
        "roles": [
            {"key": k, "name": v.get("name", k), "full_name": v.get("full_name", ""),
             "desc": v.get("desc", ""), "voice_provider": v.get("voice", {}).get("provider", "local")}
            for k, v in roles.items()
        ],
    }


@app.post("/api/roles/apply")
async def roles_apply(req: dict):
    """切换内置角色：应用其人设与音色设置（voice.provider / preset / style）。"""
    key = (req.get("key") or "").strip()
    # 同 update_config：磁盘原值副本上改，不把 env 密钥持久化
    cfg = copy.deepcopy(load_config(with_env=False))
    role = cfg.get("roles", {}).get(key)
    if not role:
        raise HTTPException(404, f"未知角色: {key}")
    cfg["active_role"] = key
    cfg["persona"] = role.get("persona") or cfg.get("persona", "")
    v = role.get("voice", {})
    cfg.setdefault("voice", {})
    if not cfg["voice"].get("manual_provider"):
        cfg["voice"]["provider"] = v.get("provider", "local")
    if v.get("preset"):
        cfg["voice"]["preset"] = v["preset"]
    cfg["voice"]["style"] = v.get("style", "")
    await save_config(cfg)
    return {"ok": True, "role": key, "persona": cfg["persona"]}


# 静态托管新前端（挂在所有 API 路由之后，/api/* 优先匹配，其余走静态文件）
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="site")



