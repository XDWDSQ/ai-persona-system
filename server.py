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
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import role_engine
from role_engine import MemoryStore, StateStore, PostProcessor

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
TTS_CACHE_DIR = DATA_DIR / "tts_cache"
SESSIONS_PATH = DATA_DIR / "sessions.json"
CONFIG_PATH = BASE_DIR / "config.json"
FRONTEND_DIR = BASE_DIR / "xiaoni-ai-persona"

_MAX_ATTACH_UPLOAD_BYTES = 15 * 1024 * 1024
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_DOC_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".json", ".log",
}
_TEXT_DOC_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log"}
_ALLOWED_ATTACH_SUFFIXES = (
    _IMAGE_SUFFIXES
    | _DOC_SUFFIXES
    | {".zip", ".rar", ".7z", ".tar", ".gz",
       ".mp3", ".wav", ".ogg", ".m4a", ".flac",
       ".mp4", ".mov", ".mkv", ".webm", ".avi"}
)


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

# 日志：避免 print 吞错误，统一输出到 stderr；级别可用环境变量 LOG_LEVEL 调整
# （支持 DEBUG/INFO/WARNING/ERROR，默认 WARNING）
_LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING").upper()
if _LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR"):
    _LOG_LEVEL = "WARNING"
logging.basicConfig(level=getattr(logging, _LOG_LEVEL), format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
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
# 三项阈值可被 config.json 的 tts_cache 配置节覆盖（见 _refresh_tts_cache_limits）
_TTS_CACHE_MAX_FILES = 500
_TTS_CACHE_MAX_BYTES = 8 * 1024 * 1024 * 1024  # 8GB
_TTS_CACHE_LAST_CLEAN = 0.0
_TTS_CACHE_CLEAN_INTERVAL = 3600.0  # 1h 内不重复扫


def _refresh_tts_cache_limits() -> None:
    """从 config.json 的 tts_cache 节（max_files/max_bytes/clean_interval）覆盖清理阈值；
    缺失/非法值回退上方默认值。"""
    global _TTS_CACHE_MAX_FILES, _TTS_CACHE_MAX_BYTES, _TTS_CACHE_CLEAN_INTERVAL
    try:
        tcfg = load_config(with_env=False).get("tts_cache") or {}
    except Exception as exc:  # noqa: BLE001
        _log.warning("tts_cache config read failed, keep defaults: %s", exc)
        return
    try:
        v = int(tcfg.get("max_files", _TTS_CACHE_MAX_FILES))
        if v > 0:
            _TTS_CACHE_MAX_FILES = v
        v = int(tcfg.get("max_bytes", _TTS_CACHE_MAX_BYTES))
        if v > 0:
            _TTS_CACHE_MAX_BYTES = v
        v = float(tcfg.get("clean_interval", _TTS_CACHE_CLEAN_INTERVAL))
        if v > 0:
            _TTS_CACHE_CLEAN_INTERVAL = v
    except (TypeError, ValueError) as exc:
        _log.warning("tts_cache config invalid, keep defaults: %s", exc)

# sessions 缓存（和 config 同样 mtime 失效策略）
_sess_cache: dict = {"_mtime_ns": 0, "_value": []}

# fire-and-forget 后台任务引用集：asyncio.create_task 的返回值若无人持有会被 GC 提前回收，
# 导致清理任务中途消失；这里持有强引用，任务结束后自动从集合移除。
_bg_tasks: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# 角色引擎：按 active_role 懒创建 (MemoryStore, StateStore) 缓存，切角色即换存储
_role_stores: dict[str, tuple[MemoryStore, StateStore]] = {}
_post_processor: PostProcessor | None = None


def _get_role_stores(role: str) -> tuple[MemoryStore, StateStore]:
    """获取（或创建）指定角色的记忆库+状态库。data/ 下按角色分文件。"""
    pair = _role_stores.get(role)
    if pair is None:
        cfg = load_config()
        engine_cfg = cfg.get("role_engine") or {}
        mem = MemoryStore(DATA_DIR, role, limit=int(engine_cfg.get("memory_limit", 200)))
        st = StateStore(DATA_DIR, role)
        pair = (mem, st)
        _role_stores[role] = pair
    return pair


def _get_post_processor() -> PostProcessor:
    """后处理器单例：复用 llm_chat（同 provider），但低温、短输出。"""
    global _post_processor
    if _post_processor is None:
        async def _post_llm(messages: list[dict]) -> str:
            # 后处理直接关 thinking，让模型输出正文（JSON）；不再切 deepseek-chat，
            # 该模型名在当前 DeepSeek 账号里已不可用（实际可用 deepseek-v4-flash/pro）
            for attempt in range(2):
                try:
                    return await llm_chat(messages, temperature=0.5, max_tokens=300, disable_thinking=True)
                except HTTPException as exc:
                    if exc.status_code == 502 and attempt == 0:
                        continue
                    raise
            return ""
        _post_processor = PostProcessor(_post_llm)
    return _post_processor

# ASR 上传安全限制
_MAX_ASR_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB，约 2h 48kHz 立体声 PCM
_ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".webm", ".mp4"}


# 文字模型供应商对应的 .env 密钥名；DASHSCOPE_API_KEY 作为 ALIYUN_API_KEY 的兼容别名
_CLOUD_PROVIDER_ENV = {
    "mimo": "MIMO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "ark": "ARK_API_KEY",
}
_ALIYUN_ENV_KEYS = ("ALIYUN_API_KEY", "DASHSCOPE_API_KEY")


def _env_aliyun_key() -> str:
    for name in _ALIYUN_ENV_KEYS:
        val = os.getenv(name)
        if val:
            return val
    return ""


