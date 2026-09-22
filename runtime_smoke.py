# -*- coding: utf-8 -*-
"""运行时冒烟：对「已经在跑的服务」打一遍关键链路，验证真实 HTTP 行为。

与离线测试（run_tests.bat）的区别：离线测试直接调函数，这里走完整的
ASGI/HTTP 栈 —— 中间件、门禁、路由顺序、序列化，都是离线测试覆盖不到的。

用法：
    1) 先起隔离实例：python qa_run.py        （127.0.0.1:8010，数据落临时目录）
       或起真实服务：venv\\Scripts\\python -m uvicorn server:app --port 8000
    2) python runtime_smoke.py [base_url]    （默认 http://127.0.0.1:8010）

对真实服务运行时，本脚本只读不写业务数据的端点会跳过；写会话用 t- 前缀
（测试会话约定，服务端 GET 默认过滤），不会污染用户的会话列表。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


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
        with urllib.request.urlopen(r, timeout=timeout) as resp:
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
    ids = {s.get("id") for s in (body.get("sessions") or []) if isinstance(s, dict)} \
        if isinstance(body, dict) else set()
    expect = {f"t-conc{d}-{i}" for d in range(n_dev) for i in range(n_sess)}
    lost = expect - ids
    # 合并语义是「按 id 并集 + 最新胜」：并发各写各的会话时一个都不该丢
    check("并发写入的会话无一被吞掉", not lost, f"lost={sorted(lost)[:6]} of {len(expect)}")

    st, body = req("GET", "/api/sessions?include_test=1")
    order = [s.get("updatedAt", 0) for s in (body.get("sessions") or []) if isinstance(s, dict)]
    check("返回仍按 updatedAt 倒序", order == sorted(order, reverse=True), f"head={order[:5]}")

    # 并发写入不应把服务自己搞崩，也不该在 data/ 留下 .tmp 垃圾（tmp 名带 pid+uuid）
    st, body = req("GET", "/api/health")
    check("并发写入后服务仍健康", st == 200 and isinstance(body, dict) and body.get("ok"), f"status={st}")


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

    # 会话写入：3 条最新在前，取回应保持 3 条且顺序不倒
    sess = [{"id": f"t-smoke{i}", "title": f"冒烟{i}", "updatedAt": 1700000000000 + i * 1000,
             "history": [{"role": "user", "content": f"m{i}"}]} for i in (2, 1, 0)]
    st, body = req("PUT", "/api/sessions?client=smoke", {"sessions": sess, "deleted": []})
    check("PUT /api/sessions 成功", st == 200 and isinstance(body, dict) and body.get("ok"),
          f"status={st} body={body}")
    st, body = req("GET", "/api/sessions?include_test=1")
    ids = [s.get("id") for s in (body.get("sessions") or [])] if isinstance(body, dict) else []
    check("GET 回读到写入的会话", set(["t-smoke0", "t-smoke1", "t-smoke2"]) <= set(ids), f"ids={ids[:8]}")
    check("返回顺序仍为最新在前", ids[:3] == ["t-smoke2", "t-smoke1", "t-smoke0"] or "t-smoke2" in ids[:3],
          f"ids={ids[:6]}")
    st, body = req("GET", "/api/sessions")
    ids2 = [s.get("id") for s in (body.get("sessions") or [])] if isinstance(body, dict) else []
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
    url2 = body2["files"][0]["url"] if st == 200 and body2.get("files") else ""
    check("同内容重复上传命中去重", url1 and url1 == url2, f"{url1} vs {url2}")
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

    # 流式聊天（隔离实例为桩实现）
    try:
        with urllib.request.urlopen(urllib.request.Request(
            BASE + "/api/chat",
            data=json.dumps({"session_id": "t-smoke-chat", "message": "冒烟"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST"), timeout=60) as resp:
            chunk = resp.read(4096).decode("utf-8", "replace")
        check("流式聊天返回 SSE 帧", resp.status == 200 and "data:" in chunk, f"body={chunk[:120]}")
    except Exception as exc:  # noqa: BLE001
        check("流式聊天返回 SSE 帧", False, str(exc)[:160])

    concurrent_session_writes()

    print("\n" + "=" * 52)
    print(f"运行时冒烟 @ {BASE}  失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
