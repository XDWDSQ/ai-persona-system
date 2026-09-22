# -*- coding: utf-8 -*-
"""第八轮后端加固回归（离线，不发真实云端请求）。

运行：python test_backend_hardening.py
覆盖（每条都含「旧行为必失败」的断言）：
  1. fingerprint 冷启动：进程缓存为空时首次轮询不 500（旧实现缺 request 参数必抛 TypeError）
  2. GET /api/sessions 中文不再 ASCII 转义（未压缩体积直接减半）
  3. load_config 的 stat 短 TTL：50 次连续读取只发 1 次 stat
  4. /api/status 脱敏缓存不污染配置真值，且二次请求复用缓存
  5. /api/health 新增字段（磁盘/在飞合成/订阅者/分供应商用量）
  6. OUTPUT_DIR 孤儿回收：超龄 tts_* 音频删除，新文件/非本服务文件保留
  7. role_news_missing.log 轮转：超 1MB 按整行保留尾部，新行仍为合法 JSON
  8. LLM 用量落盘恢复 + 按供应商分桶：模拟重启后累计不清零
  9. TTS singleflight 在飞上限：达到上限返回 503 而不是无限排队
 10. SSE 同步订阅上限：达到上限返回 503
 11. 安全头与请求 ID：Permissions-Policy 等安全头存在；X-Request-Id 合法回显、非法重生
"""
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from pathlib import Path as _P

sys.path.insert(0, str(Path(__file__).resolve().parent))
# 直跑本文件（python test_backend_hardening.py，不经过 run_tests.bat/conftest）
# 时也保证 TestClient 不起外部后台任务、不烧云端 token
os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")

import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


class sandbox:
    """会话存储 + config + 各类缓存 + 免密门禁全部就地替换，不碰 data/ 真数据。"""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hardening_"))
        self._sess_path = server.SESSIONS_PATH
        self._cfg_path = server.CONFIG_PATH
        self._tok = server._access_token
        self._sess_cache = copy.deepcopy(server._sess_cache)
        self.path = self.tmp / "sessions.json"
        server.SESSIONS_PATH = self.path
        server.CONFIG_PATH = self.tmp / "config.json"
        server.CONFIG_PATH.write_text(json.dumps({
            "provider": "local",
            "local": {"base_url": "http://localhost:11434/v1", "model": "Qwen3.5-4B-Q4_K_M"},
            "cloud": {"base_url": "", "model": "", "api_key": ""},
            "voice": {"provider": "local"},
            "roles": {},
        }, ensure_ascii=False), encoding="utf-8")
        server._access_token = lambda: None
        server._sess_cache.clear()
        server._sess_cache.update(_mtime_ns=0, _value={"sessions": [], "deleted": []})
        server._cfg_cache.update(_mtime_ns=0, _value={})
        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        server._status_parts_cache.update(key=None, value=None)
        server._env_override_cache.update(key=None, value=None)
        return self

    def __exit__(self, *exc):
        server.SESSIONS_PATH = self._sess_path
        server.CONFIG_PATH = self._cfg_path
        server._access_token = self._tok
        server._sess_cache.clear()
        server._sess_cache.update(self._sess_cache)
        server._cfg_cache.update(_mtime_ns=0, _value={})
        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        server._status_parts_cache.update(key=None, value=None)
        server._env_override_cache.update(key=None, value=None)
        return False


def _sess(i: int, content: str = "你好") -> dict:
    return {"id": f"s{i}", "title": f"会话{i}", "updatedAt": 1700000000000 + i * 1000,
            "history": [{"role": "user", "content": content}]}


