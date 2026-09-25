# -*- coding: utf-8 -*-
"""运行时冒烟：对「已经在跑的服务」打一遍关键链路，验证真实 HTTP 行为。

与离线测试（run_tests.bat）的区别：离线测试直接调函数，这里走完整的
ASGI/HTTP 栈 —— 中间件、门禁、路由顺序、序列化，都是离线测试覆盖不到的。

用法：
    python qa_run.py                                  # 隔离实例 127.0.0.1:8010，免密
    python runtime_smoke.py                           # 默认打 8010
    python runtime_smoke.py http://127.0.0.1:8000     # 打真实服务（需口令）
    python runtime_smoke.py http://127.0.0.1:8000 <口令>

口令也可以在环境变量里给：ACCESS_TOKEN=<口令>
未提供口令时先探测是否需要登录：需要就直接报错退出，
**不会**在真实服务上写任何测试会话（2026-09 第九轮修正，见下）。

对真实服务运行时，本脚本只读不写业务数据的端点会跳过；写会话用 t- 前缀
（测试会话约定，服务端 GET 默认过滤），不会污染用户的会话列表。

第九轮修正记录：
  1. 口令支持。真实服务都开了 access_token，此前冒烟脚本裸打过去必然全项
     401，而且第一个失败点就抛 AttributeError（`body.get` 打在 JSON 字符串上）
     直接崩，看不出"其实是没登录"。
  2. 因此新增：`_ensure_auth()` 先探测，需要口令却没给就**报错退出**，
     而不是继续在被拒的状态下跑完 21 项、每项都写成"功能坏了"。
  3. 所有把响应当 dict 用的地方统一过 `_as_dict()`，鉴权失败/网关错误页
     不再变成 AttributeError 崩溃。
"""
import json
import os
import sys
import time
import http.cookiejar
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8010"
_BASE_ARG = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("VERIFY_BASE") or DEFAULT_BASE)
BASE = _BASE_ARG.rstrip("/")
TOKEN = (sys.argv[2] if len(sys.argv) > 2 else os.getenv("ACCESS_TOKEN") or "").strip()
_FAIL = 0

# 带 cookie 的 opener：登录成功后会话 cookie 由 CookieJar 自动带上
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def _as_dict(body) -> dict:
    """把响应体安全地当 dict 用：非 JSON 对象一律给空 dict，绝不 AttributeError。"""
    return body if isinstance(body, dict) else {}


def req(method: str, path: str, body=None, boundary=None, raw=None, timeout=30):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if raw is not None:
        data = raw
        headers["Content-Type"] = boundary or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(r, timeout=timeout) as resp:
            payload = resp.read()
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                try:
                    return resp.status, json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    return resp.status, payload.decode("utf-8", "replace")
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _ensure_auth() -> bool:
    """探测门禁：需要口令却没给就明确退出，避免"全项 401"被误读成功能坏了。

    返回 True 表示可以继续。
    """
    global TOKEN
    st, body = req("GET", "/api/status")
    if st == 200:
        return True
    if st != 401:
        check(f"服务可达 /api/status（当前 status={st}）", False, str(body)[:160])
        return False
    if not TOKEN:
        print(f"[ABORT] {BASE} 已开启访问门禁（/api/status 返回 401），但未提供口令。\n"
              f"        用法: python runtime_smoke.py {BASE} <访问口令>\n"
              f"        或  : set ACCESS_TOKEN=<口令> 后重跑\n"
              f"        注  : 未登录时冒烟脚本不会写入任何测试会话，本次未做任何改动。")
        return False
    st, body = req("POST", "/api/login", {"token": TOKEN})
    if st != 200:
        print(f"[ABORT] 口令被拒（POST /api/login -> {st}: {str(body)[:160]}）")
        return False
    st, body = req("GET", "/api/status")
    if st != 200:
        print(f"[ABORT] 登录后 /api/status 仍非 200（{st}）")
        return False
    print(f"[INFO] 已用口令登录 {BASE}")
    return True


def multipart(fields: dict, filename: str, content: bytes, ctype: str) -> tuple[bytes, str]:
    bd = "----smokeR7Boundary"
    parts = [f"--{bd}\r\n".encode(),
             f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n".encode(),
             content, f"\r\n--{bd}--\r\n".encode()]
    return b"".join(parts), f"multipart/form-data; boundary={bd}"


