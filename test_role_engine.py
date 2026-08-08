# -*- coding: utf-8 -*-
"""role_engine 单元测试。

运行：python test_role_engine.py
覆盖：记忆写入去重 / 检索排序 / 容量裁剪 / 情绪平滑 / 情绪衰减 / 时间上下文 / 后处理解析
"""
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import role_engine
from role_engine import (
    MemoryStore,
    StateStore,
    PostProcessor,
    build_time_context,
    format_context_block,
)

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAIL += 1
    print(f"[{mark}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def fresh_stores():
    """返回 (MemoryStore, StateStore) 指向独立临时目录。"""
    td = tempfile.mkdtemp(prefix="role_engine_test_")
    return MemoryStore(Path(td), "dashuai"), StateStore(Path(td), "dashuai")


# ---------------------------------------------------------------- 记忆 --------
def test_memory_dedup():
    mem, _ = fresh_stores()
    mem.add("老公最近在练杨玉环，说想上分", 0.8)
    mem.add("老公最近在练杨玉环，说想上分", 0.8)   # 完全重复
    mem.add("我老公最近在练杨玉环想上分", 0.5)     # 近似重复（顺序微调）
    mem.add("今天训练赛赢了，心情不错", 0.6)        # 不同内容
    n = mem.count()
    check("记忆去重：重复/近似句不新增（4 次写入 → 2 条）", n == 2, f"count={n}")
    # 命中次数应累加
    mems = mem.load()
    hit = sum(int(m.get("hit_count", 1)) for m in mems if "杨玉环" in m["text"])
    check("去重后 hit_count 累加", hit >= 2, f"hit={hit}")


def test_memory_search_ranking():
    mem, _ = fresh_stores()
    mem.add("老公喜欢吃辣的，无辣不欢", 0.3)
    mem.add("老公说喜欢玩杨玉环这个英雄", 0.9)
    mem.add("队友长生爱吃火锅", 0.7)
    mem.add("上个月我们一起去看了电影", 0.2)
    top = mem.search("老公喜欢杨玉环吗", top_k=3)
    check("检索命中：第一条与查询相关", len(top) >= 1 and ("杨玉环" in top[0]["text"] or "杨玉环" in top[0].get("text", "")), str([m["text"] for m in top]))
    check("检索排序：高度相关排最前", "杨玉环" in top[0]["text"], str([m["text"] for m in top]))


def test_search_mutual_exclusion():
    mem, _ = fresh_stores()
    mem.add("晚上约好和用户双排冲分", 0.8)
    mem.add("晚上约好和用户一起双排冲分", 0.8)   # 同义改写，应互斥
    mem.add("用户在练杨玉环", 0.8)
    top = mem.search("晚上双排", top_k=5)
    texts = [m["text"] for m in top]
    check("检索互斥：同义改写记忆只返回一条", sum("双排" in t for t in texts) == 1, str(texts))


def test_memory_cap():
    mem, _ = fresh_stores()
    for i in range(220):
        mem.add(f"测试记忆条目第{i}号，内容各不相同", 0.1 + (i % 5) * 0.01, dedup=False)
    n = mem.count()
    check("记忆裁剪：220 条写入后 ≤ 200 条", n <= 200, f"count={n}")


def test_memory_corrupt_reset():
    mem, _ = fresh_stores()
    mem.path.parent.mkdir(parents=True, exist_ok=True)
    mem.path.write_text("{ broken json !!", encoding="utf-8")
    n = mem.count()
    check("损坏文件自动重置为空库", n == 0, f"count={n}")


def test_memory_concurrent_add():
    """多线程并发写 MemoryStore：锁应保证无异常、无丢失、落盘 JSON 完整。"""
    import json
    import threading
    mem, _ = fresh_stores()
    n_threads, per_thread = 8, 25   # 共 200 条，正好不超默认上限
    errors: list[Exception] = []

    def worker(tid: int):
        try:
            for i in range(per_thread):
                mem.add(f"并发线程{tid}的第{i}条独立记忆内容", 0.5, dedup=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n = mem.count()
    check("并发写：无异常", not errors, str(errors[:1]))
    check(f"并发写：无丢失（{n_threads * per_thread} 条全在）",
          n == n_threads * per_thread, f"count={n}")
    try:
        data = json.loads(mem.path.read_text(encoding="utf-8"))
        json_ok = isinstance(data.get("memories"), list)
    except (json.JSONDecodeError, OSError):
        json_ok = False
    check("并发写：落盘 JSON 可解析", json_ok)


# ---------------------------------------------------------------- 状态 --------
def test_emotion_smooth():
    _, st = fresh_stores()
    # 直接跳到很开心的目标，单次最多移动 0.2
    st.update(emotion={"valence": 1.0, "arousal": 1.0})
    st2 = st.get_decayed()
    dv = abs(st2["emotion"]["valence"] - 0.2)   # 基线 0.2
    da = abs(st2["emotion"]["arousal"] - 0.3)   # 基线 0.3
    check("情绪平滑：单次移动 ≤ 0.2", dv <= 0.2 + 1e-6 and da <= 0.2 + 1e-6, f"dv={dv:.3f} da={da:.3f}")


def test_emotion_decay():
    _, st = fresh_stores()
    st.update(emotion={"valence": 1.0, "arousal": 1.0})
    # 模拟 5 小时前更新：手动改 last_update 并落盘
    st2 = st.get_decayed()
    st2["last_update"] = (datetime.now().astimezone() - timedelta(hours=5)).isoformat(timespec="seconds")
    # 直接通过内部落盘
    from role_engine import _atomic_write
    _atomic_write(st.path, st2)
    st3 = st.get_decayed()
    check("情绪衰减：5h 后向基线靠拢", st3["emotion"]["valence"] < st2["emotion"]["valence"] and st3["emotion"]["valence"] > 0.2,
          f"before={st2['emotion']['valence']:.3f} after={st3['emotion']['valence']:.3f}")


def test_energy_intimacy_clamp():
    _, st = fresh_stores()
    st.update(energy_delta=5.0, intimacy_delta=5.0)
    st2 = st.get_decayed()
    check("精力/亲密度封顶 1.0", st2["energy"] <= 1.0 and st2["intimacy"] <= 1.0,
          f"energy={st2['energy']} intimacy={st2['intimacy']}")


# ---------------------------------------------------------------- 时间层 --------
def test_time_context():
    mon = build_time_context(datetime(2026, 8, 3, 21, 0))   # 周一 21:00（真实为周一）
    sat = build_time_context(datetime(2026, 8, 8, 10, 0))    # 周六 10:00
    check("时间上下文：工作日含训练/复盘", "训练" in mon or "复盘" in mon, mon)
    check("时间上下文：周末含比赛/直播", "比赛" in sat or "直播" in sat, sat)
    check("时间上下文：含星期与时刻", "周一" in mon and "21:00" in mon, mon)


# ---------------------------------------------------------------- 后处理 --------
class FakeLLM:
    def __init__(self, raw):
        self.raw = raw

    async def __call__(self, messages):
        return self.raw


def test_postprocessor_parse():
    import asyncio

    async def _run():
        pp = PostProcessor(FakeLLM('```json\n{"emotion": {"valence": 0.8, "arousal": 0.6}, "energy_delta": -0.05, "memories": ["老公喜欢杨玉环"]}\n```'))
        r = await pp.run("用户消息", "回复", {"valence": 0.2, "arousal": 0.3}, existing_memories=["用户在练杨玉环"])
        check("后处理：围栏 JSON 解析", r is not None and r["emotion"]["valence"] == 0.8 and r["memories"] == ["老公喜欢杨玉环"], str(r))
        pp2 = PostProcessor(FakeLLM("这不是 JSON"))
        r2 = await pp2.run("a", "b", {})
        check("后处理：非法输出返回 None", r2 is None)

    asyncio.run(_run())


def test_postprocessor_story_parse():
    """story_result/story_flag 透传：标注器抽取的剧情字段必须原样带出，
    结构非法时安全降级为 None（不能让脏数据驱动剧情状态机）。"""
    import asyncio

    async def _run():
        pp = PostProcessor(FakeLLM(
            '{"emotion":{"valence":0.5,"arousal":0.7},"energy_delta":0.0,'
            '"memories":[],"story_result":{"win":true,"score":"3:1","mvp":"岚风"},'
            '"story_flag":"command_win"}'))
        r = await pp.run("赢了3:1", "干得漂亮", {})
        check("后处理：story_result 透传",
              r and r["story_result"] == {"win": True, "score": "3:1", "mvp": "岚风"}, str(r))
        check("后处理：story_flag 透传", r and r["story_flag"] == "command_win", str(r))

        pp2 = PostProcessor(FakeLLM(
            '{"emotion":{"valence":0,"arousal":0},"energy_delta":0,"memories":[],'
            '"story_result":{"win":"yes","score":"3:1"},"story_flag":"结婚"}'))
        r2 = await pp2.run("a", "b", {})
        check("后处理：win 非 bool 时 story_result 置 None",
              r2 is not None and r2["story_result"] is None, str(r2))
        check("后处理：未知 story_flag 置 None", r2 is not None and r2["story_flag"] is None, str(r2))

        pp3 = PostProcessor(FakeLLM(
            '{"emotion":{"valence":0,"arousal":0},"energy_delta":0,"memories":[]}'))
        r3 = await pp3.run("a", "b", {})
        check("后处理：无剧情字段时两字段为 None",
              r3 is not None and r3["story_result"] is None and r3["story_flag"] is None, str(r3))

    asyncio.run(_run())


# ---------------------------------------------------------------- 上下文组装 --------
def test_context_block():
    mem, st = fresh_stores()
    mem.add("老公说最近在练杨玉环", 0.9)
    mem.add("队友叫长生，爱吃火锅", 0.5)
    ctx = role_engine.build_context(mem, st, "杨玉环练得怎么样了", top_k=3)
    block = format_context_block(ctx)
    check("上下文块：含三层信息", "此刻上下文" in block and "你的状态" in block and "你能想起的事" in block, block[:80])
    check("上下文块：记忆命中相关条目", "杨玉环" in block, block)
    empty = format_context_block({"time": "", "state": "", "memories": []})
    check("上下文块：空层返回空串", empty == "", repr(empty))


# ---------------------------------------------------------------- main --------
def main():
    tests = [
        test_memory_dedup,
        test_memory_search_ranking,
        test_search_mutual_exclusion,
        test_memory_cap,
        test_memory_corrupt_reset,
        test_memory_concurrent_add,
        test_emotion_smooth,
        test_emotion_decay,
        test_energy_intimacy_clamp,
        test_time_context,
        test_postprocessor_parse,
        test_postprocessor_story_parse,
        test_context_block,
    ]
    for t in tests:
        t()
    print(f"\n{'='*50}\n共 {len(tests)} 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
