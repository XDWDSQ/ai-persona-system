# -*- coding: utf-8 -*-
"""清理测试会话：把 id 以 t- 开头的测试会话从目标后端删除，只留日志记录。

测试会话约定：测试脚本/验证脚本写入的会话 id 一律以 t- 开头
（真实 App 生成的 id 恒为 's' + base36，不会冲突；服务端 GET 默认已过滤，
前端永远看不到它们）。本脚本在测试结束后把它们以墓碑方式删除：

用法：
  python cleanup_test_sessions.py [--base http://127.0.0.1:8000] [--token TOKEN]

- 默认目标 127.0.0.1:8000（真实服务）；测隔离实例传 --base http://127.0.0.1:8010。
- token 用 --token 或环境变量 ACCESS_TOKEN 提供（不写死凭据）；服务端未配口令可不传。
- 删除走 PUT /api/sessions 的墓碑机制（服务端正常合并/广播/缓存路径），
  绝不直接改文件，避免与运行中的服务进程冲突。
- 删除明细追加到 <数据目录>/test_cleanup.log（时间、目标、id/标题/消息数），
  无测试会话时也记录一条运行日志。

安全说明（本工具要连本机/内网后端，私网地址是合法目标，因此不封私网）：
- 仅允许 http/https，拒绝 URL 内嵌用户名/密码；
- 用 http.client 直连（天然不跟随重定向，Authorization 头不会泄露给第三方主机）；
- 主机名先解析一次并固定解析出的 IP 建连（防 DNS rebinding），
  TLS 的 SNI/证书校验仍按原始主机名进行。
"""
import argparse
import gzip
import http.client
import json
import os
import socket
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent
# 日志写进目标服务的数据目录（支持 AI_DATA_DIR 指向隔离实例的 data-test/）
DATA_ROOT = Path(os.getenv("AI_DATA_DIR") or (BASE_DIR / "data"))
LOG_PATH = DATA_ROOT / "test_cleanup.log"
TEST_PREFIX = "t-"
_REQUEST_TIMEOUT = 60


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """把连接固定到解析好的 IP 上（防 DNS rebinding）；TLS 仍按原始主机名。"""

    def __init__(self, host: str, port: int, pin_ip: str, timeout: float):
        self._pin_ip = pin_ip
        super().__init__(host, port, timeout=timeout)

    def connect(self):  # noqa: D102
        self.sock = socket.create_connection((self._pin_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS 版：同样固定 IP 建连，TLS 的 SNI/证书校验按原始主机名进行。"""

    def __init__(self, host: str, port: int, pin_ip: str, timeout: float):
        self._pin_ip = pin_ip
        super().__init__(host, port, timeout=timeout)

    def connect(self):  # noqa: D102
        self.sock = socket.create_connection((self._pin_ip, self.port), self.timeout)
        if self._tunnel_host:
            raise RuntimeError("本工具不支持 HTTP 隧道连接")
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _request(base: str, method: str, path: str, payload=None, token: str = ""):
    """发 HTTP 请求并解析 JSON 响应；失败抛 RuntimeError 带服务端错误信息。

    流程：协议/主机名/凭据校验 → DNS 解析一次 → 固定 IP 建连 → 发送。
    http.client 不自动跟随重定向，3xx 会被下面的状态检查拦下并报错。"""
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RuntimeError(f"--base 必须是 http/https 地址: {base}")
    if u.username or u.password:
        raise RuntimeError(f"--base 不允许内嵌用户名/密码: {base}")
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        raise RuntimeError(f"--base 端口非法: {base}") from None

    # 解析一次并固定 IP（防 DNS rebinding）；IPv6 地址取 [0] 亦可
    try:
        infos = socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise RuntimeError(f"无法解析主机名 {u.hostname}: {e}") from e
    pin_ip = infos[0][4][0]

    cls = _PinnedHTTPSConnection if u.scheme == "https" else _PinnedHTTPConnection
    conn = cls(u.hostname, port, pin_ip=pin_ip, timeout=_REQUEST_TIMEOUT)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        raise RuntimeError(f"{method} {path} 网络错误: {e}（服务没起？或 --base 填错？）") from e
    finally:
        conn.close()

    if not 200 <= resp.status < 300:
        detail = raw[:300].decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {resp.status} {method} {path}: {detail[:200]}")
    # 服务端 gzip 中间件会压缩较大 JSON（不看 Accept-Encoding），客户端解压
    if (resp.getheader("Content-Encoding") or "").strip().lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError as e:
            raise RuntimeError(f"{method} {path} gzip 解压失败: {e}") from e
    return json.loads(raw.decode("utf-8"))


def _log_line(base: str, removed: list, kept: int, note: str = "") -> None:
    """追加一条清理记录（JSON 行）到 test_cleanup.log。"""
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "base": base,
        "removed_count": len(removed),
        "removed": removed,
        "kept_count": kept,
        "note": note,
    }
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[!] 日志写入失败（不影响删除结果）: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 t- 前缀测试会话（墓碑删除，只留日志）")
    ap.add_argument("--base", default=os.getenv("VERIFY_BASE", "http://127.0.0.1:8000"),
                    help="目标后端地址（默认 127.0.0.1:8000；隔离实例用 8010）")
    ap.add_argument("--token", default="", help="访问口令（默认取环境变量 ACCESS_TOKEN）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将删除的测试会话，不实际删除")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    token = args.token.strip() or os.getenv("ACCESS_TOKEN", "").strip()

    # 1) 拉全量会话（include_test=1 才能看到被默认过滤的测试会话）
    try:
        data = _request(base, "GET", "/api/sessions?include_test=1", token=token)
    except RuntimeError as e:
        print(f"[X] 拉取会话失败: {e}", file=sys.stderr)
        return 1
    sessions = data.get("sessions") or []
    targets = [s for s in sessions if str((s or {}).get("id", "")).startswith(TEST_PREFIX)]
    kept = len(sessions) - len(targets)

    if not targets:
        print(f"[OK] 没有测试会话（共 {len(sessions)} 条正常会话），仅记录日志")
        _log_line(base, [], kept, note="no test sessions found")
        return 0

    print(f"待清理测试会话 {len(targets)} 条：")
    for s in targets:
        sid = s.get("id", "")
        title = s.get("title", "") or "（无标题）"
        n = len(s.get("history") or [])
        print(f"  - {sid}  {title}  ({n} 条消息)")

    if args.dry_run:
        print("[DRY-RUN] 未实际删除。")
        return 0

    # 2) 墓碑删除：走正常合并路径（广播、缓存、备份保险全部生效）
    now_ms = int(time.time() * 1000)
    payload = {
        "sessions": [],
        "deleted": [{"id": s["id"], "ts": now_ms} for s in targets],
    }
    try:
        _request(base, "PUT", "/api/sessions", payload, token=token)
    except RuntimeError as e:
        print(f"[X] 删除失败: {e}", file=sys.stderr)
        return 1

    # 3) 复核：测试会话应已从服务端消失
    try:
        after = _request(base, "GET", "/api/sessions?include_test=1", token=token)
        remain = [s for s in (after.get("sessions") or [])
                  if str((s or {}).get("id", "")).startswith(TEST_PREFIX)]
    except RuntimeError as e:
        print(f"[!] 删除已提交但复核失败: {e}", file=sys.stderr)
        remain = []

    if remain:
        print(f"[X] 仍有 {len(remain)} 条测试会话残留: "
              f"{[s.get('id') for s in remain]}", file=sys.stderr)
        _log_line(base, [{"id": s.get("id"), "title": s.get("title"), "msgs": len(s.get("history") or [])}
                         for s in targets], kept, note="verify failed, some remain")
        return 1

    removed = [{"id": s.get("id"), "title": s.get("title"), "msgs": len(s.get("history") or [])}
               for s in targets]
    _log_line(base, removed, kept)
    print(f"[OK] 已删除 {len(removed)} 条测试会话，保留 {kept} 条正常会话；"
          f"明细已记入 {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