def _apply_env_overrides(cfg: dict) -> dict:
    """把 .env / config.json 里各供应商的密钥合并进配置副本。

    每个 cloud provider 的 api_key 独立保存在 cloud_providers.<provider>.api_key，
    当前生效的 cloud.api_key 永远取「当前 provider 自己的 key」：provider 条目
    优先，其次对应 .env 变量，最后才兼容旧 config 的 cloud.api_key。这样切到
    deepseek 时绝不会拿 MIMO_API_KEY 去调 DeepSeek 接口，避免 401/空回复。
    只在副本上改，不碰缓存本体，避免 update_config/roles_apply 把 env 密钥误持久化。"""
    out = copy.deepcopy(cfg)
    cloud_cfg = out.get("cloud") or {}
    cloud_provider = cloud_cfg.get("provider", "") or "custom"
    providers = out.setdefault("cloud_providers", {})
    # 各文字模型 provider 先收拢自己的 env key（磁盘已有非空值时不覆盖；
    # 空串视为未配置，同样从 env 补入，保证 config.json 不留明文密钥时前端回填正常）
    for name, env_name in _CLOUD_PROVIDER_ENV.items():
        key = os.getenv(env_name) or ""
        if key:
            entry = providers.setdefault(name, {})
            if not entry.get("api_key"):
                entry["api_key"] = key
    # 当前生效的 cloud.api_key：provider 条目 > env > 旧 cloud.api_key
    entry = providers.get(cloud_provider) or {}
    cur_key = entry.get("api_key") or ""
    if not cur_key:
        env_name = _CLOUD_PROVIDER_ENV.get(cloud_provider)
        cur_key = (os.getenv(env_name) or "") if env_name else ""
    cloud_cfg = dict(cloud_cfg)
    if cur_key:
        cloud_cfg["api_key"] = cur_key
    out["cloud"] = cloud_cfg
    # 语音引擎的阿里云 key 独立注入
    voice = dict(out.get("voice", {}))
    aliyun_key = (voice.get("aliyun") or {}).get("api_key") or _env_aliyun_key()
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
        "voice": {"provider": "aliyun", "manual_provider": False},
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
        # 阻塞 IO 丢进线程池，避免事件循环卡住
        await asyncio.to_thread(tmp.write_text, data, encoding="utf-8")
        await asyncio.to_thread(tmp.replace, CONFIG_PATH)
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
# 每次都发真实 HTTP 探测既慢又给对端打无谓流量；同一 (provider, base_url, api_key) 3s 内复用结果。
_probe_cache: dict[tuple[str, str, str], tuple[float, bool, str]] = {}
_PROBE_TTL = 3.0
_PROBE_CACHE_MAX = 256  # 条目上限：超限按插入顺序淘汰最旧（FIFO），防无限增长


def _probe_cache_put(ck: tuple[str, str, str], value: tuple[float, bool, str]) -> None:
    _probe_cache[ck] = value
    while len(_probe_cache) > _PROBE_CACHE_MAX:
        _probe_cache.pop(next(iter(_probe_cache)), None)


async def probe_active_provider(cfg: dict) -> tuple[bool, str]:
    """探测当前 provider 的 OpenAI 兼容 /models 接口，返回 (在线, 错误信息)。"""
    provider = cfg.get("provider", "local")
    conf = cfg.get(provider, {})
    base_url = (conf.get("base_url") or "").rstrip("/")
    api_key = conf.get("api_key") or "none"
    # key 带上 api_key：不同供应商可能共用 base_url（如自定义网关），避免切换后命中旧缓存
    ck = (provider, base_url, api_key)
    now = time.monotonic()
    cached = _probe_cache.get(ck)
    if cached is not None and now - cached[0] < _PROBE_TTL:
        _probe_cache.move_to_end(ck)
        return cached[1], cached[2]
    if not base_url:
        _probe_cache_put(ck, (now, False, "未配置 API 地址"))
        return False, "未配置 API 地址"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = await httpx_client.get(f"{base_url}/models", headers=headers, timeout=3.5)
        r.raise_for_status()
        _probe_cache_put(ck, (now, True, ""))
        return True, ""
    except httpx.HTTPError as exc:
        _probe_cache_put(ck, (now, False, str(exc)))
        return False, str(exc)


# 风格标记解析：LLM 在回复开头用 [style:xxx] 标注朗读风格，让 TTS 按情绪动态合成。
# LLM 不总是把标记放最开头（实测有放在中间/末尾的情况），因此匹配全文任意位置的
# [style:x] / 【风格：x】 标记（兼容全角括号/冒号、大小写）：第一个匹配作为风格，
# 并把所有标记从正文剥离，避免标记残留进展示文本和 TTS 朗读。
_STYLE_RE = re.compile(r"[\[【](?:style|风格)\s*[:：]\s*([^\]】\r\n]+?)\s*[\]】]", re.I)

# 追加到 persona 末尾的风格前缀约定（/api/chat 与 /api/greeting 共用，内容不得分叉）
_STYLE_HINT = (
    "\n\n【输出格式】请在回复最开头用 [style:风格] 标注本句朗读风格（如 自然/激动/低沉/温柔/活泼/沉稳/俏皮 等），"
    "然后写回复正文。示例：[style:自然]今天天气不错。"
)


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


