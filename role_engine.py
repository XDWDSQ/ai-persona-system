# -*- coding: utf-8 -*-
"""角色运行引擎：为 AI 拟人角色注入「长期记忆 + 情绪状态 + 时间感知」。

设计文档：docs/superpowers/specs/2026-08-03-role-engine-design.md

模块职责（与 server.py 解耦，不依赖 FastAPI）：
  1. MemoryStore  — 长期记忆库：写入去重、检索打分、容量裁剪
  2. StateStore   — 状态库：情绪(valence/arousal)、精力、亲密度，平滑移动 + 自然衰减
  3. TimeContext  — 时间感知：真实时间 + 角色日程 → 此刻上下文文案
  4. build_context— 组装注入 system prompt 的三层上下文
  5. post_process — 对话后异步抽取 {情绪变化, 新事实} 写回（由调用方负责不阻塞）

所有文件读写均为「原子写（tmp + replace）」，损坏时自动重置，绝不影响主链路。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_log = logging.getLogger("role_engine")

# ---------------------------------------------------------------- 常量 --------
DEFAULT_STATE = {
    "version": 1,
    "emotion": {"valence": 0.2, "arousal": 0.3},   # valence: -1 难过 ~ 1 开心；arousal: 0 平静 ~ 1 激动
    "energy": 0.7,                                  # 精力 0 ~ 1
    "intimacy": 0.3,                                # 亲密度 0 ~ 1
    "last_update": None,                            # ISO 时间串
}

_EMO_MAX_DELTA = 0.2        # 单次后处理情绪最大移动量（防变脸）
_DECAY_START_H = 2.0        # 距上次更新超过此小时数开始衰减
_DECAY_RATE_PER_H = 0.1     # 每小时向基线靠拢的速率
_BASELINE_EMOTION = {"valence": 0.2, "arousal": 0.3}
_MEMORY_LIMIT_DEFAULT = 200
_MEMORY_TOP_K_DEFAULT = 5
_DUP_SIM_THRESHOLD = 0.5    # 语义去重阈值（字符 3-gram Jaccard，含长度比约束）
_DUP_LEN_RATIO = (0.6, 1.67)  # 判定重复时两文本长度比需在此区间（防"短句 vs 超长句"误并）

# 内置默认日程（可被记忆覆盖）
_WEEKDAY_SCHEDULE = {
    0: ("周一", "训练日", "下午有训练，晚上是复盘时间"),
    1: ("周二", "训练日", "下午有训练，晚上是复盘时间"),
    2: ("周三", "训练日", "下午有训练，晚上是复盘时间"),
    3: ("周四", "训练日", "下午有训练，晚上是复盘时间"),
    4: ("周五", "训练日", "下午有训练，晚上是复盘时间"),
    5: ("周六", "比赛日或直播日", "今天是比赛日或直播日"),
    6: ("周日", "比赛日或直播日", "今天是比赛日或直播日"),
}

# ---------------------------------------------------------------- 工具 --------
def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _atomic_write(path: Path, data) -> bool:
    """原子写：tmp + replace。失败返回 False，不抛异常。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows 上无锁读（search/load）持有的文件句柄不带 FILE_SHARE_DELETE，
        # replace 会撞 PermissionError；读窗口仅毫秒级，短退避重试即可
        for attempt in range(3):
            try:
                tmp.replace(path)
                return True
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
    except OSError as exc:
        _log.warning("role_engine atomic write failed %s: %s", path, exc)
        return False


def _safe_load(path: Path, default: dict) -> dict:
    """读 JSON，损坏/不存在时返回 default 并尝试重置。"""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        _log.warning("role_engine load %s failed (%s), resetting", path, exc)
        _atomic_write(path, default)
    return default


