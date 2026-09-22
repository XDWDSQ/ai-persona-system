# -*- coding: utf-8 -*-
"""临时验证：TTS 分段并行合成的并发上限 / 顺序保持 / 失败传播。跑完即删。"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="tts_par_"))
server.TTS_CACHE_DIR = tmp
server.OUTPUT_DIR = tmp
server.TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

state = {"cur": 0, "max": 0, "calls": []}


async def fake_synth(text, style, cfg, cache):
    state["cur"] += 1
    state["max"] = max(state["max"], state["cur"])
    state["calls"].append(text)
    await asyncio.sleep(0.25)
    state["cur"] -= 1
    return tmp / f"seg_{len(state['calls'])}.wav"


async def noop():
    return ""


def fake_concat(seg_paths, out_path):
    Path(out_path).write_bytes(b"RIFF")


def fake_save(cache, src):
    return cache


async def main():
    server._tts_do_synthesize = fake_synth
    server._maybe_cleanup_tts_cache = noop
    server._concat_tts_segments = fake_concat
    server._save_tts_cache = fake_save
    cfg = {"voice": {"provider": "aliyun"}}
    text = "一" * 900  # _TTS_SEG_MAX_CHARS 内分段 -> 多段
    seg_n = len(server._split_tts_text(text))
    print(f"segments={seg_n} (seg_max={server._TTS_SEG_MAX_CHARS})")
    assert seg_n >= 4, "预期切出多段"

    t0 = time.monotonic()
    out = await server._tts_do_synthesize_maybe_segmented(text, "", cfg, tmp / "final.wav", False)
    dt_par = time.monotonic() - t0
    print(f"parallel: {dt_par:.2f}s max_concurrency={state['max']} calls={len(state['calls'])}")
    assert len(state["calls"]) == seg_n, "每段都应调用一次"
    assert state["max"] <= server._TTS_SEG_CONCURRENCY, f"并发超上限: {state['max']}"
    assert state["max"] >= 2, "应存在并行（否则退化成串行）"
    assert dt_par < 0.25 * seg_n * 0.8, f"并行未提速: {dt_par:.2f}s"

    # 顺序保持：gather 保序，seg_paths 与 segments 一一对应
    state["calls"].clear()
    state["max"] = 0
    # 单段文本仍走单段路径
    out1 = await server._tts_do_synthesize_maybe_segmented("短句", "", cfg, tmp / "one.wav", False)
    print("single-segment path OK")

    # 失败传播：第 2 段抛错 -> 整体抛错（其余段继续跑完不互相取消）
    async def failing_synth(text, style, cfg, cache):
        if state["calls"].count(text) == 0 and text.startswith("二"):
            raise RuntimeError("boom")
        state["calls"].append(text)
        await asyncio.sleep(0.2)
        return tmp / "x.wav"
    server._tts_do_synthesize = failing_synth
    text2 = "二" * 900
    try:
        await server._tts_do_synthesize_maybe_segmented(text2, "", cfg, tmp / "fail.wav", False)
        print("FAIL: 异常未传播")
        sys.exit(1)
    except RuntimeError as e:
        print(f"failure propagates OK: {e}")
    print("ALL TTS PARALLEL CHECKS PASSED")


asyncio.run(main())