def _clean_history(history: list[dict], current: str) -> list[dict]:
    """压缩发送给模型的历史：折叠连续重复、去掉空消息、规整旧回复里的 style 标记。

    旧版前端曾把同一条用户消息 push 后整体发送，sessions 里因此残留连续重复；
    这些重复会让小模型把同一句当成两条输入，更容易机械复读。"""
    cleaned: list[dict] = []
    current = (current or "").strip()
    for m in history[-20:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        if role == "assistant":
            _, content = parse_style_prefix(content)
        if not content:
            continue
        if cleaned and cleaned[-1]["role"] == role and cleaned[-1]["content"] == content:
            continue
        cleaned.append({"role": role, "content": content})
    if cleaned and cleaned[-1]["role"] == "user" and cleaned[-1]["content"] == current:
        cleaned.pop()
    return cleaned


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

    返回 (音频路径, MIME)。云端克隆优先读取 mp3，mp3 样本的音高稳定度
    实测优于 wav，因此单独优先读取 voice_<role>_cloud.mp3。"""
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
_FP_CACHE_MAX = 128  # 条目上限：超限按插入顺序淘汰最旧（FIFO）


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
    stat_tuple = tuple(ref_stats)

    cached = _fp_cache.get(cache_key)
    if cached is not None and cached[1] == stat_tuple:
        return cached[0]

    parts = list(base_parts)
    for _p, mt, sz in stat_tuple:
        parts += [str(mt), str(sz)]
    fp = "|".join(parts)
    _fp_cache[cache_key] = (fp, stat_tuple)
    while len(_fp_cache) > _FP_CACHE_MAX:
        _fp_cache.pop(next(iter(_fp_cache)), None)
    return fp


def _scan_tts_cache() -> tuple[list[tuple[int, int, Path]], int]:
    """同步扫描 TTS 缓存目录（由 asyncio.to_thread 调用）。

    排序键用 max(atime, mtime)：Windows NTFS 默认不更新 atime，
    只按 atime 会把刚用过的文件误判为最旧，mtime 兜底。"""
    entries: list[tuple[int, int, Path]] = []
    total_bytes = 0
    for p in TTS_CACHE_DIR.iterdir():
        if not p.is_file() or p.suffix != ".wav":
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append((max(st.st_atime_ns, st.st_mtime_ns), st.st_size, p))
        total_bytes += st.st_size
    return entries, total_bytes


async def _maybe_cleanup_tts_cache(force: bool = False) -> None:
    """懒触发 TTS 缓存 LRU 清理：超阈值时删最旧（最近使用时间），1h 内最多扫一次。

    force=True（lifespan 启动时用）跳过间隔检查，保证开机必扫一次；
    不用 monotonic 归零技巧，避免机器开机不足 1h 时启动清理被误跳过。"""
    global _TTS_CACHE_LAST_CLEAN
    now = time.monotonic()
    if not force and now - _TTS_CACHE_LAST_CLEAN < _TTS_CACHE_CLEAN_INTERVAL:
        return
    async with _io_locks["cache_cleanup"]:
        # double-check：持锁后用最新时间再看，避免等锁期间别人刚扫完又跑一遍
        now = time.monotonic()
        if not force and now - _TTS_CACHE_LAST_CLEAN < _TTS_CACHE_CLEAN_INTERVAL:
            return
        try:
            entries, total_bytes = await asyncio.to_thread(_scan_tts_cache)
        except OSError as exc:
            _log.warning("tts cache scan failed: %s", exc)
            return
        if len(entries) <= _TTS_CACHE_MAX_FILES and total_bytes <= _TTS_CACHE_MAX_BYTES:
            _TTS_CACHE_LAST_CLEAN = now
            return
        # 按最近使用时间升序（最旧的先删）
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


_START_MONOTONIC = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _refresh_tts_cache_limits()
    # 启动时后台触发一次 TTS 缓存清理（force 跳过间隔检查，不阻塞启动）
    _spawn_bg(_maybe_cleanup_tts_cache(force=True))
    yield
    # 关闭：先等后台任务收尾（上限 5s），再关 httpx 连接池
    if _bg_tasks:
        await asyncio.wait(list(_bg_tasks), timeout=5.0)
    await httpx_client.aclose()


app = FastAPI(title="AI 拟人系统", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # 只放行本服务自身来源（含 file:// 打开页面的 null Origin），不再允许任意跨域
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "null"],
    allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------- LLM --------
# 重试间的指数退避（第 1 次重试前 0.5s，第 2 次前 1.5s），避免瞬时重试加重对端限流
_LLM_RETRY_BACKOFF = (0.5, 1.5)


async def llm_chat(messages: list[dict], temperature: float = 0.8, max_tokens: int = 768,
                   model: str | None = None, disable_thinking: bool = False,
                   thinking: bool | None = None, anti_repeat: bool = False) -> str:
    """按 config 里的 provider 调用本地 llama-server 或云端 OpenAI 兼容 API。

    temperature/max_tokens/model 可覆盖：对话用默认值；角色引擎后处理传
    disable_thinking=True 强制模型直接输出正文 JSON，避免 deepseek-v4-flash
    思考过程吃光 max_tokens 导致 content 为空；thinking 显式覆盖 config 开关。"""
    cfg = load_config()
    provider = cfg.get("provider", "cloud")
    if provider not in ("local", "cloud"):
        raise HTTPException(400, f"未知 provider: {provider}")
    conf = cfg.get(provider) or {}
    base_url = (conf.get("base_url") or "").rstrip("/")
    api_key = conf.get("api_key") or "none"
    model = model or conf.get("model", "")
    if not base_url or not model:
        raise HTTPException(400, f"provider 配置不完整: {provider}")
    provider_name = (conf.get("provider") or "") if provider == "cloud" else "local"
    use_thinking = bool(conf.get("thinking", True)) if thinking is None else bool(thinking)
    if disable_thinking:
        use_thinking = False
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,  # 512 会截断长回复（sessions.json 里多条回复半句话被掐断），截断的回复进历史更易被模型重复续写
    }
    # 本地小模型（Qwen3-4B）对长人设+历史很容易机械复读，显式惩罚重复 token；
    # 云端 OpenAI 兼容供应商不保证支持 repeat_penalty，因此只在 local 下发送。
    if provider == "local":
        payload["repeat_penalty"] = 1.5 if anti_repeat else 1.3
        payload["presence_penalty"] = 1.0 if anti_repeat else 0.6
        payload["frequency_penalty"] = 0.6 if anti_repeat else 0.3
        payload["top_p"] = 0.95
    # MiMo / DeepSeek v4 实测支持 thinking.type 开关；关闭后不返回 reasoning_content，
    # 正文不再被思考过程挤占，空回复概率大幅下降
    if provider_name in ("mimo", "deepseek"):
        payload["thinking"] = {"type": "enabled" if use_thinking else "disabled"}
    headers = {"Authorization": f"Bearer {api_key}"}
    if api_key and api_key != "none":
        headers["api-key"] = api_key
    # 更精确的分阶段超时：connect/write 短，read 留给模型推理
    timeout = httpx.Timeout(5.0, connect=5.0, write=10.0, read=180.0, pool=15.0)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = await httpx_client.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                raise HTTPException(502, f"LLM 响应缺少 choices（provider={provider_name}, model={model}），请检查云端模型配置")
            choice = choices[0]
            msg = choice.get("message") or {}
            content = (msg.get("content") or "").strip()
            finish_reason = choice.get("finish_reason") or ""
            # deepseek-v4-flash 等 reasoning 模型偶发 content 为空但 reasoning_content 有值，
            # 从 reasoning_content 兜底提取（后处理场景只需文本，不需要区分 reasoning/final）
            if not content:
                reasoning = (msg.get("reasoning_content") or "").strip()
                if reasoning:
                    content = reasoning
            if not content:
                # 空正文：先重试；启用思考时顺手关掉 thinking 强制模型出正文，
                # 仍为空再报错并带上 provider/model/finish_reason 方便排查模型配置。
                if attempt < 2:
                    last_err = HTTPException(502, "模型返回了空回复")
                    if use_thinking and provider_name in ("mimo", "deepseek"):
                        payload["thinking"] = {"type": "disabled"}
                    _log.info("llm retry: attempt=%d reason=empty_content", attempt + 1)
                    await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
                    continue
                detail = f"provider={provider_name}, model={model}"
                if finish_reason:
                    detail += f", finish_reason={finish_reason}"
                raise HTTPException(502, f"模型返回了空回复，请重试；若持续失败请检查云端模型配置（{detail}）")
            return content
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            # 5xx 与 429（限流）可重试，其余状态码直接报错
            retryable = exc.response.status_code >= 500 or exc.response.status_code == 429
            if not retryable or attempt == 2:
                if exc.response.status_code == 401:
                    raise HTTPException(502, "云端 API 鉴权失败：请检查 API Key")
                raise HTTPException(502, f"LLM 调用失败: {exc}")
            last_err = exc
            _log.info("llm retry: attempt=%d status=%d err=%s", attempt + 1, exc.response.status_code, exc)
            await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
        except httpx.TransportError as exc:
            if attempt == 2:
                if provider == "local":
                    raise HTTPException(502, "本地模型未启动：请先运行 start_llm.bat 或用云端 API")
                raise HTTPException(502, f"云端 API 连接失败: {exc}")
            last_err = exc
            _log.info("llm retry: attempt=%d transport_err=%s", attempt + 1, exc)
            await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
        except (KeyError, IndexError, ValueError) as exc:
            raise HTTPException(502, f"LLM 响应解析失败: {exc}")
    raise HTTPException(502, f"LLM 调用失败: {last_err}")


# ------------------------------------------------------------- 搜索 ----------
_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SEARCH_CACHE_TTL = 600.0
_SEARCH_CACHE_MAX = 256  # 条目上限：超限按插入顺序淘汰最旧（FIFO）
_SEARCH_RE = re.compile(r"(?:\[|【)(?:search|搜索)\s*[:：]\s*([^\]】\r\n]{1,160})(?:\]|】)")


class _DDGResultParser(HTMLParser):
    """解析 DuckDuckGo HTML 结果页：标题/摘要/真实链接。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._capture: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        classes = attr_map.get("class", "").split()
        if tag == "div" and "result" in classes and "web-result" in classes:
            if self._cur and self._cur.get("title"):
                self.results.append(self._cur)
            self._cur = {"title": "", "snippet": "", "url": ""}
        if self._cur is None:
            return
        if tag == "a" and "result__a" in classes:
            self._capture = "title"
            self._buf = []
            self._cur["url"] = _ddg_result_url(attr_map.get("href", ""))
        elif tag == "a" and "result__snippet" in classes:
            self._capture = "snippet"
            self._buf = []

    def handle_endtag(self, tag):
        if self._capture and tag == "a":
            text = " ".join("".join(self._buf).split())
            if self._cur is not None:
                self._cur[self._capture] = text
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)

    def close(self):
        super().close()
        if self._cur and self._cur.get("title"):
            self.results.append(self._cur)


