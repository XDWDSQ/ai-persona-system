# -*- coding: utf-8 -*-
"""_sse_with_heartbeat 行为验证（离线，不碰网络/真数据）：
  1. 静默窗口发 ": ping" 注释，事件内容不被破坏
  2. 无静默时不插心跳
  3. 底层异常原样向上抛
  4. 消费端提前关闭（GeneratorExit）→ 生产者任务被收掉
运行：venv/Scripts/python.exe test_sse_heartbeat.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

_FAIL = 0


def check(name, cond, detail=""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


async def slow_gen():
    for c in ["a", "b", "c"]:
        await asyncio.sleep(0.35)  # > interval(0.1)，必然触发心跳
        yield f"data: {c}\n\n"


async def fast_gen():
    for c in ["x", "y"]:
        yield f"data: {c}\n\n"


async def boom_gen():
    yield "data: ok\n\n"
    raise RuntimeError("boom")


async def collect(agen, limit=50):
    out = []
    async for item in agen:
        out.append(item)
        if len(out) >= limit:
            break
    return out


async def main():
    # 1) 心跳出现且事件完整
    out = await collect(server._sse_with_heartbeat(slow_gen(), interval=0.1))
    pings = [x for x in out if x.startswith(": ping")]
    events = [x for x in out if x.startswith("data: ")]
    check("静默时发出心跳注释", len(pings) >= 2, f"pings={len(pings)} out={out}")
    check("事件内容完整无破坏", events == ["data: a\n\n", "data: b\n\n", "data: c\n\n"], f"events={events}")
    check("心跳行是 SSE 注释格式", all(x == ": ping\n\n" for x in pings), f"{pings!r}")

    # 2) 无静默不插心跳
    out2 = await collect(server._sse_with_heartbeat(fast_gen(), interval=0.5))
    check("无静默时零心跳", all(not x.startswith(":") for x in out2), f"out={out2}")

    # 3) 异常透传
    raised = None
    try:
        await collect(server._sse_with_heartbeat(boom_gen(), interval=0.5))
    except RuntimeError as e:
        raised = e
    check("底层异常原样重抛", isinstance(raised, RuntimeError) and str(raised) == "boom", f"{raised!r}")

    # 4) 消费端提前关闭：aclose 后生产者任务应被取消收掉
    agen = server._sse_with_heartbeat(slow_gen(), interval=10.0)
    first = await agen.__anext__()
    check("首次迭代拿到事件", first.startswith("data: "), first)
    await agen.aclose()
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    check("提前关闭后无残留生产者任务", not pending, f"pending={[t.get_name() for t in pending]}")


if __name__ == "__main__":
    asyncio.run(main())
    print("RESULT:", "ALL PASS" if _FAIL == 0 else f"{_FAIL} FAILURES")
    sys.exit(1 if _FAIL else 0)