def concurrent_session_writes() -> None:
    """并发 PUT 完整性：多端同时写会话是本服务的常态，而离线测试全是单线程。

    打的是 _io_locks['sessions'] 的读-合并-写整链：并发下若锁没覆盖到、或缓存
    与磁盘不同步，表现就是某些端的会话被静默吞掉、或 sessions.json 留下 .tmp 垃圾。
    """
    from concurrent.futures import ThreadPoolExecutor

    n_dev, n_sess = 8, 6
    payloads = []
    for d in range(n_dev):
        payloads.append([{
            "id": f"t-conc{d}-{i}",
            "title": f"并发{d}-{i}",
            "updatedAt": 1700000000000 + d * 100 + i,
            "history": [{"role": "user", "content": f"dev{d} sess{i}"}],
        } for i in range(n_sess)])

    with ThreadPoolExecutor(max_workers=n_dev) as ex:
        futs = [ex.submit(req, "PUT", f"/api/sessions?client=dev{d}",
                          {"sessions": p, "deleted": []}) for d, p in enumerate(payloads)]
        codes = [f.result()[0] for f in futs]
    check("并发 PUT 全部成功", all(c == 200 for c in codes), f"codes={codes}")

    st, body = req("GET", "/api/sessions?include_test=1")
    ids = {s.get("id") for s in (_as_dict(body).get("sessions") or []) if isinstance(s, dict)}
    expect = {f"t-conc{d}-{i}" for d in range(n_dev) for i in range(n_sess)}
    lost = expect - ids
    # 合并语义是「按 id 并集 + 最新胜」：并发各写各的会话时一个都不该丢
    check("并发写入的会话无一被吞掉", not lost, f"lost={sorted(lost)[:6]} of {len(expect)}")

    st, body = req("GET", "/api/sessions?include_test=1")
    order = [s.get("updatedAt", 0) for s in (_as_dict(body).get("sessions") or []) if isinstance(s, dict)]
    check("返回仍按 updatedAt 倒序", order == sorted(order, reverse=True), f"head={order[:5]}")

    # 并发写入不应把服务自己搞崩，也不该在 data/ 留下 .tmp 垃圾（tmp 名带 pid+uuid）
    st, body = req("GET", "/api/health")
    check("并发写入后服务仍健康", st == 200 and _as_dict(body).get("ok"), f"status={st}")


def sse_broadcast() -> None:
    """跨端实时同步的推送半边：A 端写会话 → B 端要收到 sessions_updated。

    离线测试全在直调合并函数，这条 SSE 通路（订阅注册、来源跳过、广播帧格式）
    从来没被端到端打过；而它一坏，多端就退化成「最多 30 秒轮询才看到对方消息」。
    """
    import threading

    got: list[str] = []

    def reader(client: str, sink: list[str]) -> threading.Thread:
        def loop():
            try:
                with urllib.request.urlopen(f"{BASE}/api/sync/stream?client={client}", timeout=25) as r:
                    deadline = time.time() + 18
                    while time.time() < deadline:
                        line = r.readline()
                        if not line:
                            break
                        s = line.decode("utf-8", "replace").strip()
                        if s.startswith("data:"):
                            sink.append(s)
                            return
            except Exception as exc:  # noqa: BLE001
                sink.append(f"ERR:{exc}")
        th = threading.Thread(target=loop, daemon=True)
        th.start()
        return th

    now_ms = int(time.time() * 1000)
    th = reader("smoke-B", got)
    time.sleep(1.0)  # 让订阅先注册上，否则广播时它还不在线
    st, _ = req("PUT", "/api/sessions?client=smoke-A", {
        "sessions": [{"id": "t-smoke-bcast", "title": "广播", "updatedAt": now_ms,
                      "history": [{"role": "user", "content": "hi"}]}], "deleted": []})
    check("A 端写入成功", st == 200, f"status={st}")
    th.join(timeout=15)
    pushed = [f for f in got if "sessions_updated" in f]
    check("B 端收到跨端变更推送", bool(pushed), f"frames={got[:2]}")

    # 回声抑制：自己写的不应再推给自己，否则每发一条消息本端就多拉一次全量会话
    echoed: list[str] = []
    th2 = reader("smoke-C", echoed)
    time.sleep(1.0)
    req("PUT", "/api/sessions?client=smoke-C", {
        "sessions": [{"id": "t-smoke-echo", "title": "回声", "updatedAt": now_ms + 5000,
                      "history": []}], "deleted": []})
    th2.join(timeout=6)
    check("本端写入不推回本端", not any("sessions_updated" in f for f in echoed),
          f"echoed={echoed[:2]}")