def _ddg_result_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        qs = parse_qs(urlparse(href).query)
        if qs.get("uddg"):
            return qs["uddg"][0]
    except ValueError:
        pass
    return href


def _search_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    }


async def _search_duckduckgo(query: str, timeout: float = 15.0) -> list[dict]:
    r = await httpx_client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_search_headers(),
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    parser = _DDGResultParser()
    parser.feed(r.text)
    parser.close()
    return parser.results


async def _search_bing_rss(query: str, timeout: float = 15.0) -> list[dict]:
    r = await httpx_client.get(
        "https://www.bing.com/search",
        params={"format": "rss", "q": query},
        headers=_search_headers(),
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out: list[dict] = []
    for item in root.findall(".//item")[:10]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "")
        desc = " ".join(desc.split())
        out.append({"title": title, "snippet": desc, "url": link})
    return out


async def web_search(query: str, cfg: dict | None = None, max_results: int = 5) -> list[dict]:
    """按配置执行联网搜索；DuckDuckGo 为主，失败/无结果时用 Bing RSS 兜底。"""
    cfg = cfg or load_config()
    search_cfg = cfg.get("search") or {}
    if not search_cfg.get("enabled", True):
        return []
    provider = (search_cfg.get("provider") or "duckduckgo").lower()
    max_results = max(1, min(int(search_cfg.get("max_results", max_results) or max_results), 10))
    timeout = float(search_cfg.get("timeout", 15) or 15)
    key = f"{provider}\x00{query.strip()[:200].lower()}"
    now = time.time()
    cached = _SEARCH_CACHE.get(key)
    if cached and now - cached[0] < _SEARCH_CACHE_TTL:
        _SEARCH_CACHE.move_to_end(key)
        return cached[1][:max_results]
    results: list[dict] = []
    try:
        if provider == "bing":
            results = await _search_bing_rss(query, timeout)
        else:
            results = await _search_duckduckgo(query, timeout)
    except Exception as exc:  # noqa: BLE001
        _log.warning("web_search %s failed: %s", provider, exc)
    if not results and provider != "bing":
        try:
            results = await _search_bing_rss(query, timeout)
        except Exception as exc:  # noqa: BLE001
            _log.warning("web_search bing fallback failed: %s", exc)
    elif not results:
        try:
            results = await _search_duckduckgo(query, timeout)
        except Exception as exc:  # noqa: BLE001
            _log.warning("web_search ddg fallback failed: %s", exc)
    results = results[:max_results]
    _SEARCH_CACHE[key] = (now, results)
    while len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)), None)
    return results