# ------------------------------------------------------------ 1. fingerprint 冷启动
def test_fingerprint_cold_start(client):
    with sandbox() as sb:
        sb.path.write_text(json.dumps({"sessions": [], "deleted": []}), encoding="utf-8")
        # 关键前置：服务进程刚启动的真实形态——缓存为空、只有磁盘文件
        server._sess_cache.clear()
        r = client.get("/api/sessions/fingerprint")
        check("冷启动 fingerprint 返回 200（旧实现缺 request 参数必 500）",
              r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
        fp_cold = r.json().get("fp", "")
        r_full = client.get("/api/sessions", headers={"Accept-Encoding": "identity"})
        fp_full = r_full.json().get("fp", "")
        check("冷启动指纹与全量拉取一致", bool(fp_cold) and fp_cold == fp_full,
              f"cold={fp_cold!r} full={fp_full!r}")


# ------------------------------------------------------------ 2. 中文不转义
def test_sessions_chinese_not_escaped(client):
    with sandbox():
        marker = "熊猫火箭咖啡你好世界" * 3
        client.put("/api/sessions", json={"sessions": [_sess(0, marker)], "deleted": []})
        # identity 显式跳过 gzip，直接看服务端原始 body 字节
        r = client.get("/api/sessions", headers={"Accept-Encoding": "identity"})
        check("中文会话 GET 返回 200", r.status_code == 200, f"status={r.status_code}")
        raw = r.content
        check("原始 body 为 UTF-8 中文（未 \\uXXXX 转义）",
              marker.encode("utf-8") in raw and b"\\u" not in raw,
              "中文未按原文输出")
        check("响应体仍可正常 JSON 解析且内容不丢",
              r.json()["sessions"][0]["history"][0]["content"] == marker)


# ------------------------------------------------------------ 3. stat 短 TTL
def test_config_stat_ttl():
    with sandbox() as sb:
        # 先正常读一次建立缓存基底
        server.load_config()
        orig_stat = _P.stat
        counts = {"n": 0}

        def counting_stat(self, *a, **k):
            try:
                if self == server.CONFIG_PATH:
                    counts["n"] += 1
            except Exception:
                pass
            return orig_stat(self, *a, **k)

        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        _P.stat = counting_stat
        try:
            for _ in range(50):
                server.load_config()
        finally:
            _P.stat = orig_stat
        check("50 次 load_config 只发 1 次 stat（250ms TTL 命中）",
              counts["n"] <= 1, f"stat 次数={counts['n']}")


# ------------------------------------------------------------ 4. status 脱敏缓存
def test_status_mask_cache(client):
    with sandbox() as sb:
        secret = "sk-secret-abcdef123456"
        cfg = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["cloud"]["api_key"] = secret
        cfg["cloud"]["provider"] = "custom"
        server.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        server._cfg_cache.update(_mtime_ns=0, _value={})
        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        server._status_parts_cache.update(key=None, value=None)

        r1 = client.get("/api/status")
        check("status 200", r1.status_code == 200, r1.text[:200])
        out_key = r1.json()["cloud"].get("api_key", "")
        check("响应密钥已脱敏", out_key.startswith("***") and secret not in out_key, repr(out_key))
        # 第二次命中缓存：结果一致
        r2 = client.get("/api/status")
        check("二次请求脱敏结果一致（缓存命中）",
              r2.json()["cloud"].get("api_key") == out_key)
        # 缓存的是 deepcopy 后的脱敏成品：磁盘配置真值毫发无损
        disk_cfg = server.load_config(with_env=False)
        check("脱敏未污染配置缓存真值", disk_cfg["cloud"]["api_key"] == secret,
              repr(disk_cfg["cloud"].get("api_key")))

        # 走真实保存路径后新密钥必须立即反映（save 更新 mtime -> 缓存键变化），
        # 不能继续返回旧缓存里的掩码
        new_key = "sk-brand-new-zz9999"
        rw = client.post("/api/config", json={"cloud_api_key": new_key})
        check("POST /api/config 保存新密钥成功", rw.status_code == 200, rw.text[:200])
        r3 = client.get("/api/status")
        out3 = r3.json()["cloud"].get("api_key", "")
        check("保存后 status 立即反映新密钥（缓存键随 mtime 失效）",
              out3.endswith("9999") and not out3.endswith("23456") and new_key not in out3,
              repr(out3))
        check("保存后磁盘真值为新密钥",
              server.load_config(with_env=False)["cloud"]["api_key"] == new_key)


# ------------------------------------------------------------ 5. health 新字段
def test_health_fields(client):
    with sandbox():
        r = client.get("/api/health")
        check("health 200", r.status_code == 200, r.text[:200])
        d = r.json()
        check("磁盘余量字段", isinstance(d.get("disk"), dict) and d["disk"].get("free_gb", -1) >= 0,
              str(d.get("disk")))
        check("在飞合成/订阅者计数字段",
              isinstance(d.get("tts_inflight"), int) and isinstance(d.get("sync_subscribers"), int))
        check("LLM 用量 totals 四字段",
              all(k in d.get("llm_usage", {}) for k in
                  ("calls", "prompt_tokens", "completion_tokens", "total_tokens")))
        check("LLM 分供应商用量与起始时间字段",
              isinstance(d.get("llm_usage_by_provider"), dict)
              and isinstance(d.get("llm_usage_since"), (int, float)))


# ------------------------------------------------------------ 6. outputs 孤儿清理
def test_outputs_orphan_cleanup():
    orig_dir = server.OUTPUT_DIR
    td = Path(tempfile.mkdtemp(prefix="outputs_orphan_"))
    server.OUTPUT_DIR = td
    now = time.time()
    try:
        old_wav = td / "tts_aaaaaaaa.wav"
        old_mp3 = td / "tts_bbbbbbbb.mp3"
        new_wav = td / "tts_cccccccc.wav"
        keep_txt = td / "readme.txt"
        for p in (old_wav, old_mp3, new_wav, keep_txt):
            p.write_bytes(b"x" * 128)
        old_ts = now - 7 * 3600
        os.utime(old_wav, (old_ts, old_ts))
        os.utime(old_mp3, (old_ts, old_ts))
        os.utime(keep_txt, (old_ts, old_ts))
        # new_wav 保持当前 mtime

        removed = server._cleanup_orphan_outputs()
        check("超龄 wav/mp3 孤儿被删（2 个）",
              removed == 2 and not old_wav.exists() and not old_mp3.exists(),
              f"removed={removed}")
        check("6h 宽限内的新音频保留", new_wav.exists())
        check("非 tts_ 前缀文件绝不删", keep_txt.exists())
    finally:
        server.OUTPUT_DIR = orig_dir


# ------------------------------------------------------------ 7. 缺失日志轮转
def test_missing_log_rotation():
    orig_log = server._ROLE_NEWS_LOG_FILE
    log_path = Path(tempfile.mkdtemp(prefix="news_log_")) / "role_news_missing.log"
    server._ROLE_NEWS_LOG_FILE = log_path
    try:
        # 造超过 1MB 的旧内容：每行恰好 100 个 x + 换行
        line = b"x" * 100 + b"\n"
        log_path.write_bytes(line * 11000)  # ~1.08MB
        size_before = log_path.stat().st_size
        check("前置旧日志已超 1MB", size_before > server._ROLE_NEWS_LOG_MAX_BYTES,
              f"{size_before}")
        server._log_role_news_missing("轮转测试原因XYZ")
        size_after = log_path.stat().st_size
        check("轮转后体积收敛到保留窗口 + 一条新行",
              size_after <= server._ROLE_NEWS_LOG_KEEP_BYTES + 4096,
              f"{size_after}")
        lines = log_path.read_text(encoding="utf-8").splitlines()
        check("轮转按整行截断（首行长度完整）",
              lines and len(lines[0]) == 100, f"首行长度={len(lines[0]) if lines else -1}")
        last = json.loads(lines[-1])
        check("轮转后追加的新行仍是合法 JSON 且内容完整",
              last.get("reason") == "轮转测试原因XYZ", lines[-1][:120])
    finally:
        server._ROLE_NEWS_LOG_FILE = orig_log


# ------------------------------------------------------------ 8. 用量持久化
def test_llm_usage_persistence():
    orig_path = server._LLM_USAGE_PATH
    td = Path(tempfile.mkdtemp(prefix="llm_usage_"))
    server._LLM_USAGE_PATH = td / "llm_usage.json"
    totals = dict(server._llm_usage_total)
    baseline = dict(server._llm_usage_baseline)
    by_prov = dict(server._llm_usage_by_provider)
    base_prov = dict(server._llm_usage_baseline_providers)
    dirty = server._llm_usage_dirty_calls
    last_flush = server._llm_usage_last_flush
    try:
        server._llm_usage_total.clear()
        server._llm_usage_total.update(dict(server._LLM_USAGE_ZERO))
        server._llm_usage_baseline.clear()
        server._llm_usage_baseline.update(dict(server._LLM_USAGE_ZERO))
        server._llm_usage_by_provider.clear()
        server._llm_usage_baseline_providers.clear()
        server._llm_usage_dirty_calls = 0
        # 模拟进程刚启动（last_flush=now）：让落盘由「满 50 次」计数阈值触发，
        # 而非首次调用的时间阈值（生产中冷启动首次调用即落盘是刻意的尽早建基线）
        server._llm_usage_last_flush = time.time()

        # 50 次成功调用：达节流阈值落盘（直跑无事件循环 -> 同步写）
        for _ in range(50):
            server._record_llm_usage("testprov", "m1",
                                     {"prompt_tokens": 10, "completion_tokens": 20,
                                      "total_tokens": 30})
        check("用量文件已落盘", server._LLM_USAGE_PATH.exists())
        on_disk = json.loads(server._LLM_USAGE_PATH.read_text(encoding="utf-8"))
        check("落盘 totals 正确（50 调用 / 1500 token）",
              on_disk["totals"]["calls"] == 50 and on_disk["totals"]["total_tokens"] == 1500,
              str(on_disk["totals"]))
        check("按供应商分桶落盘",
              on_disk["providers"].get("testprov", {}).get("calls") == 50,
              str(on_disk["providers"]))

        # 模拟进程重启：清空内存增量后重新加载，历史累计必须还在
        server._llm_usage_total.clear()
        server._llm_usage_total.update(dict(server._LLM_USAGE_ZERO))
        server._llm_usage_by_provider.clear()
        server._llm_usage_baseline.clear()
        server._llm_usage_baseline.update(dict(server._LLM_USAGE_ZERO))
        server._llm_usage_baseline_providers.clear()
        server._load_llm_usage_on_startup()
        for _ in range(5):
            server._record_llm_usage("testprov", "m1",
                                     {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
        server._flush_llm_usage_now()
        snap = server._llm_usage_snapshot()
        check("重启恢复后累计不清零（55 调用 / 1515 token）",
              snap["totals"]["calls"] == 55 and snap["totals"]["total_tokens"] == 1515,
              str(snap["totals"]))
        check("分桶累计同样跨重启保留",
              snap["providers"].get("testprov", {}).get("calls") == 55,
              str(snap["providers"]))
    finally:
        server._LLM_USAGE_PATH = orig_path
        server._llm_usage_total.clear()
        server._llm_usage_total.update(totals)
        server._llm_usage_baseline.clear()
        server._llm_usage_baseline.update(baseline)
        server._llm_usage_by_provider.clear()
        server._llm_usage_by_provider.update(by_prov)
        server._llm_usage_baseline_providers.clear()
        server._llm_usage_baseline_providers.update(base_prov)
        server._llm_usage_dirty_calls = dirty
        server._llm_usage_last_flush = last_flush


# ------------------------------------------------------------ 9. TTS 在飞上限
def test_tts_inflight_limit():
    orig_max = server._TTS_INFLIGHT_MAX
    server._TTS_INFLIGHT_MAX = 1

    async def scenario() -> int:
        loop = asyncio.get_running_loop()
        server._tts_inflight["fake_other_key.wav"] = loop.create_future()
        try:
            await server.tts_synthesize("一句不会命中任何假 key 的文本", "")
            return 200
        except HTTPException as exc:
            return exc.status_code
        finally:
            server._tts_inflight.pop("fake_other_key.wav", None)

    try:
        code = asyncio.run(scenario())
        check("在飞合成达上限返回 503", code == 503, f"code={code}")
    finally:
        server._TTS_INFLIGHT_MAX = orig_max


# ------------------------------------------------------------ 10. SSE 订阅上限
def test_sync_subscriber_limit(client):
    with sandbox():
        orig_max = server._SYNC_SUBSCRIBERS_MAX
        server._SYNC_SUBSCRIBERS_MAX = 0
        try:
            r = client.get("/api/sync/stream?client=limit-test")
            check("同步订阅达上限返回 503", r.status_code == 503, f"status={r.status_code}")
        finally:
            server._SYNC_SUBSCRIBERS_MAX = orig_max


# ------------------------------------------------------------ 11. 离线模式零外部调用
def test_offline_mode_gate():
    # 直跑/run_tests.bat/conftest 三个入口都应让本进程处于离线模式
    check("测试进程处于离线模式（AI_DISABLE_EXTERNAL）", server._OFFLINE_MODE is True,
          f"_OFFLINE_MODE={server._OFFLINE_MODE}")

    async def _call():
        # force=True 也必须短路：手动刷新端点与启动预取共用同一函数
        w = await server._bg_weather_refresh(force=True)
        n = await server._bg_role_news_refresh(force=True)
        return w, n

    w, n = asyncio.run(_call())
    check("离线模式下天气/角色动态刷新全部短路返回空串", w == "" and n == "",
          f"weather={w!r} news={n!r}")


# ------------------------------------------------------------ 12. 安全头与请求 ID
def test_security_headers_and_request_id(client):
    with sandbox():
        r = client.get("/api/health")
        h = {k.lower(): v for k, v in r.headers.items()}
        check("Permissions-Policy 已收紧",
              "permissions-policy" in h and "geolocation=()" in h["permissions-policy"],
              h.get("permissions-policy", ""))
        check("nosniff / Referrer-Policy / X-Frame-Options 存在",
              h.get("x-content-type-options") == "nosniff"
              and "x-frame-options" in h and "referrer-policy" in h)
        rid_auto = h.get("x-request-id", "")
        check("响应自动携带 X-Request-Id", bool(rid_auto), rid_auto)

        r2 = client.get("/api/health", headers={"X-Request-Id": "my-trace-123"})
        check("合法上游 X-Request-Id 原样回显",
              r2.headers.get("x-request-id") == "my-trace-123",
              r2.headers.get("x-request-id", ""))

        r3 = client.get("/api/health", headers={"X-Request-Id": "bad id with spaces!!"})
        rid3 = r3.headers.get("x-request-id", "")
        check("非法 X-Request-Id 被重生（防日志注入）",
              bool(rid3) and rid3 != "bad id with spaces!!", rid3)


def main():
    from fastapi.testclient import TestClient  # noqa: E402
    with TestClient(server.app) as client:
        test_fingerprint_cold_start(client)
        test_sessions_chinese_not_escaped(client)
        test_config_stat_ttl()
        test_status_mask_cache(client)
        test_health_fields(client)
        test_outputs_orphan_cleanup()
        test_missing_log_rotation()
        test_llm_usage_persistence()
        test_tts_inflight_limit()
        test_sync_subscriber_limit(client)
        test_offline_mode_gate()
        test_security_headers_and_request_id(client)
    print(f"\n{'=' * 50}\n第八轮后端加固测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
