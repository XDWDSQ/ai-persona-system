# -*- coding: utf-8 -*-
"""TTS 缓存机制离线测试（mock 外部合成，不发真实请求）。

运行：python test_tts_cache.py
覆盖：
  - 缓存指纹失效：换音色 / 换 style / 换文本得到不同缓存路径，同输入同路径
  - singleflight 并发复用（含 force=True 合并）
  - LRU 扫描排序按 max(atime, mtime) 从旧到新，mtime 新 atime 旧不误删
  - tts_cache 配置节阈值生效与缺省回退（500 文件 / 8GB / 3600s）
隔离：TTS_CACHE_DIR / CONFIG_PATH 重定向到临时目录，不触碰真实 data/ 与 config.json。
"""
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 指纹失效 --------
def test_cache_fingerprint():
    server._fp_cache.clear()
    base = {"active_role": "", "voice": {"provider": "local", "language": "Chinese"}}
    p1 = server.tts_cache_path("你好", "自然", base)
    p2 = server.tts_cache_path("你好", "自然", base)
    check("同输入同路径", p1 == p2, f"{p1} vs {p2}")
    p3 = server.tts_cache_path("你好", "激动", base)
    check("换 style 得到不同缓存路径", p3 != p1, f"{p3} vs {p1}")
    p4 = server.tts_cache_path("你好呀", "自然", base)
    check("换文本得到不同缓存路径", p4 != p1, f"{p4} vs {p1}")
    cfg_ali = copy.deepcopy(base)
    cfg_ali["voice"] = {"provider": "aliyun",
                        "aliyun": {"model": "qwen3-tts-flash", "voice": "Cherry"}}
    p5 = server.tts_cache_path("你好", "自然", cfg_ali)
    check("换音色 provider 后缓存失效", p5 != p1, f"{p5} vs {p1}")
    cfg_ali2 = copy.deepcopy(cfg_ali)
    cfg_ali2["voice"]["aliyun"]["voice"] = "Ethan"
    p6 = server.tts_cache_path("你好", "自然", cfg_ali2)
    check("阿里云音色 ID 变化后缓存失效", p6 != p5, f"{p6} vs {p5}")


# ---------------------------------------------------------------- singleflight --------
def test_singleflight():
    orig_do = server._tts_do_synthesize
    orig_cleanup = server._maybe_cleanup_tts_cache
    orig_dir = server.TTS_CACHE_DIR
    td = Path(tempfile.mkdtemp(prefix="tts_sf_test_"))
    server.TTS_CACHE_DIR = td
    calls: list[str] = []

    async def fake_do(text, style, cfg, cache):
        calls.append(text)
        await asyncio.sleep(0.3)  # 制造并发窗口
        cache.write_bytes(b"RIFF\x00\x00\x00\x00")
        return cache

    async def noop_cleanup(force=False):
        return None

    server._tts_do_synthesize = fake_do
    server._maybe_cleanup_tts_cache = noop_cleanup
    try:
        async def run_five():
            return await asyncio.gather(*[
                server.tts_synthesize("同一句话", "", 1.0) for _ in range(5)
            ])

        results = asyncio.run(run_five())
        check("并发 5 路同文本只合成 1 次", len(calls) == 1, f"calls={len(calls)}")
        check("5 路结果指向同一缓存路径", len(set(results)) == 1, str(results))

        again = asyncio.run(server.tts_synthesize("同一句话", "", 1.0))
        check("二次请求命中磁盘缓存不再合成", len(calls) == 1 and again == results[0],
              f"calls={len(calls)} again={again}")

        calls.clear()

        async def run_force_mix():
            return await asyncio.gather(
                server.tts_synthesize("另一句话", "", 1.0, force=True),
                *[server.tts_synthesize("另一句话", "", 1.0) for _ in range(4)],
            )

        asyncio.run(run_force_mix())
        check("force=True 在飞期间与普通请求合并，仅合成 1 次", len(calls) == 1,
              f"calls={len(calls)}")
        check("singleflight 字典收尾清空", not server._tts_inflight, str(server._tts_inflight))
    finally:
        server._tts_do_synthesize = orig_do
        server._maybe_cleanup_tts_cache = orig_cleanup
        server.TTS_CACHE_DIR = orig_dir