def _extract_search_query(text: str) -> str:
    m = _SEARCH_RE.search(text or "")
    return m.group(1).strip()[:160] if m else ""


def _strip_search_markers(text: str) -> str:
    return _SEARCH_RE.sub("", text or "").strip()


def _format_search_feedback(query: str, results: list[dict]) -> str:
    if not results:
        return (
            f"（你发起了搜索 [search:{query}]，但没有搜到结果。请如实告诉用户暂时没有查到，"
            "不要编造新闻，再基于你已有的知识自然回答。）"
        )
    lines = [
        f"（你发起了搜索 [search:{query}]。下面是搜索到的资料，请优先使用这些最新信息回答，"
        "用自己的话复述，不要编造来源，也不要罗列链接。）"
    ]
    for i, r in enumerate(results[:8], 1):
        title = r.get("title") or "无标题"
        snippet = r.get("snippet") or ""
        url = r.get("url") or ""
        lines.append(f"{i}. {title}\n{snippet}\n来源：{url}")
    return "\n".join(lines)


async def _chat_with_search(
    system: dict,
    history: list[dict],
    user_content: str,
    cfg: dict,
    temperature: float = 0.8,
    anti_repeat: bool = False,
) -> tuple[str, bool]:
    """对话主循环：模型输出 [search:...] 时自动搜索并回填结果后再次回答。"""
    search_cfg = cfg.get("search") or {}
    max_rounds = int(search_cfg.get("max_rounds", 2) or 2)
    messages = [dict(system)] + [dict(m) for m in history] + [{"role": "user", "content": user_content}]
    searched = False
    last_raw = ""
    for _ in range(max_rounds + 1):
        last_raw = await llm_chat(messages, temperature=temperature, anti_repeat=anti_repeat)
        query = _extract_search_query(last_raw)
        if not query:
            break
        searched = True
        results = await web_search(query, cfg)
        messages.append({"role": "assistant", "content": last_raw})
        messages.append({"role": "user", "content": _format_search_feedback(query, results)})
    return _strip_search_markers(last_raw), searched


# ---------------------------------------------------------------- TTS ---------
def _run_sync(*args: str, timeout: float = 660) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired:
        # 子进程卡死（常见于本地模型假死）：转成明确的 504，不再裸 500
        raise HTTPException(
            504,
            f"本地模型子进程执行超时（{int(timeout)} 秒），请稍后重试；"
            "若持续出现请检查本地 TTS/ASR 服务是否卡死或重启对应服务",
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
                _log.info("tts cache hit: %s", cache.name)
                return cache
        except OSError:
            pass
    # 已有同 key 合成在飞，直接等它的结果（shield 防止等待方被取消时连带取消合成方）；
    # force=True 也登记 _tts_inflight，因此并发的重复请求（含 force）会在此合并，避免重复合成
    inflight = _tts_inflight.get(cache.name)
    if inflight is not None:
        return await asyncio.shield(inflight)
    _spawn_bg(_maybe_cleanup_tts_cache())
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _tts_inflight[cache.name] = fut
    t0 = time.monotonic()
    try:
        path = await _tts_do_synthesize(text, style, cfg, cache)
        if not fut.done():
            fut.set_result(path)
        _log.info("tts synthesized in %.2fs: %s", time.monotonic() - t0, cache.name)
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
        raise HTTPException(400, "MiMo 语音合成已移除，请使用本地合成或阿里云千问")
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
        detail = exc.response.text[:500] if getattr(exc, "response", None) is not None else str(exc)
        if "AllocationQuota.FreeTierOnly" in detail:
            detail = "阿里云免费额度已用完，请到百炼控制台充值或关闭“仅使用免费额度”，或改用本地合成"
        raise HTTPException(502, f"阿里云 TTS 调用失败: {detail}")
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, f"阿里云 TTS 响应解析失败: {exc}")
    out_path = OUTPUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"
    audio_data = data.get("data")
    if audio_data:
        try:
            await asyncio.to_thread(out_path.write_bytes, base64.b64decode(audio_data))
        except (ValueError, TypeError):
            raise HTTPException(502, "阿里云 TTS 返回的不是有效音频数据")
    elif data.get("url"):
        try:
            resp = await httpx_client.get(data["url"], timeout=120)
            resp.raise_for_status()
            await asyncio.to_thread(out_path.write_bytes, resp.content)
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
    message: str = ""
    history: list[dict] = []
    attachments: list[dict] = []


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5


def _upload_path_for(attachment: dict) -> Path | None:
    """从附件元数据解析服务器本地文件，只接受本服务生成的 /uploads/ 路径。"""
    url = attachment.get("url") or ""
    if not url.startswith("/uploads/"):
        return None
    name = Path(url).name
    if name in (".", "..") or "/" in name or "\\" in name:
        return None
    path = UPLOAD_DIR / name
    return path if path.is_file() else None


def _attachment_label(kind: str) -> str:
    return {"image": "图片", "doc": "文档"}.get(kind, "文件")