def _fnum(v, default: float) -> float:
    """float 转换防御：手改/损坏的 JSON 字段为 null/非数字时回退默认值。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _shingles(text: str) -> set[str]:
    """字符 3-gram（去空白）用于相似度比较。"""
    text = re.sub(r"\s+", "", text or "")
    if len(text) < 3:
        return {text} if text else set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _text2grams(text: str) -> tuple[set[str], set[str]]:
    """返回 (2-gram 词集, 3-gram 词集)。"""
    t = re.sub(r"\s+", "", text or "")
    if len(t) < 2:
        return ({t} if t else set()), ({t} if t else set())
    big = {t[i:i + 2] for i in range(len(t) - 1)}
    tri = {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else set()
    return big, tri


def _dup_sim(a: str, b: str) -> float:
    """语义相似度：2-gram / 3-gram Jaccard 取 max（对同义改写更鲁棒）。"""
    a_big, a_tri = _text2grams(a)
    b_big, b_tri = _text2grams(b)
    return max(_jaccard(a_big, b_big), _jaccard(a_tri, b_tri))


# ---------------------------------------------------------------- 记忆库 --------
class MemoryStore:
    def __init__(self, data_dir: Path, role: str, limit: int = _MEMORY_LIMIT_DEFAULT):
        self.path = data_dir / "memory" / f"{role}.json"
        # 路径穿越双保险：role 若含 ../ 或绝对路径，resolve 后必然越出 memory 目录
        root = (data_dir / "memory").resolve()
        if not self.path.resolve().is_relative_to(root):
            raise ValueError(f"illegal role path: {self.path}")
        self.limit = limit
        self._lock = threading.RLock()
        # mtime 失效缓存：chat 主链路每次请求都会 search → load，
        # 命中缓存时省掉读盘+JSON 解析（写盘后显式失效）
        self._cache: tuple[int, dict] | None = None

    def _load_cached(self) -> dict:
        """读取原始 JSON（mtime 失效缓存）。文件缺失/损坏返回空基底。"""
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._cache = None
            return {"version": 1, "memories": []}
        if self._cache is not None and self._cache[0] == mtime:
            return self._cache[1]
        data = _safe_load(self.path, {"version": 1, "memories": []})
        self._cache = (mtime, data)
        return data

    def load(self) -> list[dict]:
        data = self._load_cached()
        mems = data.get("memories", [])
        if not isinstance(mems, list):
            return []
        # 返回浅拷贝副本：调用方（_touch_many 等）会在元素上原地修改，
        # 不能直接暴露缓存的原始列表（会污染后续命中的缓存读取）
        return [dict(m) if isinstance(m, dict) else m for m in mems]

    def _save(self, mems: list[dict]) -> bool:
        self._cache = None  # 写盘后失效缓存，下次读取重新加载
        return _atomic_write(self.path, {"version": 1, "memories": mems})

    # -- 检索 --
    def search(self, query: str, top_k: int = _MEMORY_TOP_K_DEFAULT) -> list[dict]:
        """按 关键词命中 + importance + 新鲜度 打分取 top_k。无结果时返回 []。

        打分阶段不加锁读快照；命中条目在返回前经 _touch_many 持锁批量刷新
        last_hit/hit_count（一次写盘），保证高频回忆的记忆不因时间衰减被淘汰。"""
        if not query or not query.strip():
            return []
        q_sh = _shingles(query)
        q_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", query.lower()))
        now = time.time()
        scored = []
        for m in self.load():
            text = m.get("text", "")
            # 关键词命中：记忆文本与查询共现的 2 字以上中文词 / 英文词
            m_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", text.lower()))
            overlap = len(q_tokens & m_tokens)
            sim = _jaccard(q_sh, _shingles(text))
            # 新鲜度：last_hit 越近越好，100 天内线性衰减到 0（10 天≈0.9，60 天≈0.4）
            last_hit = _parse_iso(m.get("last_hit"))
            recency = 1.0
            if last_hit:
                age_days = max(0.0, (now - last_hit.timestamp()) / 86400)
                recency = max(0.0, 1.0 - age_days / 100.0)
            score = 0.5 * (1.0 if overlap > 0 or sim > 0.4 else 0.0) \
                + 0.3 * _fnum(m.get("importance", 0.3), 0.3) \
                + 0.2 * recency
            if score > 0.05:
                scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        # 结果互斥：与已选条目高度相似的（同义改写）跳过，保证 top_k 条信息不重复
        picked: list[tuple[float, dict]] = []
        for score, m in scored:
            dup = any(_dup_sim(m.get("text", ""), p.get("text", "")) >= _DUP_SIM_THRESHOLD
                      for _, p in picked)
            if not dup:
                picked.append((score, m))
            if len(picked) >= top_k:
                break
        # 命中即刷新 last_hit/hit_count：高频回忆的老记忆不应按创建时间衰减被淘汰
        self._touch_many([m.get("id") for _, m in picked if m.get("id")])
        return [dict(m) for _, m in picked]

    def _touch_many(self, ids: list[str]) -> None:
        """检索命中后批量刷新 last_hit/hit_count（一次读盘一次写盘，失败静默）。"""
        if not ids:
            return
        try:
            with self._lock:
                mems = self.load()
                idset = set(ids)
                now_iso = _iso()
                hit = False
                for m in mems:
                    if m.get("id") in idset:
                        m["last_hit"] = now_iso
                        m["hit_count"] = int(_fnum(m.get("hit_count", 1), 1)) + 1
                        hit = True
                if hit:
                    self._save(mems)
        except Exception as exc:  # noqa: BLE001
            _log.debug("touch_many skipped: %s", exc)

    # -- 写入（去重） --
    def add(self, text: str, importance: float = 0.5, tags: list[str] | None = None,
            dedup: bool = True) -> bool:
        """写入一条记忆。

        dedup=True（默认）：与已有记忆相似度超阈值时更新旧条目（merge），不新增。
        dedup=False：跳过去重直接追加（批量导入/裁剪测试用）。
        """
        text = (text or "").strip()
        if not text:
            return False
        importance = max(0.0, min(1.0, float(importance)))
        # 读改写全程持锁：防止并发 add/touch 互相覆盖丢失写入
        with self._lock:
            mems = self.load()
            now_iso = _iso()
            if dedup:
                t_len = len(re.sub(r"\s+", "", text))
                best_idx, best_sim = -1, 0.0
                for i, m in enumerate(mems):
                    sim = _dup_sim(text, m.get("text", ""))
                    if sim > best_sim:
                        best_sim, best_idx = sim, i
                if best_sim >= _DUP_SIM_THRESHOLD and best_idx >= 0:
                    old = mems[best_idx]
                    o_len = len(re.sub(r"\s+", "", old.get("text", "")))
                    ratio = t_len / o_len if o_len else 0.0
                    if _DUP_LEN_RATIO[0] <= ratio <= _DUP_LEN_RATIO[1]:
                        # 更新：提升 importance、合并 tags、刷新时间戳（保留原 id）
                        old["text"] = text
                        old["importance"] = max(_fnum(old.get("importance", 0.3), 0.3), importance)
                        old["tags"] = list(dict.fromkeys((old.get("tags") or []) + (tags or [])))
                        old["last_hit"] = now_iso
                        old["hit_count"] = int(_fnum(old.get("hit_count", 1), 1)) + 1
                        self._save(mems)
                        return True
            mems.append({
                "id": uuid.uuid4().hex[:12],
                "text": text,
                "importance": importance,
                "tags": tags or [],
                "created_at": now_iso,
                "last_hit": now_iso,
                "hit_count": 1,
            })
            # 裁剪：按 importance × 新鲜度 淘汰最旧
            mems.sort(key=lambda m: self._score_for_evict(m), reverse=True)
            if len(mems) > self.limit:
                mems = mems[:self.limit]
            return self._save(mems)

    @staticmethod
    def _score_for_evict(m: dict) -> float:
        """裁剪排序分：importance × 新鲜度（新鲜度 60 天线性衰减到 0）。"""
        imp = _fnum(m.get("importance", 0.3), 0.3)
        last_hit = _parse_iso(m.get("last_hit"))
        recency = 1.0
        if last_hit:
            age_days = max(0.0, (time.time() - last_hit.timestamp()) / 86400)
            recency = max(0.0, 1.0 - age_days / 60.0)
        return imp * 0.6 + recency * 0.4

    def touch(self, mem_id: str) -> bool:
        """命中记忆刷新 last_hit / hit_count。"""
        with self._lock:
            mems = self.load()
            for m in mems:
                if m.get("id") == mem_id:
                    m["last_hit"] = _iso()
                    m["hit_count"] = int(_fnum(m.get("hit_count", 1), 1)) + 1
                    return self._save(mems)
            return False

    def clear(self) -> bool:
        with self._lock:
            return self._save([])

    def count(self) -> int:
        return len(self.load())


# ---------------------------------------------------------------- 状态库 --------
class StateStore:
    def __init__(self, data_dir: Path, role: str):
        self.path = data_dir / "state" / f"{role}.json"
        root = (data_dir / "state").resolve()
        if not self.path.resolve().is_relative_to(root):
            raise ValueError(f"illegal role path: {self.path}")
        self._lock = threading.RLock()
        # mtime 失效缓存：chat 主链路每次请求 get_decayed → load，
        # 命中缓存省读盘（load 每次构建全新 merged 返回，天然不污染缓存）
        self._cache: tuple[int, dict] | None = None

    def _load_cached(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._cache = None
            return dict(DEFAULT_STATE)
        if self._cache is not None and self._cache[0] == mtime:
            return self._cache[1]
        data = _safe_load(self.path, dict(DEFAULT_STATE))
        self._cache = (mtime, data)
        return data

    def load(self) -> dict:
        data = self._load_cached()
        # 兼容缺字段：补默认
        emo = data.get("emotion") or {}
        merged = dict(DEFAULT_STATE)
        merged.update({k: v for k, v in data.items() if k != "emotion"})
        # 数值字段统一 float() 防御：手改/损坏的 JSON 若为字符串，衰减计算会 TypeError
        try:
            merged["emotion"] = {
                "valence": float(emo.get("valence", DEFAULT_STATE["emotion"]["valence"])),
                "arousal": float(emo.get("arousal", DEFAULT_STATE["emotion"]["arousal"])),
            }
        except (TypeError, ValueError):
            merged["emotion"] = dict(DEFAULT_STATE["emotion"])
        try:
            merged["energy"] = float(merged.get("energy", DEFAULT_STATE["energy"]))
        except (TypeError, ValueError):
            merged["energy"] = DEFAULT_STATE["energy"]
        try:
            merged["intimacy"] = float(merged.get("intimacy", DEFAULT_STATE["intimacy"]))
        except (TypeError, ValueError):
            merged["intimacy"] = DEFAULT_STATE["intimacy"]
        return merged

    def _save(self, state: dict) -> bool:
        self._cache = None  # 写盘后失效缓存
        return _atomic_write(self.path, state)

    def get_decayed(self) -> dict:
        """读取并应用自然衰减（不落盘，只在返回副本上计算）。"""
        st = self.load()
        last = _parse_iso(st.get("last_update"))
        if last:
            hours = max(0.0, (_now() - last).total_seconds() / 3600)
            if hours > _DECAY_START_H:
                decay = min((hours - _DECAY_START_H) * _DECAY_RATE_PER_H, 1.0)
                emo = st["emotion"]
                emo["valence"] = emo["valence"] + (_BASELINE_EMOTION["valence"] - emo["valence"]) * decay
                emo["arousal"] = emo["arousal"] + (_BASELINE_EMOTION["arousal"] - emo["arousal"]) * decay
                # 精力向 0.7 基线靠拢
                st["energy"] = st["energy"] + (0.7 - st["energy"]) * decay
        return st

    def update(self, emotion: dict | None = None, energy_delta: float = 0.0,
               intimacy_delta: float = 0.0) -> dict:
        """更新状态：情绪平滑移动（每维最多 _EMO_MAX_DELTA），精力/亲密度增量封顶。"""
        with self._lock:
            st = self.get_decayed()
            now_iso = _iso()
            if emotion:
                for k in ("valence", "arousal"):
                    target = float(emotion.get(k, st["emotion"][k]))
                    target = max(-1.0, min(1.0, target))
                    cur = st["emotion"][k]
                    # 平滑：向目标移动，单次最多 ±_EMO_MAX_DELTA
                    delta = max(-_EMO_MAX_DELTA, min(_EMO_MAX_DELTA, target - cur))
                    st["emotion"][k] = round(cur + delta, 3)
            st["energy"] = max(0.0, min(1.0, round(st["energy"] + energy_delta, 3)))
            st["intimacy"] = max(0.0, min(1.0, round(st["intimacy"] + intimacy_delta, 3)))
            st["last_update"] = now_iso
            self._save(st)
            return st

    def reset(self) -> bool:
        with self._lock:
            st = dict(DEFAULT_STATE)
            st["last_update"] = _iso()
            return self._save(st)


# ---------------------------------------------------------------- 情绪 → 文案 --------
def emotion_to_words(emotion: dict) -> str:
    """把情绪数值转成自然语气描述。"""
    v, a = float(emotion.get("valence", 0)), float(emotion.get("arousal", 0))
    if v >= 0.6:
        return "开心、兴奋" if a >= 0.5 else "心情很好、挺开心的"
    if v >= 0.25:
        return "有点小开心" if a >= 0.5 else "平静中带点开心"
    if v > -0.25:
        return "情绪有点上头" if a >= 0.6 else "平静、稳定"
    if v > -0.6:
        return "有点低落" if a <= 0.3 else "心里不太舒服"
    return "很低落、不太想说话" if a <= 0.3 else "烦躁、情绪差"


def energy_to_words(energy: float) -> str:
    e = float(energy)
    if e >= 0.8:
        return "精力充沛"
    if e >= 0.5:
        return "状态还行"
    if e >= 0.3:
        return "有点累"
    return "很疲惫"


def intimacy_to_words(intimacy: float) -> str:
    i = float(intimacy)
    if i >= 0.8:
        return "你们已经是彼此最重要的人"
    if i >= 0.5:
        return "你们已经很亲密，无话不谈"
    if i >= 0.25:
        return "你们越来越熟，已经能开玩笑了"
    return "你们刚认识不久，还在互相熟悉"


# ---------------------------------------------------------------- 时间层 --------
def build_time_context(now: datetime | None = None) -> str:
    """生成时间层上下文：星期几 + 时段 + 日程。"""
    now = now or _now()
    weekday, day_label, schedule = _WEEKDAY_SCHEDULE[now.weekday()]
    hour = now.hour
    if hour < 6:
        period = "凌晨"
    elif hour < 9:
        period = "早上"
    elif hour < 12:
        period = "上午"
    elif hour < 14:
        period = "中午"
    elif hour < 18:
        period = "下午"
    elif hour < 22:
        period = "晚上"
    else:
        period = "深夜"
    lines = [
        f"现在是 {now.strftime('%Y-%m-%d')} {weekday} {period} {now.strftime('%H:%M')}。",
        f"今天{schedule}。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 位置层 --------
def build_location_context(location_text: str | None = None) -> str:
    """生成位置层上下文：用户当前所在位置（系统提供）。

    location_text 为空串/None 时返回空串，即不注入任何位置信息。
    文案刻意用中性表述、不带称呼：不同角色对用户称呼不同（老公/朋友/老铁…），
    位置属于全局客观事实，把称呼留给 persona 自行处理，避免写死在全局层污染其他角色。
    """
    location_text = (location_text or "").strip()
    if not location_text:
        return ""
    return f"对方（用户）当前所在位置：{location_text}。"


# ---------------------------------------------------------------- 天气层 --------
def build_weather_context(weather_text: str | None = None) -> str:
    """生成天气层上下文：用户所在位置的实时天气（系统查询，非模型编造）。

    weather_text 为空串/None 时返回空串，即不注入。中性表述、不带称呼，与位置层同理。
    """
    weather_text = (weather_text or "").strip()
    if not weather_text:
        return ""
    return f"对方（用户）当前所在位置的天气：{weather_text}。"


# ---------------------------------------------------------------- 自况层 --------
def build_self_context(location: str | None = None, recent: str | None = None) -> str:
    """生成自况层上下文：角色自己此刻的位置与最近在忙的事（第一视角）。

    location/recent 均为空时返回空串（不注入）。这里的「你」指角色自己，
    与位置层（描述用户）方向相反：自况层告诉角色「你在哪、在忙什么」。
    """
    loc = (location or "").strip()
    rec = (recent or "").strip()
    parts = []
    if loc:
        parts.append(f"你此刻在{loc}")
    if rec:
        parts.append(f"最近在忙：{rec}")
    if not parts:
        return ""
    return "。".join(parts) + "。"


# ---------------------------------------------------------------- 现实动态层 --------
def build_news_context(news_text: str | None = None) -> str:
    """生成现实动态层上下文：角色在现实世界里最近的真实动态（联网搜索所得）。

    news_text 为空串/None 时返回空串（不注入）。与自况层（静态配置）互补：
    自况是「系统设定的此刻状态」，现实动态是「搜索到的最近真实消息」。"""
    news_text = (news_text or "").strip()
    if not news_text:
        return ""
    return f"你在现实世界最近的真实动态：{news_text}。"


# ---------------------------------------------------------------- 组装上下文 --------
def build_context(memory_store: MemoryStore, state_store: StateStore, query: str,
                  top_k: int = _MEMORY_TOP_K_DEFAULT, location: str | None = None,
                  weather: str | None = None, self_location: str | None = None,
                  self_recent: str | None = None, news: str | None = None) -> dict:
    """组装上下文，返回 dict：
    { "time": str, "state": str, "memories": [str, ...], "location": str,
      "weather": str, "self": str, "news": str }
    location/weather：用户位置/天气（调用方提供，空串不注入）。
    self_location/self_recent：角色自己的位置/最近动态（第一视角，空串不注入）。
    news：角色现实世界最近真实动态（联网搜索所得，空串不注入）。
    任何一层失败都返回空文案，绝不抛异常。
    """
    ctx = {"time": "", "state": "", "memories": [], "location": "",
           "weather": "", "self": "", "news": ""}
    try:
        ctx["time"] = build_time_context()
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_time_context failed: %s", exc)
    try:
        ctx["self"] = build_self_context(self_location, self_recent)
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_self_context failed: %s", exc)
    try:
        ctx["news"] = build_news_context(news)
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_news_context failed: %s", exc)
    try:
        ctx["location"] = build_location_context(location)
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_location_context failed: %s", exc)
    try:
        ctx["weather"] = build_weather_context(weather)
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_weather_context failed: %s", exc)
    try:
        st = state_store.get_decayed()
        emo = st["emotion"]
        ctx["state"] = (
            f"你现在的心情：{emotion_to_words(emo)}。"
            f"精力：{energy_to_words(st['energy'])}。"
            f"你们的关系：{intimacy_to_words(st['intimacy'])}。"
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("build_state_context failed: %s", exc)
    try:
        mems = memory_store.search(query, top_k=top_k)
        ctx["memories"] = [m["text"] for m in mems]
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory search failed: %s", exc)
    return ctx


def format_context_block(ctx: dict) -> str:
    """把三层上下文格式化成可拼进 system prompt 的文本块（空则返回空串）。"""
    parts = []
    if ctx.get("time"):
        parts.append(f"【此刻上下文】\n{ctx['time']}")
    if ctx.get("self"):
        parts.append(f"【你此刻在哪里、在忙什么】\n{ctx['self']}")
    if ctx.get("news"):
        parts.append(f"【你的现实动态】\n{ctx['news']}")
    if ctx.get("location"):
        parts.append(f"【用户位置】\n{ctx['location']}")
    if ctx.get("weather"):
        parts.append(f"【今日天气】\n{ctx['weather']}")
    if ctx.get("state"):
        parts.append(f"【你的状态】\n{ctx['state']}")
    if ctx.get("memories"):
        mem_lines = "\n".join(f"- {t}" for t in ctx["memories"])
        parts.append(
            f"【你能想起的事】\n{mem_lines}\n"
            "（上面是你能想起来的事，不要逐条复述，自然带出即可；如果与当前话题无关就不用提）"
        )
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------- 后处理 --------
class PostProcessor:
    """对话后处理：单次轻量 LLM 调用抽取 {情绪变化, 新事实, 精力变化}。
    由调用方保证异步、不阻塞主链路；任何失败静默跳过。"""

    SYSTEM = (
        "你是对话状态标注器。根据用户消息和角色回复，输出情绪和新事实标注。\n"
        "只输出一行 JSON，不要任何思考过程和解释：\n"
        '{"emotion":{"valence":0.0,"arousal":0.0},"energy_delta":0.0,"memories":[]}\n'
        "字段说明：valence(-1难过~1开心)、arousal(0平静~1激动)、energy_delta(-0.1~0.1)、"
        "memories 只记用户身上稳定重要的事实（用户喜好/约定/经历），以\"用户\"为主语；"
        "禁止把角色自己说过的回复原文、角色扮演台词或对话寒暄存成记忆，没有就空数组。\n"
        "已有记忆里存在的信息不要重复输出。"
    )

    PROMPT_TPL = (
        "已有记忆：\n{existing}\n\n"
        "用户消息：{user}\n角色回复：{reply}\n当前情绪：valence={valence},arousal={arousal}\n\n"
        "输出JSON："
    )

    def __init__(self, llm_chat_callable, temperature: float = 0.3, max_tokens: int = 300):
        self._llm = llm_chat_callable
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def run(self, user_msg: str, reply: str, emotion: dict,
                  existing_memories: list[str] | None = None) -> dict | None:
        """返回解析后的标注 dict；失败返回 None。

        existing_memories：已有记忆文本列表，喂给标注器避免重复记忆入库。"""
        try:
            existing = existing_memories or []
            existing_block = "\n".join(f"- {t}" for t in existing[:8]) or "（暂无）"
            prompt = self.PROMPT_TPL.format(
                user=(user_msg or "")[:600],
                reply=(reply or "")[:800],
                valence=emotion.get("valence", 0),
                arousal=emotion.get("arousal", 0),
                existing=existing_block,
            )
            raw = await self._llm([
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": prompt},
            ])
            return self._parse(raw)
        except Exception as exc:  # noqa: BLE001
            # warning 级：持续失败（key 失效/超时）时默认可日志可见，避免引擎静默坏死
            _log.warning("post_process skipped: %s", exc)
            return None

    @staticmethod
    def _iter_json_objects_reverse(raw: str):
        """从右往左做花括号配平扫描，依次产出文本中每个完整 JSON 对象子串
        （最后一个对象最先产出）。比正则可靠：嵌套 JSON 用 [^{}] 匹配不到，
        贪婪 \\{.*\\} 又会在输出含多组花括号时抓错范围。"""
        depth = 0
        end = -1
        for i in range(len(raw) - 1, -1, -1):
            ch = raw[i]
            if ch == "}":
                if depth == 0:
                    end = i
                depth += 1
            elif ch == "{":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and end >= 0:
                        yield raw[i:end + 1]
                        end = -1

    @staticmethod
    def _parse(raw: str) -> dict | None:
        if not raw:
            return None
        # 兼容模型输出带 ```json 围栏 / 前后说明文字 / reasoning 模式：
        # 从后往前逐个尝试完整 JSON 对象，优先采用含标注字段的那个
        # （reasoning 模型可能先输出示例 JSON，再输出最终答案，最后出现的优先）
        data: dict | None = None
        fallback: dict | None = None
        for candidate in PostProcessor._iter_json_objects_reverse(raw):
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if fallback is None:
                fallback = obj
            if any(k in obj for k in ("emotion", "memories", "energy_delta")):
                data = obj
                break
        if data is None:
            data = fallback
        if not data:
            return None
        emo = data.get("emotion")
        if not isinstance(emo, dict):
            emo = {}
        try:
            valence = float(emo.get("valence", 0))
            arousal = float(emo.get("arousal", 0))
            energy_delta = float(data.get("energy_delta", 0))
        except (TypeError, ValueError):
            valence, arousal, energy_delta = 0.0, 0.0, 0.0
        # 精力增量限速：与情绪平滑同量级，防 LLM 输出大数值瞬间打满/清零
        energy_delta = max(-_EMO_MAX_DELTA, min(_EMO_MAX_DELTA, energy_delta))
        # memories 必须是 list：LLM 输出成字符串/dict 时逐字符迭代会产生单字垃圾记忆
        mems = data.get("memories")
        if not isinstance(mems, list):
            mems = []
        return {
            "emotion": {"valence": valence, "arousal": arousal},
            "energy_delta": energy_delta,
            "memories": [t for t in mems if isinstance(t, str) and t.strip()][:5],
        }
