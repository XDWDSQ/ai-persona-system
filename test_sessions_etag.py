# -*- coding: utf-8 -*-
"""会话同步「加量不加价」三件套回归：
  1. GET /api/sessions 带 ETag；客户端 If-None-Match 命中返回 304 空体，
     内容变化（PUT 后）自动换 ETag，不会拿到旧数据。
  2. include_test 两种过滤形态 ETag 互不串扰（同一 fp 不同变体）。
  3. GET /api/sessions/fingerprint 只回指纹，值与全量响应的 fp 一致，
     PUT 后随之变化 —— 前端轮询据此跳过全量拉取。
运行：python test_sessions_etag.py
"""
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os as _os
_os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")  # 直跑本文件也切断后台外部请求（烧 token）
import server  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


class sandbox:
    """会话存储 + 缓存 + 免密门禁全部就地替换，不碰 data/ 真数据。"""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sessions_etag_"))
        self.path = self.tmp / "sessions.json"
        self._path = server.SESSIONS_PATH
        self._cache = copy.deepcopy(server._sess_cache)
        self._tok = server._access_token
        server.SESSIONS_PATH = self.path
        server._sess_cache.clear()
        server._access_token = lambda: None
        return self

    def __exit__(self, *exc):
        server.SESSIONS_PATH = self._path
        server._sess_cache.clear()
        server._sess_cache.update(self._cache)
        server._access_token = self._tok
        return False


def _sess(i: int) -> dict:
    return {"id": f"s{i}", "title": f"会话{i}", "updatedAt": 1700000000000 + i * 1000,
            "history": [{"role": "user", "content": f"第{i}条"}]}


def test_etag_roundtrip(client):
    with sandbox():
        # 空库：无 fp 无 ETag，普通 200
        r = client.get("/api/sessions")
        check("空库 GET 返回 200", r.status_code == 200, f"status={r.status_code}")
        check("空库不带 ETag", "etag" not in {k.lower() for k in r.headers.keys()})

        r = client.put("/api/sessions", json={"sessions": [_sess(0), _sess(1)], "deleted": []})
        check("PUT 两条会话成功", r.status_code == 200, f"status={r.status_code}")
        put_fp = r.json().get("fp", "")
        check("PUT 响应带 fp", bool(put_fp))

        r1 = client.get("/api/sessions")
        etag = r1.headers.get("etag", "")
        check("GET 带 ETag", bool(etag), f"etag={etag!r}")
        check("ETag 与 PUT fp 同源", put_fp in etag, f"fp={put_fp!r} etag={etag!r}")
        check("GET 响应 fp 与 PUT 一致", r1.json().get("fp") == put_fp)

        # 内容未变：If-None-Match 命中 → 304 空体
        r2 = client.get("/api/sessions", headers={"If-None-Match": etag})
        check("未变内容命中 304", r2.status_code == 304, f"status={r2.status_code}")
        check("304 无响应体", not (r2.content or b"").strip())

        # 内容变化：PUT 新会话 → fp 变 → 旧 If-None-Match 失效，拿得到新数据
        r = client.put("/api/sessions", json={"sessions": [_sess(0), _sess(1), _sess(2)], "deleted": []})
        check("PUT 第三条会话成功", r.status_code == 200)
        new_fp = r.json().get("fp", "")
        check("内容变化后 fp 随之变化", bool(new_fp) and new_fp != put_fp)
        r3 = client.get("/api/sessions", headers={"If-None-Match": etag})
        check("内容变化后 304 失效返回 200", r3.status_code == 200, f"status={r3.status_code}")
        ids = {s["id"] for s in r3.json().get("sessions", [])}
        check("新数据完整返回", "s2" in ids, f"ids={sorted(ids)}")
        check("新响应 ETag 已更新", r3.headers.get("etag", "") not in ("", etag))


def test_etag_include_test_variant(client):
    with sandbox():
        client.put("/api/sessions", json={"sessions": [_sess(0)], "deleted": []})
        r_norm = client.get("/api/sessions")
        r_test = client.get("/api/sessions?include_test=1")
        e_norm = r_norm.headers.get("etag", "")
        e_test = r_test.headers.get("etag", "")
        check("默认与 include_test 均带 ETag", bool(e_norm) and bool(e_test))
        check("两种变体 ETag 互不相同", e_norm != e_test,
              "同一 fp 两种过滤形态共享 ETag 会互串 304")
        # 各自与自己协商都命中
        check("默认变体自协商 304",
              client.get("/api/sessions", headers={"If-None-Match": e_norm}).status_code == 304)
        check("include_test 变体自协商 304",
              client.get("/api/sessions?include_test=1",
                         headers={"If-None-Match": e_test}).status_code == 304)


def test_fingerprint_endpoint(client):
    with sandbox():
        client.put("/api/sessions", json={"sessions": [_sess(0)], "deleted": []})
        full_fp = client.get("/api/sessions").json().get("fp", "")
        fp_resp = client.get("/api/sessions/fingerprint")
        check("指纹端点 200", fp_resp.status_code == 200)
        check("指纹端点只回 fp", fp_resp.json().get("fp") == full_fp and full_fp,
              f"full={full_fp!r} probe={fp_resp.json()!r}")
        # 再 PUT 一次：指纹必须变化（前端据此决定拉全量）
        client.put("/api/sessions", json={"sessions": [_sess(0), _sess(1)], "deleted": []})
        fp2 = client.get("/api/sessions/fingerprint").json().get("fp", "")
        check("写入变化后指纹变化", bool(fp2) and fp2 != full_fp)


if __name__ == "__main__":
    from fastapi.testclient import TestClient  # noqa: E402
    with TestClient(server.app) as client:
        print("\n--- test_etag_roundtrip ---")
        test_etag_roundtrip(client)
        print("\n--- test_etag_include_test_variant ---")
        test_etag_include_test_variant(client)
        print("\n--- test_fingerprint_endpoint ---")
        test_fingerprint_endpoint(client)
    print("\n" + "=" * 50)
    print(f"会话 ETag/指纹 测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