def _attachment_prompt_text(attachments: list[dict], user_text: str = "") -> str:
    """把附件转成模型能读的文字描述；文本类文档直接带内容片段。"""
    lines: list[str] = []
    for a in attachments[:8]:
        name = a.get("name") or "附件"
        label = _attachment_label(a.get("kind") or "file")
        lines.append(f"- [{label}] {name}")
        path = _upload_path_for(a)
        if path and a.get("kind") == "doc" and path.suffix.lower() in _TEXT_DOC_SUFFIXES:
            try:
                snippet = path.read_text(encoding="utf-8", errors="replace")[:2000]
                if snippet.strip():
                    lines.append(f"  <文件内容>{snippet}</文件内容>")
            except OSError:
                pass
    if not lines:
        return user_text
    prefix = "用户发送了附件：\n" + "\n".join(lines)
    if user_text:
        prefix += f"\n用户留言：{user_text}"
    return prefix


async def _call_vision(messages: list[dict], cfg: dict) -> str | None:
    """调用本地/云端视觉模型；失败或空回复返回 None 交给普通文本模型兜底。"""
    vision_cfg = cfg.get("vision") or {}
    base_url = (vision_cfg.get("base_url") or "http://127.0.0.1:11435/v1").rstrip("/")
    model = vision_cfg.get("model") or "Qwen2.5-VL-3B"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 768,
        "repeat_penalty": 1.3,
        "top_p": 0.95,
    }
    timeout = httpx.Timeout(5.0, connect=5.0, write=10.0, read=240.0, pool=15.0)
    try:
        r = await httpx_client.post(
            f"{base_url}/chat/completions", json=payload, timeout=timeout
        )
        r.raise_for_status()
        choices = r.json().get("choices") or []
        if not choices:
            return None
        content = (choices[0].get("message") or {}).get("content") or ""
        return content.strip() or None
    except (httpx.HTTPError, KeyError, ValueError, OSError):
        return None


async def _try_vision_chat(system: dict, history: list[dict], req: ChatRequest, cfg: dict) -> str | None:
    """有图片附件时把图片以 data URI 送入视觉模型；不可用则返回 None。"""
    if not (cfg.get("vision") or {}).get("enabled", True):
        return None
    content: list[dict] = []
    for a in req.attachments[:4]:
        if a.get("kind") != "image":
            continue
        path = _upload_path_for(a)
        if not path:
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        if len(content) >= 3:
            break
    if not content:
        return None
    text = req.message.strip() or "（用户发送了图片）"
    content.append({"type": "text", "text": text})
    messages = [dict(system)] + [dict(m) for m in history] + [{"role": "user", "content": content}]
    return await _call_vision(messages, cfg)


def _last_assistant_content(history: list[dict]) -> str:
    """返回历史中最后一条 assistant 消息（用于判断是否复读了上一条回复）。"""
    for m in reversed(history):
        if m.get("role") == "assistant":
            return m.get("content") or ""
    return ""


def _too_similar_to_last(text: str, history: list[dict]) -> bool:
    prev = _last_assistant_content(history)
    if not prev or not text:
        return False
    return role_engine._dup_sim(text, prev) >= 0.8