def main() -> int:
    if not _ensure_auth():
        return 1

    st, _ = req("GET", "/api/health")
    check("服务在线 /api/health", st == 200, f"status={st}")

    st, body = req("GET", "/pages/chat.html")
    check("聊天页可取回", st == 200 and isinstance(body, bytes) and b"<html" in body[:200].lower()
          or (isinstance(body, str) and "<html" in body[:400].lower()), f"status={st}")

    st, body = req("GET", "/api/status")
    check("/api/status 返回配置", st == 200 and isinstance(body, dict), f"status={st}")
    if isinstance(body, dict):
        bad = []

        def walk(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    cur = f"{p}.{k}" if p else k
                    if isinstance(v, str) and v and any(t in k for t in ("api_key", "access_token")) \
                       and not v.startswith("***"):
                        bad.append(cur)
                    elif isinstance(v, dict):
                        walk(v, cur)
        walk(body)
        check("/api/status 密钥一律脱敏", not bad, f"明文密钥字段: {bad}")
        # 第九轮新增：provider 探测必须给出明确结论，且任何情况下都不能把
        # /api/status 打成 500（客户端已关闭 / 事件循环关闭都曾真实触发过）
        check("provider 探测字段齐备（active_online/active_error）",
              "active_online" in body and "active_error" in body,
              f"keys={sorted(body)[:12]}")

    # 会话写入：3 条最新在前，取回应保持 3 条且顺序不倒
    sess = [{"id": f"t-smoke{i}", "title": f"冒烟{i}", "updatedAt": 1700000000000 + i * 1000,
             "history": [{"role": "user", "content": f"m{i}"}]} for i in (2, 1, 0)]
    st, body = req("PUT", "/api/sessions?client=smoke", {"sessions": sess, "deleted": []})
    check("PUT /api/sessions 成功", st == 200 and _as_dict(body).get("ok"),
          f"status={st} body={body}")
    st, body = req("GET", "/api/sessions?include_test=1")
    rows = [s for s in (_as_dict(body).get("sessions") or []) if isinstance(s, dict)]
    ids = [s.get("id") for s in rows]
    check("GET 回读到写入的会话", {"t-smoke0", "t-smoke1", "t-smoke2"} <= set(ids), f"ids={ids[:8]}")
    # 断言写成「冒烟会话之间的相对顺序」，而不是「它们必须占据列表前三名」：
    # 打真实服务时用户自己的会话 updatedAt 更新，必然排在冒烟会话前面，
    # 旧写法（ids[:3] == [t-smoke2, ...]）在真实服务上必然假失败。
    smoke_order = [s.get("updatedAt", 0) for s in rows
                   if s.get("id") in ("t-smoke0", "t-smoke1", "t-smoke2")]
    check("冒烟会话按 updatedAt 倒序返回",
          len(smoke_order) == 3 and smoke_order == sorted(smoke_order, reverse=True),
          f"smoke_order={smoke_order}")
    all_ts = [s.get("updatedAt", 0) for s in rows]
    check("整体顺序仍为最新在前", all_ts == sorted(all_ts, reverse=True), f"head={all_ts[:5]}")
    st, body = req("GET", "/api/sessions")
    ids2 = [s.get("id") for s in (_as_dict(body).get("sessions") or []) if isinstance(s, dict)]
    check("默认过滤测试会话", not any(i.startswith("t-") for i in ids2), f"ids={ids2[:8]}")

    # 附件上传 + 内容寻址去重
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
    raw, ctype = multipart({}, "smoke.png", png, "image/png")
    st, body = req("POST", "/api/upload", raw=raw, boundary=ctype)
    ok1 = st == 200 and isinstance(body, dict) and body.get("files")
    check("POST /api/upload 返回附件", bool(ok1), f"status={st} body={body}")
    url1 = body["files"][0]["url"] if ok1 else ""
    raw, ctype = multipart({}, "renamed-again.png", png, "image/png")
    st, body2 = req("POST", "/api/upload", raw=raw, boundary=ctype)
    url2 = body2["files"][0]["url"] if st == 200 and isinstance(body2, dict) and body2.get("files") else ""
    check("同内容重复上传命中去重", bool(url1) and url1 == url2, f"{url1} vs {url2}")
    if url1:
        st, _ = req("GET", url1)
        check("上传文件可回取", st == 200, f"status={st}")

    exe_raw, exe_ctype = multipart({}, "e.exe", b"MZ", "application/octet-stream")
    st, body = req("POST", "/api/upload", raw=exe_raw, boundary=exe_ctype)
    check("危险扩展名被拒", st == 400, f"status={st}")

    # 记忆列表：非配置角色也应给出明确状态而非 500
    st, body = req("GET", "/api/roles/memories?role=dashuai")
    check("记忆列表可读", st in (200, 400, 404), f"status={st} body={str(body)[:120]}")
    st, body = req("GET", "/api/roles/memories?role=../../etc/passwd")
    check("非法角色名被拒而非 500", st in (400, 404), f"status={st}")

    st, body = req("GET", "/api/story/status")
    check("剧情状态端点有响应", st in (200, 404), f"status={st}")

    st, body = req("GET", "/api/location")
    check("已下线的 /api/location 返回 404", st == 404, f"status={st}")

    # 流式聊天（隔离实例为桩实现；真实服务会真的调用云端 LLM，因此默认跳过）
    if os.getenv("SMOKE_REAL_CHAT") == "1":
        try:
            with _opener.open(urllib.request.Request(
                BASE + "/api/chat",
                data=json.dumps({"session_id": "t-smoke-chat", "message": "冒烟"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST"), timeout=60) as resp:
                chunk = resp.read(4096).decode("utf-8", "replace")
            check("流式聊天返回 SSE 帧", resp.status == 200 and "data:" in chunk, f"body={chunk[:120]}")
        except Exception as exc:  # noqa: BLE001
            check("流式聊天返回 SSE 帧", False, str(exc)[:160])
    else:
        print("[SKIP] 流式聊天真实调用（打真实服务会烧 token；设 SMOKE_REAL_CHAT=1 强制跑）")

    concurrent_session_writes()

    print("\n" + "=" * 52)
    print(f"运行时冒烟 @ {BASE}  失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
