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
 12. （第九轮补）provider 探测永不把 /api/status 打成 500；server 重导出公共面契约
"""
import asyncio
import copy
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from pathlib import Path as _P
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
# 直跑本文件（python test_backend_hardening.py，不经过 run_tests.bat/conftest）
# 时也保证 TestClient 不起外部后台任务、不烧云端 token
os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")

import server  # noqa: E402
import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402

# conftest 与各 sandbox 都会把 server._access_token 换成 `lambda: None`（离线用例不必
# 登录）。需要验证**真实门禁**的用例（第十轮的 fail-closed）必须拿回原函数，而它一旦被
# 覆盖就无法按名字取回 —— 所以在模块导入期（fixture 生效之前）先留一份引用。
_REAL_ACCESS_TOKEN = server._access_token

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


# ------------------------------------------------------------ 11.5 provider 探测不得 500
def test_probe_never_breaks_status():
    """第九轮回归：修前 /api/status 在「共享 httpx client 已关闭」时 500。

    真实触发路径：TestClient 退出 lifespan 会 close 全局 httpx_client，而
    probe_active_provider 仍去发 /models 探测 -> httpx 抛 RuntimeError。
    RuntimeError 不是 httpx.HTTPError，旧的 `except httpx.HTTPError` 兜不住，
    异常直接冒到路由层，GET /api/status 整个 500（真实浏览器表现为设置页转圈
    然后报「加载配置失败」）。
    """
    cfg = {"provider": "local", "local": {"base_url": "http://127.0.0.1:1/v1"}}

    # 1) 离线模式：根本不发请求，明确报「未探测」
    async def _offline():
        server._probe_cache.clear()
        return await server.probe_active_provider(cfg)
    online, err = asyncio.run(_offline())
    check("离线模式下 provider 探测短路为未在线", online is False, f"online={online} err={err!r}")
    check("离线模式下给出明确原因（未探测）", "未探测" in err, repr(err))

    # 2) 非离线 + client 已关闭：必须被兜住，返回 (False, 原因) 而不是抛异常
    async def _closed():
        server._probe_cache.clear()
        server._OFFLINE_MODE = False
        # 用同一份 AsyncClient 类型造一个"已关闭"的替身：真实崩溃点就在这里
        closed = httpx.AsyncClient()
        await closed.aclose()
        orig = server.httpx_client
        server.httpx_client = closed
        try:
            return await server.probe_active_provider(cfg)
        finally:
            server.httpx_client = orig
            server._OFFLINE_MODE = True

    try:
        online2, err2 = asyncio.run(_closed())
        check("client 已关闭时探测降级而非抛异常", online2 is False, f"online={online2} err={err2!r}")
        check("降级时带回可读原因", bool(err2), repr(err2))
    except Exception as exc:  # noqa: BLE001
        check("client 已关闭时探测降级而非抛异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        server._OFFLINE_MODE = True


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


# ------------------------------------------------------------ 13. 重导出公共面
def test_re_export_surface():
    """server 对 server_pkg 的重导出是**公共 API 面**，不是"没用的导入"。

    静态检查（ruff F401）会把这一整块标成未使用，很容易被后人"顺手清理"，
    然后静默打断所有 `server._mask_key(...)` 形式的外部调用与测试。
    这里把契约钉住：符号必须存在且可调用。
    """
    for name in ("_mask_key", "_clean_history", "_merge_sessions", "mask_credentials",
                 "normalize_tts_text", "parse_style_prefix", "_sessions_fp", "_safe_int"):
        check(f"server 重导出 {name} 可用", hasattr(server, name), "missing")
    check("重导出的函数是真函数（不是 None 占位）",
          callable(getattr(server, "_mask_key", None)), "not callable")


# ------------------------------------------------------------ 14. /api/stats 畸形数据
def test_stats_tolerates_malformed_entries(client):
    """第九轮回归：/api/stats 必须容忍客户端可控的畸形字段。

    会话经 PUT /api/sessions 原样落盘，所以 history 里的 content/ts 完全是
    客户端可控且**驻留**的数据。旧实现 `len(m.get("content") or "")` 遇到数字
    抛 TypeError、`time.localtime(ts/1000)` 遇到 Infinity/1e15 抛 OverflowError /
    OSError —— 任一坏条目都会让设置页「陪伴足迹」永久 500，且用户无从知道原因
    （删掉那条会话才能恢复）。
    """
    with sandbox() as sb:
        bad = [
            {"role": "user", "content": 5, "ts": 1},                     # content 非字符串
            {"role": "assistant", "content": None, "ts": float("inf")},  # ts 无穷
            {"role": "user", "content": {"a": 1}, "ts": 1e15},           # ts 越界 + content 非字符串
            {"role": "assistant", "content": "正常", "ts": float("nan")},
            {"role": "user", "content": "好", "ts": -1},
            {"role": "user", "content": "好", "ts": True},
        ]
        sb.path.write_text(json.dumps({"sessions": [
            {"id": "s-bad", "title": "坏数据", "updatedAt": 1700000000000, "history": bad},
        ], "deleted": []}, ensure_ascii=False), encoding="utf-8")
        server._sess_cache.clear()
        server._sess_cache.update(_mtime_ns=0, _value={"sessions": [], "deleted": []})
        r = client.get("/api/stats")
        check("/api/stats 遇到畸形 content/ts 仍返回 200",
              r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 200:
            d = r.json()
            check("畸形条目仍被计入条数（不静默丢数据）",
                  d.get("total_messages") == len(bad), f"total={d.get('total_messages')}")
            check("daily_7d 结构完整（7 天）", len(d.get("daily_7d") or []) == 7)
            check("total_chars 是整数（非字符串拼接）",
                  isinstance(d.get("total_chars"), int), repr(d.get("total_chars")))


# ------------------------------------------------------------ 15. 访问口令 fail-closed
def test_access_token_fail_closed():
    """第九轮安全加固：config 读取异常时**不得**把门禁降级成全放行。

    旧实现 `except Exception: cfg_tok = ""` 会把"配置被改坏"当成"用户没配口令"，
    access_gate 随即放行 —— ngrok 公网上 /uploads/* 附件与 /api/sync/stream
    全部未认证可读，且没有任何日志痕迹。
    """
    saved = (server._last_good_token, server.CONFIG_PATH, server.load_config)
    try:
        # 1) 有过成功读取记录后配置坏掉 -> 继续要求同一口令
        server._last_good_token = "known-token-abc"
        def _boom(*a, **k):
            raise ValueError("config.json 被改坏")
        server.load_config = _boom
        got = server._access_token()
        check("config 异常时回退到上一次生效口令（fail-closed）",
              got == "known-token-abc", repr(got))

        # 2) 从未成功读取过 + 无环境变量 -> 只能放行，但必须有 ERROR 日志
        server._last_good_token = ""
        saved_env = os.environ.pop("ACCESS_TOKEN", None)
        try:
            got2 = server._access_token()
            check("无历史口令且无环境变量时返回 None（明确放行）",
                  got2 is None, repr(got2))
        finally:
            if saved_env is not None:
                os.environ["ACCESS_TOKEN"] = saved_env

        # 3) 从未成功读取 + 有环境变量 -> 回退到环境变量
        server._last_good_token = ""
        os.environ["ACCESS_TOKEN"] = "env-token-xyz"
        try:
            got3 = server._access_token()
            check("config 异常时回退到环境变量 ACCESS_TOKEN",
                  got3 == "env-token-xyz", repr(got3))
        finally:
            os.environ.pop("ACCESS_TOKEN", None)
    finally:
        server._last_good_token, server.CONFIG_PATH, server.load_config = saved


def test_access_token_failsafe_on_degraded_config(client):
    """第十轮：config.json 是**非法 JSON** 时门禁必须 fail-closed。

    旧行为只兜住了 load_config() **抛异常** 这一种损坏（第九轮）。而最常见的损坏
    ——非法 JSON / 读不到——走的是 load_config 的**静默降级**分支：它不抛异常，而是
    return _default_config，而那份默认配置里没有 access_token 键。于是
    _access_token() 返回 None → access_gate 判定"用户没配口令"→ **全放行**，
    而且日志里一个字都没有。触发条件在本项目非常现实：跑在移动云盘同步盘上，
    config.json 很容易被同步客户端截成半截。
    """
    tmp = Path(tempfile.mkdtemp(prefix="degraded_cfg_"))
    saved_path = server.CONFIG_PATH
    saved_tok = server._last_good_token
    saved_ok = server._cfg_load_ok
    saved_fn = server._access_token
    saved_cfg_cache = dict(server._cfg_cache)
    saved_stat_cache = dict(server._cfg_stat_cache)
    saved_env = os.environ.pop("ACCESS_TOKEN", None)
    try:
        server.CONFIG_PATH = tmp / "config.json"
        # 半截 JSON：编辑器写坏 / 云盘同步截断的典型形态
        server.CONFIG_PATH.write_text('{"provider": "cloud", "roles": {', encoding="utf-8")
        server._cfg_cache.update(_mtime_ns=0, _value={})
        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        server._last_good_token = "known-token-abc"
        server._cfg_load_ok = True

        cfg = server.load_config()
        check("非法 JSON 时 load_config 不抛异常（走的是降级分支）",
              isinstance(cfg, dict), repr(cfg)[:80])
        check("降级返回的默认配置里没有 access_token（这正是漏洞成因）",
              not (cfg.get("access_token") or ""), str(sorted(cfg)[:6]))
        check("降级已被标记（_cfg_load_ok=False）",
              server._cfg_load_ok is False, repr(server._cfg_load_ok))

        got = server._access_token()
        check("口令 fail-closed：延续上一次生效口令，不放行匿名",
              got == "known-token-abc", repr(got))

        # 端点层：恢复真实门禁 —— 未带凭据必须 401（旧实现这里会 200 全放行）
        server._access_token = _REAL_ACCESS_TOKEN
        r = client.get("/api/sessions")
        check("配置损坏时 /api/sessions 必须 401（旧实现 200 全放行）",
              r.status_code == 401, f"status={r.status_code} body={r.text[:120]}")
        r2 = client.get("/api/sessions", headers={"Authorization": "Bearer known-token-abc"})
        check("fail-closed 不误伤本人：带正确口令仍 200", r2.status_code == 200,
              f"status={r2.status_code}")

        # 反向用例：配置健康且确实没配口令时仍应放行（不破坏本机直连）
        server.CONFIG_PATH.write_text(json.dumps({"provider": "cloud", "roles": {}}),
                                      encoding="utf-8")
        server._cfg_cache.update(_mtime_ns=0, _value={})
        server._cfg_stat_cache.update(mtime_ns=0, checked_at_ns=0)
        server._last_good_token = ""
        check("健康配置且确实未配口令：返回 None（放行本机直连）",
              server._access_token() is None, repr(server._access_token()))
    finally:
        if saved_env is not None:
            os.environ["ACCESS_TOKEN"] = saved_env
        server.CONFIG_PATH = saved_path
        server._last_good_token = saved_tok
        server._cfg_load_ok = saved_ok
        server._access_token = saved_fn
        server._cfg_cache.update(saved_cfg_cache)
        server._cfg_stat_cache.update(saved_stat_cache)


def test_gate_auth_failures_are_counted(client):
    """第十轮：`/api/*` 上的鉴权失败必须计数/留痕（旧实现是爆破防护的完整绕过口）。

    旧行为：失败计数/锁定/留痕只挂在 `POST /api/login` 上，而**真正守门的是
    access_gate → _auth_ok()**。攻击者对任意 `/api/*` 逐个换
    `Authorization: Bearer <猜测>` 就能无限次尝试口令 —— 那套防护完全不参与，
    不计数、不留痕、不退避，等于形同虚设。
    """
    saved_tok = server._access_token
    saved_max = server._LOGIN_FAIL_MAX
    saved_backoff = server._LOGIN_BACKOFF_SECONDS
    saved_track = dict(server._login_track)
    try:
        server._access_token = lambda: "secret-gate-token"
        server._LOGIN_FAIL_MAX = 2
        server._LOGIN_BACKOFF_SECONDS = 0.0  # 别为测试白等秒级退避
        server._login_track.clear()
        ip = "testclient"

        r1 = client.get("/api/sessions", headers={"Authorization": "Bearer wrong-1"})
        check("错误 Bearer 被拒（401）", r1.status_code == 401, f"status={r1.status_code}")
        check("失败已被计数（旧实现计数恒为 0）",
              server._login_track.get(ip, {}).get("fails") == 1, str(server._login_track))

        client.get("/api/sessions", headers={"Authorization": "Bearer wrong-2"})
        check("第二次失败继续累计",
              server._login_track.get(ip, {}).get("fails") == 2, str(server._login_track))
        check("达到阈值即判定锁定（门禁与 /api/login 共用同一阈值）",
              server._login_locked(ip) is True, str(server._login_track))

        r3 = client.get("/api/sessions", headers={"Authorization": "Bearer secret-gate-token"})
        check("带正确口令仍 200（不产生假锁定）", r3.status_code == 200, f"status={r3.status_code}")

        before = server._login_track.get(ip, {}).get("fails", 0)
        client.get("/api/sessions")
        check("完全无凭据的访问同样计数",
              server._login_track.get(ip, {}).get("fails", 0) == before + 1,
              str(server._login_track))
        check("白名单端点 /api/health 不参与计数（探活必须始终可用）",
              client.get("/api/health").status_code == 200)
    finally:
        server._access_token = saved_tok
        server._LOGIN_FAIL_MAX = saved_max
        server._LOGIN_BACKOFF_SECONDS = saved_backoff
        server._login_track.clear()
        server._login_track.update(saved_track)


# ------------------------------------------------------------ 16. URL 白名单（SSRF）
def test_url_guard_blocks_internal_targets():
    """第十轮：用户自填 base_url 不得把服务端变成内网/管理面探测器。

    旧行为：/api/llm-models 在"未传 key + 自填 base_url"时照样带空 Authorization
    发 GET，填 http://127.0.0.1:4040/api 就能把 ngrok 管理面返回的 JSON 回显出来。
    """
    g = server._url_is_blocked_target
    # 必须拦
    check("拦 ngrok 管理端口 4040", bool(g("http://127.0.0.1:4040/api")), "not blocked")
    check("拦 4041", bool(g("http://localhost:4041/api")), "not blocked")
    check("拦云元数据地址", bool(g("http://169.254.169.254/latest/meta-data")), "not blocked")
    check("拦 file:// 协议", bool(g("file:///c:/windows/win.ini")), "not blocked")
    check("拦 gopher:// 协议", bool(g("gopher://x/")), "not blocked")
    check("拦回环地址（非配置值）", bool(g("http://127.0.0.1:11434/v1")), "not blocked")
    check("拦 IPv6 回环", bool(g("http://[::1]:8080/v1")), "not blocked")
    check("缺主机名也拦", bool(g("http://")), "not blocked")
    # 必须放行（否则会误伤正常用法）
    check("放行公网 https 网关", not g("https://api.deepseek.com/v1"), "wrongly blocked")
    check("放行无 scheme 的公网域名", not g("api.deepseek.com/v1"), "wrongly blocked")
    check("放行局域网自建网关（本地 LLM 场景）", not g("http://192.168.1.50:11434/v1"), "wrongly blocked")
    check("配置里本来就是该回环地址时放行", not g("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
          "wrongly blocked")
    check("空串放行", not g(""), "wrongly blocked")


def test_url_guard_normalizes_equivalent_hosts():
    """第十一轮：URL 里「本机」的等价写法必须全拦。

    旧实现只做字符串比对（127.0.0.1 / localhost / startswith("127.")），下面这些
    写法**httpx 真的会连过去**却整片放行 —— 填 http://2130706433:4040/ 就能把 ngrok
    管理面读回来，填 http://2130706433/ 则等于让服务端打自己。

    归一化办法：ipaddress 直解 + socket.inet_aton 兜底（接受整型/十六进制/八进制/
    短式四种旧写法），IPv4-mapped IPv6 取内层 IPv4 再判型。"""
    g = server._url_is_blocked_target
    check("拦十进制整型 2130706433", bool(g("http://2130706433/v1")), "not blocked")
    check("拦十六进制 0x7f000001", bool(g("http://0x7f000001/v1")), "not blocked")
    check("拦八进制 017700000001", bool(g("http://017700000001/v1")), "not blocked")
    check("拦短式 127.1", bool(g("http://127.1/v1")), "not blocked")
    check("拦 IPv4-mapped IPv6 [::ffff:127.0.0.1]", bool(g("http://[::ffff:127.0.0.1]/v1")), "not blocked")
    check("拦带尾部点的 localhost.", bool(g("http://localhost./v1")), "not blocked")
    check("拦未指定地址 [::]", bool(g("http://[::]:9000/v1")), "not blocked")
    check("拦 fe80:: 链路本地", bool(g("http://[fe80::1]/v1")), "not blocked")
    check("拦带作用域 id 的 fe80::1%25eth0",
          bool(g("http://[fe80::1%25eth0]/v1")), "not blocked")
    check("拦整数写法 + 管理端口", bool(g("http://2130706433:4040/api")), "not blocked")
    # 归一化不得误伤正常用法
    check("仍放行局域网自建网关", not g("http://192.168.1.50:11434/v1"), "wrongly blocked")
    check("仍放行公网域名（名字里像 127 也不行）",
          not g("https://127-0-0-1.example.com/v1"), "wrongly blocked")
    check("仍放行域名里含 localhost 的第三方",
          not g("https://notlocalhost.example.com/v1"), "wrongly blocked")


def test_url_guard_bad_port_returns_reason_not_exception():
    """第十一轮：端口写错（:99999 / :abc）曾经把端点打成 500。

    旧实现在 try/except **之外**直接读 `parsed.port`：urlparse 能解析 URL，但 .port
    抛 ValueError，一路冒到端点 → 未捕获异常 → 用户看到「服务器内部错误」+ 一整条
    traceback，而不是"端口不合法"。手填地址时这是最常见的笔误之一。"""
    g = server._url_is_blocked_target
    check("越界端口返回拒绝原因", bool(g("http://example.com:99999/v1")), "not blocked")
    check("非数字端口返回拒绝原因", bool(g("http://h:abc/v1")), "not blocked")
    for c in ("http://example.com:99999/v1", "http://h:abc/v1", "http://[::1/v1",
              "http://x:0/v1", "http://x:-1/v1"):
        try:
            g(c)
            check(f"不抛异常：{c}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"不抛异常：{c}", False, f"{type(exc).__name__}: {exc}")


def test_url_resolves_to_internal_blocks_dns_rebound_host():
    """第十一轮：`127.0.0.1.nip.io` 这类「公网域名解析到本机」必须拦。

    只做字面量归一化，挂个免费 wildcard DNS（nip.io / sslip.io）就能把回环藏在一个
    合法域名后面 —— DNS rebinding 的静态版本。离线用例不该真发 DNS 查询，
    所以替换掉 _getaddrinfo 这个缝。"""
    orig = server._getaddrinfo

    async def stub(host):
        table = {
            "evil.nip.io": "127.0.0.1",
            "meta.example": "169.254.169.254",
            "v6loop.example": "::1",
            "lan.gateway": "192.168.1.50",
        }
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]
        raise OSError("no dns here")

    def run(url, cfg=""):
        return asyncio.run(server._url_resolves_to_internal(url, cfg))

    server._getaddrinfo = stub
    try:
        check("解析到 127.0.0.1 的域名被拦", "SSRF" in run("http://evil.nip.io/v1"),
              repr(run("http://evil.nip.io/v1")))
        check("解析到 ::1 的域名被拦", "SSRF" in run("http://v6loop.example/v1"))
        check("解析到云元数据地址的域名被拦", "SSRF" in run("http://meta.example/v1"))
        check("解析到局域网网关的域名仍放行（本地 LLM 场景）",
              not run("http://lan.gateway:11434/v1"))
        check("解析失败时放行（交由真实请求报错，不因 DNS 一时不通判死）",
              not run("http://unresolvable.example/v1"))
        check("IP 字面量不触发 DNS 查询（字面量闸已判过）",
              not run("http://8.8.8.8/v1"))
        check("机主自己配的地址不拦",
              not run("http://evil.nip.io/v1", "http://evil.nip.io/v1"))
    finally:
        server._getaddrinfo = orig


def test_llm_models_ssrf_endpoint(client):
    """端点层：自填内网地址必须 400 拒绝，且**不能**真的发出请求。"""
    with sandbox():
        r = client.post("/api/llm-models", json={"base_url": "http://127.0.0.1:4040/api"})
        check("自填 ngrok 管理面地址被 400 拒绝", r.status_code == 400, f"status={r.status_code}")
        check("拒绝原因是 SSRF 防护", "SSRF" in (r.text or "") or "管理端口" in (r.text or ""),
              r.text[:160])
        r2 = client.post("/api/llm-models", json={"base_url": "file:///etc/passwd"})
        check("file:// 协议被 400 拒绝", r2.status_code == 400, f"status={r2.status_code}")


def test_llm_quota_ssrf_endpoint(client):
    """第十轮：/api/llm-quota 必须与 /api/llm-models 走同一道 SSRF 闸。

    旧行为：该端点会按调用方给的 base_url 拼出 quota 端点并**真的发 GET**，却完全
    没有 _url_is_blocked_target 检查 —— 口令一旦泄露/被绕过，SSRF 靶点就从
    llm-models 转移到这个端点（可打内网 / 本机 ngrok 管理面 4040）。
    """
    with sandbox():
        r = client.post("/api/llm-quota", json={
            "base_url": "http://127.0.0.1:4040/api", "api_key": "fake-key",
            "billing_mode": "token_plan"})
        check("自填 ngrok 管理面地址被 400 拒绝（旧实现会真的发出请求）",
              r.status_code == 400, f"status={r.status_code} body={r.text[:160]}")
        check("拒绝原因是 SSRF 防护",
              "SSRF" in r.text or "管理端口" in r.text, r.text[:160])
        r2 = client.post("/api/llm-quota", json={"base_url": "file:///etc/passwd",
                                                 "api_key": "fake-key"})
        check("file:// 协议被 400 拒绝", r2.status_code == 400, f"status={r2.status_code}")
        # 沿用 config 里（非回环）的地址时不应被误拦 —— 未配密钥才是这里的正确报错
        r3 = client.post("/api/llm-quota", json={
            "base_url": "https://api.minimaxi.com/v1", "billing_mode": "payg"})
        check("公网地址不被 SSRF 闸误拦（报错是缺密钥而非 SSRF）",
              r3.status_code == 400 and "SSRF" not in r3.text, f"status={r3.status_code} {r3.text[:120]}")


def test_url_guard_errors_are_400_not_500(client):
    """第十一轮：端点层确认——端口笔误是 400 提示，不是 500 内部错误。"""
    with sandbox():
        r = client.post("/api/llm-models", json={"base_url": "http://example.com:99999/v1"})
        check("越界端口 -> 400（旧实现 500）", r.status_code == 400, f"status={r.status_code}")
        check("提示是端口问题", "端口" in r.text, r.text[:120])
        r2 = client.post("/api/llm-quota", json={"base_url": "http://h:abc/v1", "api_key": "k"})
        check("非数字端口 -> 400（旧实现 500）", r2.status_code == 400, f"status={r2.status_code}")
        r3 = client.post("/api/llm-quota", json={"base_url": "http://2130706433/v1", "api_key": "k"})
        check("整型写法在端点层也被 SSRF 闸拦下", r3.status_code == 400 and "SSRF" in r3.text,
              f"status={r3.status_code} {r3.text[:120]}")


# ------------------------------------------------- 18. XFF 可信度（第十一轮）
def _fake_req(peer, headers=None):
    return SimpleNamespace(url=SimpleNamespace(scheme="http"),
                           client=SimpleNamespace(host=peer),
                           headers=headers or {})


def test_xff_untrusted_from_lan_peer():
    """第十一轮：局域网直连对端伪造 X-Forwarded-For 不得换到新的限流桶。

    旧实现把「对端在任意私有网段」(is_private) 也算自己人。可服务默认 bind 0.0.0.0，
    局域网里**任何一台机器**直连过来对端都是私网地址，于是它自己塞的 XFF 被采信，
    _client_ip 每请求换一个伪造值就换一个限流桶 —— "8 次失败锁 10 分钟"变成无限重试。
    同机代理（ngrok）的连接必然来自回环，所以判据只该看 is_loopback。"""
    ips = {server._client_ip(_fake_req("192.168.1.50", {"x-forwarded-for": f"10.0.0.{i}"}))
           for i in range(1, 40)}
    check("局域网对端轮换 XFF 拿不到多个限流桶", ips == {"192.168.1.50"}, str(ips))
    check("同机代理（回环）仍采信 XFF 末跳（ngrok 不受影响）",
          server._client_ip(_fake_req("127.0.0.1", {"x-forwarded-for": "1.2.3.4, 5.6.7.8"})) == "5.6.7.8")
    check("IPv6 回环同样算同机",
          server._client_ip(_fake_req("::1", {"x-forwarded-for": "1.2.3.4"})) == "1.2.3.4")
    check("回环对端无 XFF 时用对端自身",
          server._client_ip(_fake_req("127.0.0.1", {})) == "127.0.0.1")
    check("公网直连不采信 XFF",
          server._client_ip(_fake_req("8.8.8.8", {"x-forwarded-for": "1.2.3.4"})) == "8.8.8.8")
    check("局域网对端不算 behind_local_proxy", not server._behind_local_proxy(_fake_req("10.0.0.7")))
    check("回环对端算 behind_local_proxy", server._behind_local_proxy(_fake_req("127.0.0.1")))


def test_lan_peer_cannot_brute_force_by_rotating_xff():
    """第十一轮（真实门禁、真实请求）：局域网对端连发错误口令 + 每请求换 XFF。

    旧实现下这 5 次请求落在 5 个不同的限流桶里，永远攒不到上限（全 401）；
    修好后 3 次即触发 429，且桶里只有真实对端那一个 IP。"""
    from fastapi.testclient import TestClient  # noqa: E402

    saved_tok = server._access_token
    saved_max = server._LOGIN_FAIL_MAX
    saved_backoff = server._LOGIN_BACKOFF_SECONDS
    saved_track = dict(server._login_track)
    try:
        server._access_token = lambda: "correct-token-xyzzy"
        server._LOGIN_FAIL_MAX = 3
        server._LOGIN_BACKOFF_SECONDS = 0
        server._login_track.clear()
        lan = TestClient(server.app, client=("192.168.1.50", 1234), raise_server_exceptions=False)
        codes = [lan.post("/api/login", json={"token": f"guess-{i}"},
                          headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
                 for i in range(5)]
        check("累积到上限后转 429（旧实现永远 401）", 429 in codes, str(codes))
        check("第一个失败仍是 401（没提前把正常路径锁死）", codes[0] == 401, str(codes))
        check("限流桶里只有真实对端 IP，没有伪造的那些",
              list(server._login_track.keys()) == ["192.168.1.50"], str(server._login_track))
    finally:
        server._access_token = saved_tok
        server._LOGIN_FAIL_MAX = saved_max
        server._LOGIN_BACKOFF_SECONDS = saved_backoff
        server._login_track.clear()
        server._login_track.update(saved_track)


# ------------------------------------------------- 19. 跨源闸（第十一轮）
def test_cross_origin_rejected_in_token_mode(client):
    """第十一轮：配了口令时跨源请求也必须被拒，且**不给** CORS 放行头。

    旧实现把跨源安全寄托在"有口令保护"上（`allow_origins=["*"]`），但 /api/login
    的跨源响应里带着 `Access-Control-Allow-Origin: *`，而口令校验在该端点**之后**：
    恶意页面可以跨源 POST 猜口令，并凭 ACAO:* 读懂 200 还是 401 —— 一个分布式口令
    oracle。每个访客浏览器都是一个不同出口 IP，按 IP 计数的锁定完全跟不上。
    改完：跨源一律 403、无 ACAO；同源（Origin == Host）完全不受影响。"""
    saved_tok = server._access_token
    saved_backoff = server._LOGIN_BACKOFF_SECONDS
    saved_track = dict(server._login_track)
    try:
        server._access_token = lambda: "correct-token-xyzzy"
        server._LOGIN_BACKOFF_SECONDS = 0
        server._login_track.clear()
        evil = {"Origin": "https://evil.example"}
        r = client.post("/api/login", json={"token": "guess"}, headers=evil)
        check("跨源登录被 403 拒绝", r.status_code == 403, f"status={r.status_code}")
        check("跨源响应不再带 ACAO:*",
              r.headers.get("access-control-allow-origin") is None,
              str(r.headers.get("access-control-allow-origin")))
        r2 = client.options("/api/login", headers={**evil, "Access-Control-Request-Method": "POST"})
        check("跨源预检同样被拒（旧实现 200 + ACAO:*）",
              r2.status_code == 403 and r2.headers.get("access-control-allow-origin") is None,
              f"status={r2.status_code} acao={r2.headers.get('access-control-allow-origin')}")
        check("跨源读业务 API 同样被拒",
              client.get("/api/status", headers=evil).status_code == 403)
        check("Origin: null 不可信",
              client.get("/api/status", headers={"Origin": "null"}).status_code == 403)
        r3 = client.post("/api/login", json={"token": "guess"}, headers={"Origin": "http://testserver"})
        check("同源请求不受影响（仍是口令错误 401）", r3.status_code == 401, f"status={r3.status_code}")
        check("无 Origin 头不受影响（curl / PWA / SSE）",
              client.get("/api/health").status_code == 200)
        # 归一化：默认端口 / 大小写 / 尾部点不该把自己人拦掉
        def origin_ok(host_header, origin, scheme="http"):
            req = SimpleNamespace(url=SimpleNamespace(scheme=scheme),
                                  client=SimpleNamespace(host="127.0.0.1"),
                                  headers={"host": host_header})
            return server._origin_is_trusted(req, origin)

        check("Origin 省略默认端口仍算同源", origin_ok("example.com:80", "http://example.com"))
        check("Host 带尾部点仍算同源", origin_ok("example.com.:8000", "http://example.com:8000"))
        check("同源但端口不同 -> 拒绝", not origin_ok("example.com:8000", "http://example.com:5173"))
        check("来源不同域名 -> 拒绝", not origin_ok("example.com:8000", "http://evil.com:8000"))
    finally:
        server._access_token = saved_tok
        server._LOGIN_BACKOFF_SECONDS = saved_backoff
        server._login_track.clear()
        server._login_track.update(saved_track)


# ------------------------------------------------------------ 17. 白名单端点按鉴权收敛
def test_whitelist_endpoints_redact_when_locked(client):
    """第十轮：配了口令时，/api/health 与 /api/activity 不得对匿名调用方泄露运维细节。

    这两个端点被 access_gate 无条件白名单放行（探活需要），于是只拿到隧道地址的人
    未登录就能读到磁盘余量、分供应商 token 消耗、SSE 订阅者数，以及"机主此刻正在
    聊天"的实时时间戳。
    """
    saved = (server._access_token, server._auth_ok)
    try:
        with sandbox():
            server._access_token = lambda: "secret-token"
            server._auth_ok = lambda req, tok: False  # 模拟未登录
            h = client.get("/api/health")
            check("匿名 health 仍 200（探活不能坏）", h.status_code == 200, f"status={h.status_code}")
            d = h.json()
            check("匿名 health 只回 ok 探活字段", set(d.keys()) == {"ok"} and d["ok"] is True, str(d))
            a = client.get("/api/activity")
            check("匿名 activity 仍 200", a.status_code == 200, f"status={a.status_code}")
            check("匿名 activity 不回传精确时间戳",
                  "last_chat_ts" not in a.json() and "active" in a.json(), str(a.json()))

            # 已鉴权（无口令模式 / 登录后）仍要拿到完整运维字段，否则设置页与部署脚本失能
            server._access_token = lambda: None
            d2 = client.get("/api/health").json()
            check("无口令模式（本机）health 仍回全部细节",
                  "disk" in d2 and "llm_usage" in d2, str(sorted(d2)[:8]))
            d3 = client.get("/api/activity").json()
            check("无口令模式 activity 仍回时间戳", "last_chat_ts" in d3, str(d3))
    finally:
        server._access_token, server._auth_ok = saved


# ------------------------------------------------------------ 18. 会话上限截断留痕
def test_sessions_cap_truncation_reported(client):
    """第十轮：合并层的 500 上限截断必须带出 stats，且 PUT 时落审计日志。

    截断是**永久**的（超出部分不会随下次 PUT 回来），此前完全静默。
    """
    from server_pkg.sessions_merge import SESSIONS_CAP, _merge_sessions
    check("上限常量导出且为 500", SESSIONS_CAP == 500, str(SESSIONS_CAP))
    many = [{"id": f"s{i}", "title": f"t{i}", "updatedAt": 1700000000000 + i,
             "history": [{"role": "user", "content": "x"}]} for i in range(SESSIONS_CAP + 7)]
    stats: dict = {}
    out = _merge_sessions([], many, {}, stats=stats)
    check(f"返回条数被上限截断到 {SESSIONS_CAP}", len(out) == SESSIONS_CAP, f"len={len(out)}")
    check("stats 报出被丢弃条数", stats.get("dropped") == 7, str(stats))
    check("stats 给出被丢弃的会话 id", len(stats.get("dropped_ids") or []) == 7, str(stats))
    # 不传 stats 时行为完全不变（既有调用方零改动）
    out2 = _merge_sessions([], many, {})
    check("不传 stats 时返回值不变", len(out2) == len(out))

    # 端点层 · 入口截断：本端一次送来超过上限的会话，超出部分根本不进合并
    # （这是更早、更彻底的一种静默丢失，只在合并层记账会完全看不到它）
    with sandbox() as sb:
        log_path = server.SESSIONS_PATH.parent / "sessions_truncated.log"
        if log_path.exists():
            log_path.unlink()
        r = client.put("/api/sessions?client=cap-test",
                       json={"sessions": many, "deleted": []})
        check("超量 PUT 仍成功（不因截断报错）", r.status_code == 200, f"status={r.status_code}")
        check("入口截断审计日志已落盘", log_path.exists(), str(log_path))
        if log_path.exists():
            recs = [json.loads(ln) for ln in
                    log_path.read_text(encoding="utf-8").strip().splitlines() if ln.strip()]
            check("审计记录 stage=ingest",
                  any(x.get("stage") == "ingest" for x in recs), str(recs)[:200])
            rec = recs[-1]
            check("审计记录含 dropped/cap/ids",
                  rec.get("dropped", 0) >= 7 and rec.get("cap") == SESSIONS_CAP
                  and 0 < len(rec.get("dropped_ids") or []) <= 50, str(rec)[:220])
            check("入口截断报告的 dropped 与实收条目对得上",
                  rec.get("total") == len(many), str(rec)[:220])

    # 端点层 · 合并层截断：存量已达上限、来端只送少量新会话
    with sandbox() as sb2:
        log_path2 = server.SESSIONS_PATH.parent / "sessions_truncated.log"
        if log_path2.exists():
            log_path2.unlink()
        full = [{"id": f"m{i}", "title": f"m{i}", "updatedAt": 1700000000000 + i,
                 "history": [{"role": "user", "content": "x"}]} for i in range(SESSIONS_CAP)]
        sb2.path.write_text(json.dumps({"sessions": full, "deleted": []}, ensure_ascii=False),
                            encoding="utf-8")
        server._sess_cache.clear()
        server._sess_cache.update(_mtime_ns=0, _value={"sessions": [], "deleted": []})
        r2 = client.put("/api/sessions?client=merge-cap", json={
            "sessions": [{"id": "newcomer", "title": "新", "updatedAt": time.time() * 1000,
                          "history": [{"role": "user", "content": "hi"}]}],
            "deleted": [],
        })
        check("存量已满时少量 PUT 仍成功", r2.status_code == 200, f"status={r2.status_code}")
        recs2 = [json.loads(ln) for ln in
                 log_path2.read_text(encoding="utf-8").strip().splitlines() if ln.strip()] \
            if log_path2.exists() else []
        check("合并层截断留痕且 stage=merge",
              any(x.get("stage") == "merge" and x.get("dropped") for x in recs2), str(recs2)[:220])


# ------------------------------------------------------------ 20. 非有限浮点不污染接口
def _strict_json(text: str):
    """按 RFC 8259 严格解析：拒绝 Infinity / -Infinity / NaN。

    浏览器的 JSON.parse / Response.json() 就是这个严格度，而 Python 的 json.loads
    默认接受这三个字面量 —— 所以用默认 json.loads 断言会漏掉整整一类故障。
    """
    def _reject(token):
        raise ValueError(f"非法 JSON 字面量：{token}")
    return json.loads(text, parse_constant=_reject)


def test_sessions_nonfinite_never_poisons_api(client):
    """第十轮：inf/nan 落盘后会让 GET /api/sessions 的**响应体本身**非法。

    旧行为：_dump 与 _render_sessions_body 都用默认 allow_nan=True，于是 1e400
    （语法完全合法的 JSON 数字，解析为 inf）会被写成 `Infinity` 落盘、再原样下发。
    Python 侧看起来一切正常，但每台设备的 `r.json()` 都抛错、重试几次后放弃 ——
    多端同步**永久停摆**，服务端日志无痕，只能手改文件自愈（手机端还改不了）。
    """
    from server_pkg.sessions_merge import _sanitize_nonfinite
    # —— 单元层：递归清洗、结构保持 ——
    got = _sanitize_nonfinite({"a": float("inf"), "b": [1, float("nan"), {"c": float("-inf")}],
                               "d": "x", "e": None, "f": True})
    check("清洗后结构不变（键一个不少）",
          set(got) == {"a", "b", "d", "e", "f"}, str(got))
    check("inf/nan/-inf 一律变 None",
          got["a"] is None and got["b"][1] is None and got["b"][2]["c"] is None, str(got))
    check("正常值原样保留（含 bool/None/str）",
          got["b"][0] == 1 and got["d"] == "x" and got["e"] is None and got["f"] is True, str(got))
    check("有限浮点不被误伤", _sanitize_nonfinite(1.5) == 1.5)

    # —— 端点层 · 入站：客户端送 1e400（合法 JSON 数字）—— 
    with sandbox() as sb:
        bad_json = ('{"sessions":[{"id":"s-nonfinite","title":"无穷","updatedAt":1e400,'
                    '"history":[{"role":"user","content":"你好","ts":1e400}]}],'
                    '"deleted":[]}')
        r = client.put("/api/sessions?client=nonfinite",
                       content=bad_json.encode("utf-8"),
                       headers={"Content-Type": "application/json"})
        check("含 1e400 的 PUT 仍成功（不因清洗报错）", r.status_code == 200, f"status={r.status_code}")
        g = client.get("/api/sessions")
        check("GET /api/sessions 返回 200", g.status_code == 200, f"status={g.status_code}")
        try:
            doc = _strict_json(g.content.decode("utf-8"))
            ok_strict = True
        except ValueError as exc:
            ok_strict = False
            doc = {}
            check("响应体必须能被严格 JSON 解析（旧实现必失败）", False, repr(exc))
        if ok_strict:
            check("响应体可被严格 JSON 解析（旧实现必失败）", True)
            sess = [s for s in doc.get("sessions", []) if s.get("id") == "s-nonfinite"]
            check("会话没有被静默丢弃（只清洗值）", len(sess) == 1, str(doc.get("sessions"))[:200])
            if sess:
                check("updatedAt 被归一为 null 而不是丢弃",
                      sess[0].get("updatedAt") is None, str(sess[0].get("updatedAt")))
            check("磁盘上也不再写出 Infinity（下一次 PUT 已净化）",
                  "Infinity" not in sb.path.read_text(encoding="utf-8"),
                  "disk still contains Infinity")

    # —— 端点层 · 存量坏文件自愈：模拟同步盘回滚出来的 Illegal 文件 ——
    with sandbox() as sb2:
        sb2.path.write_text(
            '{"sessions":[{"id":"s-legacy-bad","title":"旧坏文件","updatedAt":1700000000000,'
            '"history":[{"role":"user","content":"早","ts":Infinity}]}],"deleted":[]}',
            encoding="utf-8")
        server._sess_cache.clear()
        server._sess_cache.update(_mtime_ns=0, _value={"sessions": [], "deleted": []})
        g2 = client.get("/api/sessions")
        check("存量含 Infinity 的文件：GET 仍 200（自愈不 500）",
              g2.status_code == 200, f"status={g2.status_code}")
        try:
            doc2 = _strict_json(g2.content.decode("utf-8"))
            check("存量坏文件读出来的响应体也能被严格解析", True)
            kept = [s for s in doc2.get("sessions", []) if s.get("id") == "s-legacy-bad"]
            check("存量坏文件里的会话仍保留（不丢数据）", len(kept) == 1, str(doc2)[:200])
            if kept:
                check("坏 ts 被归一为 null",
                      kept[0]["history"][0].get("ts") is None, str(kept[0]["history"]))
        except ValueError as exc:
            check("存量坏文件读出来的响应体也能被严格解析", False, repr(exc))


# ------------------------------------------------------------ 21. 截断保置顶
def test_sessions_truncation_keeps_pinned_first():
    """第十轮：超出上限时**绝不能先丢置顶会话**。

    前端列表是置顶优先，而服务端入站/合并两处截断此前只看 updatedAt —— 于是先被
    截掉的恰好是置顶的老会话（用户最在意的那几条），且无任何提示。
    """
    from server_pkg.sessions_merge import SESSIONS_CAP, _merge_sessions
    pinned_old = {"id": "s-pinned-old", "title": "置顶的老会话", "pinned": True,
                  "updatedAt": 1600000000000, "history": [{"role": "user", "content": "很重要"}]}
    rest = [{"id": f"n{i}", "title": f"n{i}", "updatedAt": 1700000000000 + i,
             "history": [{"role": "user", "content": "x"}]} for i in range(SESSIONS_CAP)]
    stats: dict = {}
    out = _merge_sessions([], [pinned_old, *rest], {}, stats=stats)
    ids = [s.get("id") for s in out]
    check("截断后仍保留置顶会话（旧实现必丢）",
          "s-pinned-old" in ids, f"pinned dropped; kept={len(ids)}")
    check("置顶会话排在最前", bool(ids) and ids[0] == "s-pinned-old", str(ids[:3]))
    check("被截断的是普通会话且只丢了一条", stats.get("dropped") == 1, str(stats))
    check("置顶会话不在被丢弃名单里",
          "s-pinned-old" not in (stats.get("dropped_ids") or []), str(stats.get("dropped_ids")))


# ------------------------------------------------------------ 19. 启动期剧情引擎预热
def test_story_manager_prewarmed_on_startup(client):
    """第十轮：lifespan 必须 **await** 剧情引擎构建完成再对外服务。

    旧写法 _spawn_bg(to_thread(...)) 把构建丢后台，首个剧情请求可能与预热线程抢
    threading.Lock，而后者正持锁做 mkdir/读 state/生成整季日历/写盘 —— 事件循环
    同步阻塞，SSE 全卡。TestClient 进入 lifespan 后管理器就必须已就绪。
    """
    check("TestClient 的 lifespan 已把剧情管理器构建好（不再靠后台竞态）",
          server._story_manager is not None, "still None after startup")



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
        test_stats_tolerates_malformed_entries(client)
        test_access_token_fail_closed()
        test_access_token_failsafe_on_degraded_config(client)
        test_gate_auth_failures_are_counted(client)
        test_url_guard_blocks_internal_targets()
        test_url_guard_normalizes_equivalent_hosts()
        test_url_guard_bad_port_returns_reason_not_exception()
        test_url_resolves_to_internal_blocks_dns_rebound_host()
        test_llm_models_ssrf_endpoint(client)
        test_llm_quota_ssrf_endpoint(client)
        test_url_guard_errors_are_400_not_500(client)
        test_xff_untrusted_from_lan_peer()
        test_lan_peer_cannot_brute_force_by_rotating_xff()
        test_cross_origin_rejected_in_token_mode(client)
        test_whitelist_endpoints_redact_when_locked(client)
        test_sessions_cap_truncation_reported(client)
        test_sessions_nonfinite_never_poisons_api(client)
        test_sessions_truncation_keeps_pinned_first()
        test_story_manager_prewarmed_on_startup(client)
        test_probe_never_breaks_status()
        test_re_export_surface()
        test_security_headers_and_request_id(client)
    print(f"\n{'=' * 50}\n后端加固测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
