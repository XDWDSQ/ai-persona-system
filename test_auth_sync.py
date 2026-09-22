# -*- coding: utf-8 -*-
"""会话入库截断方向 + 登录 cookie 派生/Secure 判定回归。
两条都是第七轮修掉的真问题：
  1. PUT /api/sessions 用 [-500:] 截断，而会话数组两端都是「最新在前」，
     于是被丢掉的是最新和置顶的会话 —— 用户正在聊的那条会凭空消失。
  2. cookie 值就是访问口令本身，且隧道部署下 Secure 永远没设上。
运行：python test_auth_sync.py
"""
import copy
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        self.tmp = Path(tempfile.mkdtemp(prefix="auth_sync_"))
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
def test_ingest_keeps_newest(client):
    """601 个会话按最新在前提交：必须留下最新的 500 个。"""
    with sandbox():
        total = 501
        sessions = [_sess(i) for i in range(total - 1, -1, -1)]  # s500 在最前（最新）
        r = client.put("/api/sessions", json={"sessions": sessions, "deleted": []})
        check("PUT 会话成功", r.status_code == 200, f"status={r.status_code}")
        stored = {s["id"] for s in json.loads(server.SESSIONS_PATH.read_text(encoding="utf-8"))["sessions"]}
        check("保留的是最新的 500 个会话", "s500" in stored and "s1" in stored,
              f"count={len(stored)}")
        check("被截断的是最旧的少数会话", "s0" not in stored,
              "s0 仍在 -> 截断方向又反了")
        check("入库总数受限", len(stored) <= 500, f"count={len(stored)}")
def test_ingest_order_agnostic(client):
    """调用方乱序提交（最旧在前）也必须留下最新的，不依赖数组顺序。"""
    with sandbox():
        sessions = [_sess(i) for i in range(501)]  # s0 在前 = 最旧在前
        r = client.put("/api/sessions", json={"sessions": sessions, "deleted": []})
        check("乱序 PUT 成功", r.status_code == 200, f"status={r.status_code}")
        stored = {s["id"] for s in json.loads(server.SESSIONS_PATH.read_text(encoding="utf-8"))["sessions"]}
        check("乱序入参同样保留最新", "s500" in stored and "s0" not in stored,
              f"s500={'s500' in stored} s0={'s0' in stored}")
def test_cookie_is_not_the_token():
    tok = "s3cr3t-access-token-value"
    val = server._cookie_value(tok)
    check("cookie 值不等于口令本身", val != tok)
    check("cookie 值可复现（无随机盐）", val == server._cookie_value(tok))
    check("cookie 值不泄露口令子串", tok[:6] not in val)
    check("不同口令派生出不同 cookie", val != server._cookie_value(tok + "x"))
    req_ok = SimpleNamespace(
        headers={"cookie": f"{server._COOKIE_NAME}={val}"}, cookies={server._COOKIE_NAME: val})
    req_raw = SimpleNamespace(
        headers={"cookie": f"{server._COOKIE_NAME}={tok}"}, cookies={server._COOKIE_NAME: tok})
    check("派生 cookie 通过校验", server._auth_ok(req_ok, tok))
    check("旧版裸口令 cookie 不再被接受", not server._auth_ok(req_raw, tok))
    check("Bearer 口令仍然有效", server._auth_ok(
        SimpleNamespace(headers={"authorization": f"Bearer {tok}"}, cookies={}), tok))
def _req(scheme, peer, xfp=None):
    headers = {} if xfp is None else {"x-forwarded-proto": xfp}
    return SimpleNamespace(url=SimpleNamespace(scheme=scheme),
                           client=SimpleNamespace(host=peer), headers=headers)
def test_cookie_secure_behind_tunnel():
    check("本机直连 http 不加 Secure",
          not server._cookie_secure(_req("http", "127.0.0.1")))
    check("ngrok 回源 + XFP=https 必须加 Secure",
          server._cookie_secure(_req("http", "127.0.0.1", "https")))
    check("公网直连时自塞的 XFP 不算数",
          not server._cookie_secure(_req("http", "8.8.8.8", "https")))
    check("隧道内http 降级不加 Secure",
          not server._cookie_secure(_req("http", "127.0.0.1", "http")))
if __name__ == "__main__":
    from fastapi.testclient import TestClient  # noqa: E402
    with TestClient(server.app) as client:
        print("\n--- test_ingest_keeps_newest ---")
        test_ingest_keeps_newest(client)
        print("\n--- test_ingest_order_agnostic ---")
        test_ingest_order_agnostic(client)
    print("\n--- test_cookie_is_not_the_token ---")
    test_cookie_is_not_the_token()
    print("\n--- test_cookie_secure_behind_tunnel ---")
    test_cookie_secure_behind_tunnel()
    print("\n" + "=" * 50)
    print(f"会话截断与 cookie 测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