def _is_degenerate_reply(text: str) -> bool:
    """检测机械复读：空回复、连续 20+ 个相同字符、或某个 3-gram 占比超过 60%。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return True
    if re.search(r"(.)\1{19,}", t):
        return True
    if len(t) >= 12:
        tris = [t[i:i + 3] for i in range(len(t) - 2)]
        counts: dict[str, int] = {}
        for g in tris:
            counts[g] = counts.get(g, 0) + 1
        if counts and max(counts.values()) / len(tris) > 0.6:
            return True
    return False


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
        # 密钥按用户要求不回填脱敏：返回完整明文，前端回填输入框并支持显隐切换
        "cloud": dict(cloud_cfg),
        "cloud_providers": {k: dict(v) for k, v in cfg.get("cloud_providers", {}).items()},
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
        "aliyun_configured": bool(cfg.get("voice", {}).get("aliyun", {}).get("api_key")),
        "voice_aliyun": dict(cfg.get("voice", {}).get("aliyun", {})),
        "voice_style": cfg.get("voice", {}).get("style", ""),
        "voice_manual_provider": bool(cfg.get("voice", {}).get("manual_provider")),
    }


@app.get("/api/health")
async def health():
    """轻量健康检查：只报进程自身状态与缓存概况，不探测外部服务。"""
    try:
        cache_files = sum(1 for p in TTS_CACHE_DIR.iterdir() if p.is_file())
    except OSError:
        cache_files = -1
    return {
        "ok": True,
        "uptime": round(time.monotonic() - _START_MONOTONIC, 1),
        "bg_tasks": len(_bg_tasks),
        "tts_cache_files": cache_files,
    }


@app.post("/api/search")
async def search_api(req: SearchRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "搜索词不能为空")
    if len(query) > 200:
        raise HTTPException(400, "搜索词过长")
    max_results = max(1, min(req.max_results, 10))
    results = await web_search(query, max_results=max_results)
    return {"query": query, "results": results}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    user_text = req.message.strip()
    if not user_text and not req.attachments:
        raise HTTPException(400, "消息不能为空")
    cfg = load_config()
    active_role = cfg.get("active_role", "")
    role_name = (cfg.get("roles", {}).get(active_role) or {}).get("name") or active_role
    # 角色引擎：启用时把 时间层/状态层/记忆层 上下文注入 system prompt（前端零改动）
    engine_cfg = cfg.get("role_engine") or {}
    engine_on = bool(engine_cfg.get("enabled", True))
    ctx_block = ""
    mem, st = None, None
    if engine_on and active_role:
        try:
            mem, st = _get_role_stores(active_role)
            ctx = role_engine.build_context(mem, st, user_text or "（附件）", top_k=int(engine_cfg.get("top_k", 5)))
            ctx_block = role_engine.format_context_block(ctx)
        except Exception as exc:  # noqa: BLE001
            _log.warning("role_engine context build failed: %s", exc)
            ctx_block = ""
    # 在 persona 末尾追加风格前缀约定，让 LLM 输出 [style:xxx] 标注朗读情绪，
    # server 解析后剥离前缀只把纯回复存 history，避免污染对话上下文。
    persona = current_persona(cfg)
    style_hint = _STYLE_HINT
    search_hint = ""
    if (cfg.get("search") or {}).get("enabled", True):
        search_hint = (
            f"\n\n【联网搜索】当用户问到关于你（{role_name}）的最新消息（你的比赛、战队、近况、战绩等），"
            "或其他需要最新信息的问题，而你的知识可能过时时，请在回复最开头单独输出 "
            "[search:搜索词]，例如 [search:成都AG超玩会 大帅 2026年KPL]。"
            "我会自动搜索并把结果发给你，你再基于结果正常回答；最终回答里不要保留 [search:...]。"
        )
    system = {"role": "system", "content": persona + ctx_block + style_hint + search_hint}
    history = _clean_history(req.history, user_text)
    raw = None
    vision_used = False
    searched = False
    if req.attachments:
        raw = await _try_vision_chat(system, history, req, cfg)
        vision_used = raw is not None
        if raw is not None:
            raw = _strip_search_markers(raw)
    if raw is None:
        raw, searched = await _chat_with_search(
            system, history, _attachment_prompt_text(req.attachments, user_text), cfg
        )
    fallback_style = cfg.get("voice", {}).get("style") or "自然"
    style, reply = parse_style_prefix(raw, fallback=fallback_style)
    if _is_degenerate_reply(reply) or _too_similar_to_last(reply, history):
        # 小模型偶发把上一条回复原样复读或陷入单字循环；用更强的采样惩罚 + 明确指令重试一次。
        retry_system = dict(system)
        retry_system["content"] = system["content"] + (
            "\n\n【注意】用户已经换话题或指出你重复了。请完全重新组织语言，"
            "不要复读你上一条回复，不要重复同一句话或同一个字，直接针对这条新消息回答。"
        )
        raw2 = None
        if vision_used:
            raw2 = await _try_vision_chat(retry_system, history, req, cfg)
            if raw2 is not None:
                raw2 = _strip_search_markers(raw2)
        if raw2 is None:
            raw2, _ = await _chat_with_search(
                retry_system,
                history,
                _attachment_prompt_text(req.attachments, user_text),
                cfg,
                temperature=1.0,
                anti_repeat=True,
            )
        style2, reply2 = parse_style_prefix(raw2, fallback=fallback_style)
        if not _is_degenerate_reply(reply2) and (
            _is_degenerate_reply(reply) or not _too_similar_to_last(reply2, history)
        ):
            style, reply = style2, reply2
    post_text = user_text or (
        "用户发送了附件：" + "、".join((a.get("name") or "附件") for a in req.attachments[:8])
    )
    # 对话后处理（异步，不阻塞）：更新情绪状态 + 抽取长期记忆写回
    if engine_on and mem is not None and st is not None and reply:
        _spawn_bg(_post_process_chat(active_role, post_text, reply))
    return {"reply": reply, "style": style, "vision_used": vision_used, "searched": searched}


async def _post_process_chat(role: str, user_msg: str, reply: str) -> None:
    """后台：用轻量 LLM 调用抽取情绪变化与新事实，写回状态库+记忆库。
    任何失败都静默跳过，绝不影响主对话链路。"""
    try:
        mem, st = _get_role_stores(role)
        state = st.get_decayed()
        # 喂已有最相关记忆给标注器，从源头避免重复事实反复入库
        existing = [m["text"] for m in mem.search(user_msg, top_k=5)]
        ann = await _get_post_processor().run(user_msg, reply, state.get("emotion") or {},
                                              existing_memories=existing)
        if not ann:
            return
        st.update(emotion=ann.get("emotion"), energy_delta=ann.get("energy_delta", 0), intimacy_delta=0.01)
        for text in ann.get("memories") or []:
            # 防记忆污染：AI 自己的回复原文/台词不得当成用户事实入库，否则下轮会被注入 system 导致复读。
            if role_engine._dup_sim(text, reply) >= 0.5 or role_engine._dup_sim(text, user_msg) >= 0.5:
                continue
            # 写前预过滤：与库中最相似记忆重合度高则 touch 旧条目，不新增冗余
            near = mem.search(text, top_k=1)
            if near and role_engine._dup_sim(text, near[0].get("text", "")) >= 0.5:
                mem.touch(near[0]["id"])
                continue
            mem.add(text, importance=0.6)
    except Exception as exc:  # noqa: BLE001
        # 后处理失败不影响主链路，但记 warning 便于排查偶发空响应/超时
        _log.warning("post_process_chat skipped for %s: %s: %s", role, type(exc).__name__, exc)


@app.get("/api/state")
async def role_state():
    """当前角色状态（情绪/精力/亲密度/记忆概况），供前端展示与调试。"""
    cfg = load_config()
    role = cfg.get("active_role", "")
    if not role or not cfg.get("role_engine", {}).get("enabled", True):
        return {"enabled": False, "role": role}
    try:
        mem, st = _get_role_stores(role)
        state = st.get_decayed()
        return {
            "enabled": True,
            "role": role,
            "emotion": state.get("emotion"),
            "energy": state.get("energy"),
            "intimacy": state.get("intimacy"),
            "last_update": state.get("last_update"),
            "memory_count": mem.count(),
        }
    except Exception as exc:  # noqa: BLE001
        _log.warning("api/state failed: %s", exc)
        raise HTTPException(500, f"读取状态失败: {exc}")


@app.delete("/api/state")
async def role_state_reset():
    """重置当前角色的状态与记忆（调试/重开用）。"""
    cfg = load_config()
    role = cfg.get("active_role", "")
    if not role:
        raise HTTPException(400, "未激活角色")
    mem, st = _get_role_stores(role)
    ok1, ok2 = st.reset(), mem.clear()
    if not (ok1 and ok2):
        raise HTTPException(500, "重置失败，请检查 data/ 目录权限")
    return {"ok": True, "role": role}


@app.post("/api/greeting")
async def role_greeting():
    """主动问候：基于记忆+情绪+时间生成大帅的主动开场白。

    前端在页面打开时按阈值调用（距上次聊天够久 / 新的一天还没问候过）。
    频率控制在前端 localStorage（每天最多 1-2 次），后端只负责生成。
    """
    cfg = load_config()
    active_role = cfg.get("active_role", "")
    engine_cfg = cfg.get("role_engine") or {}
    engine_on = bool(engine_cfg.get("enabled", True))
    if not active_role:
        raise HTTPException(400, "未激活角色")
    persona = current_persona(cfg)
    # 组装上下文（和 /api/chat 同一套，但不带用户消息 -- 这是主动开口）
    ctx_block = ""
    if engine_on:
        try:
            mem, st = _get_role_stores(active_role)
            ctx = role_engine.build_context(mem, st, "", top_k=int(engine_cfg.get("top_k", 5)))
            ctx_block = role_engine.format_context_block(ctx)
        except Exception as exc:  # noqa: BLE001
            _log.warning("greeting context build failed: %s", exc)
    style_hint = _STYLE_HINT
    greeting_instruction = (
        "\n\n【任务】现在是你主动找老公说话的时刻。没有人先开口，是你想找他聊两句。"
        "可以是一句关心、一个分享、或者想起之前聊过的事顺嘴提一句。"
        "就像真人微信里突然发来一条消息一样自然，1-3 句话，别太长，别像系统通知。"
        "不要用'你好'这种客套开场。"
    )
    system = {"role": "system", "content": persona + ctx_block + greeting_instruction + style_hint}
    messages = [system, {"role": "user", "content": "（你主动发消息给老公）"}]
    raw = await llm_chat(messages)
    fallback_style = cfg.get("voice", {}).get("style") or "自然"
    style, reply = parse_style_prefix(raw, fallback=fallback_style)
    if not reply.strip():
        reply = "老公，在忙吗？"
        style = fallback_style
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
            raw = await asyncio.to_thread(SESSIONS_PATH.read_text, encoding="utf-8")
            data = json.loads(raw)
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
        await asyncio.to_thread(tmp.write_text, payload_str, encoding="utf-8")
        await asyncio.to_thread(tmp.replace, SESSIONS_PATH)
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
    cloud_thinking: bool | None = None
    persona: str = ""
    voice_language: str = "Chinese"
    voice_provider: str = ""
    voice_style: str = ""
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
    # 每个云端供应商独立保存 key/model/base_url/thinking：切换供应商后各用各的，
    # 不再让 cloud.api_key 被上一个供应商的密钥串用
    cloud_providers = cfg.setdefault("cloud_providers", {})
    cur_provider = cloud_cfg.get("provider", "") or "custom"
    entry = cloud_providers.setdefault(cur_provider, {})
    # 旧配置迁移：config.json 里只有 cloud.api_key 时，把它收进当前 provider 条目
    if not entry.get("api_key") and cloud_cfg.get("api_key"):
        entry["api_key"] = cloud_cfg["api_key"]
    if upd.cloud_base_url:
        cloud_cfg["base_url"] = upd.cloud_base_url
        entry["base_url"] = upd.cloud_base_url
    elif upd.cloud_provider and entry.get("base_url") and not cloud_cfg.get("base_url"):
        cloud_cfg["base_url"] = entry["base_url"]
    if upd.cloud_api_key:
        cloud_cfg["api_key"] = upd.cloud_api_key
        entry["api_key"] = upd.cloud_api_key
    elif upd.cloud_provider and entry.get("api_key") and not cloud_cfg.get("api_key"):
        cloud_cfg["api_key"] = entry["api_key"]
    if upd.cloud_model:
        cloud_cfg["model"] = upd.cloud_model
        entry["model"] = upd.cloud_model
    elif upd.cloud_provider and entry.get("model") and not cloud_cfg.get("model"):
        cloud_cfg["model"] = entry["model"]
    if upd.cloud_thinking is not None:
        cloud_cfg["thinking"] = bool(upd.cloud_thinking)
        entry["thinking"] = bool(upd.cloud_thinking)
    if upd.persona:
        cfg["persona"] = upd.persona
    if upd.voice_language:
        cfg.setdefault("voice", {})["language"] = upd.voice_language
    # 声音克隆引擎（与对话 API 完全独立的另一套配置）
    if upd.voice_provider in ("local", "aliyun"):
        voice_cfg = cfg.setdefault("voice", {})
        voice_cfg["provider"] = upd.voice_provider
        voice_cfg["manual_provider"] = True
    if upd.voice_style:
        cfg.setdefault("voice", {})["style"] = upd.voice_style
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


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """附件上传：后缀白名单 + 大小上限，随机文件名落盘后返回可访问元数据。"""
    if not files:
        raise HTTPException(400, "未选择文件")
    if len(files) > 8:
        raise HTTPException(400, "一次最多上传 8 个文件")
    results = []
    for f in files:
        original = (f.filename or "").strip() or "file"
        suffix = (Path(original).suffix or "").lower()
        if suffix not in _ALLOWED_ATTACH_SUFFIXES:
            raise HTTPException(400, f"不支持的文件类型: {suffix or '无扩展名'}（{original}）")
        stored = f"att_{uuid.uuid4().hex[:12]}{suffix}"
        dest = UPLOAD_DIR / stored
        total = 0
        try:
            with dest.open("wb") as fout:
                while True:
                    chunk = await f.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_ATTACH_UPLOAD_BYTES:
                        raise HTTPException(
                            413, f"文件过大（上限 {_MAX_ATTACH_UPLOAD_BYTES // 1024 // 1024}MB）"
                        )
                    fout.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(500, f"上传保存失败: {exc}")
        if total == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"文件为空: {original}")
        kind = "image" if suffix in _IMAGE_SUFFIXES else ("doc" if suffix in _DOC_SUFFIXES else "file")
        results.append({
            "name": original,
            "url": f"/uploads/{stored}",
            "kind": kind,
            "suffix": suffix,
            "size": total,
            "mime": mimetypes.guess_type(stored)[0] or "application/octet-stream",
        })
    return {"files": results}


# 静态托管新前端（挂在所有 API 路由之后，/api/* 优先匹配，其余走静态文件）
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="site")