# ---------------------------------------------------------------- LRU 排序与淘汰 --------
def test_lru_order_and_eviction():
    orig_dir = server.TTS_CACHE_DIR
    td = Path(tempfile.mkdtemp(prefix="tts_lru_test_"))
    server.TTS_CACHE_DIR = td
    now = time.time()
    t_old, t_mid, t_c, t_new = now - 86400, now - 3600, now - 60, now

    def mk(name: str, at: float, mt: float) -> Path:
        p = td / name
        p.write_bytes(b"x" * 100)
        os.utime(p, (at, mt))
        return p

    a = mk("a_both_old.wav", t_old, t_old)   # 最旧：atime/mtime 都旧
    d = mk("d_mid.wav", t_mid, t_mid)        # 次旧
    c = mk("c_mtime_new.wav", t_old, t_c)    # atime 旧但 mtime 较新
    b = mk("b_atime_new.wav", t_new, t_old)  # mtime 旧但 atime 新（刚被访问）

    orig_limits = (server._TTS_CACHE_MAX_FILES, server._TTS_CACHE_MAX_BYTES,
                   server._TTS_CACHE_CLEAN_INTERVAL)
    try:
        entries, total = server._scan_tts_cache()
        check("扫描收集全部 wav 文件", len(entries) == 4, f"entries={len(entries)}")
        check("扫描聚合总字节数", total == 400, f"total={total}")
        # 扫描本身不排序（排序在清理函数里），这里验证排序键 max(atime, mtime) 语义
        order = [p.name for _k, _s, p in sorted(entries, key=lambda x: x[0])]
        check("按 max(atime, mtime) 从旧到新排序",
              order == [a.name, d.name, c.name, b.name], str(order))
        keys = {p.name: k for k, _s, p in entries}
        check("mtime 旧但 atime 新（刚访问过）不算最旧", keys[b.name] > keys[d.name],
              f"b={keys[b.name]} d={keys[d.name]}")
        check("atime 旧但 mtime 新不会被误判为最旧", keys[c.name] > keys[d.name],
              f"c={keys[c.name]} d={keys[d.name]}")

        server._TTS_CACHE_MAX_FILES = 2
        server._TTS_CACHE_MAX_BYTES = 8 * 1024 * 1024 * 1024
        server._TTS_CACHE_CLEAN_INTERVAL = 3600.0
        asyncio.run(server._maybe_cleanup_tts_cache(force=True))
        check("最旧文件被淘汰", not a.exists() and not d.exists(),
              f"a={a.exists()} d={d.exists()}")
        check("mtime 新 atime 旧的文件未被优先删除", c.exists(), str(c))
        check("atime 新 mtime 旧的文件（刚访问过）保留", b.exists(), str(b))
        remain = sorted(p.name for p in td.iterdir())
        check("淘汰后文件数等于阈值", len(remain) == 2, str(remain))
    finally:
        (server._TTS_CACHE_MAX_FILES, server._TTS_CACHE_MAX_BYTES,
         server._TTS_CACHE_CLEAN_INTERVAL) = orig_limits
        server.TTS_CACHE_DIR = orig_dir


# ---------------------------------------------------------------- 阈值配置 --------
def test_cache_limits_from_config():
    orig_cfg_path = server.CONFIG_PATH
    orig_limits = (server._TTS_CACHE_MAX_FILES, server._TTS_CACHE_MAX_BYTES,
                   server._TTS_CACHE_CLEAN_INTERVAL)
    td = Path(tempfile.mkdtemp(prefix="tts_cfg_test_"))
    cfg_path = td / "config.json"
    server.CONFIG_PATH = cfg_path
    try:
        cfg_path.write_text(json.dumps(
            {"tts_cache": {"max_files": 12, "max_bytes": 3456, "clean_interval": 77}}),
            encoding="utf-8")
        server._cfg_cache["_mtime_ns"] = 0
        server._refresh_tts_cache_limits()
        check("tts_cache 配置节覆盖阈值",
              server._TTS_CACHE_MAX_FILES == 12 and server._TTS_CACHE_MAX_BYTES == 3456
              and server._TTS_CACHE_CLEAN_INTERVAL == 77.0,
              f"{server._TTS_CACHE_MAX_FILES}/{server._TTS_CACHE_MAX_BYTES}/"
              f"{server._TTS_CACHE_CLEAN_INTERVAL}")

        # 缺省时回退默认 500 / 8GB / 3600
        server._TTS_CACHE_MAX_FILES = 500
        server._TTS_CACHE_MAX_BYTES = 8 * 1024 * 1024 * 1024
        server._TTS_CACHE_CLEAN_INTERVAL = 3600.0
        cfg_path.write_text(json.dumps({}), encoding="utf-8")
        server._cfg_cache["_mtime_ns"] = 0
        server._refresh_tts_cache_limits()
        check("缺省回退 500 文件 / 8GB / 3600s",
              server._TTS_CACHE_MAX_FILES == 500
              and server._TTS_CACHE_MAX_BYTES == 8 * 1024 * 1024 * 1024
              and server._TTS_CACHE_CLEAN_INTERVAL == 3600.0,
              f"{server._TTS_CACHE_MAX_FILES}/{server._TTS_CACHE_MAX_BYTES}/"
              f"{server._TTS_CACHE_CLEAN_INTERVAL}")

        # 非法值（负数）不生效，保留当前值
        cfg_path.write_text(json.dumps(
            {"tts_cache": {"max_files": -5, "max_bytes": -1, "clean_interval": 0}}),
            encoding="utf-8")
        server._cfg_cache["_mtime_ns"] = 0
        server._refresh_tts_cache_limits()
        check("非法值不覆盖阈值",
              server._TTS_CACHE_MAX_FILES == 500
              and server._TTS_CACHE_MAX_BYTES == 8 * 1024 * 1024 * 1024
              and server._TTS_CACHE_CLEAN_INTERVAL == 3600.0,
              f"{server._TTS_CACHE_MAX_FILES}/{server._TTS_CACHE_MAX_BYTES}/"
              f"{server._TTS_CACHE_CLEAN_INTERVAL}")
    finally:
        server.CONFIG_PATH = orig_cfg_path
        (server._TTS_CACHE_MAX_FILES, server._TTS_CACHE_MAX_BYTES,
         server._TTS_CACHE_CLEAN_INTERVAL) = orig_limits
        server._cfg_cache["_mtime_ns"] = 0
        server._cfg_cache["_value"] = {}


def main():
    test_cache_fingerprint()
    test_singleflight()
    test_lru_order_and_eviction()
    test_cache_limits_from_config()
    print(f"\n{'=' * 50}\nTTS 缓存测试完成，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
