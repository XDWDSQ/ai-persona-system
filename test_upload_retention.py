# -*- coding: utf-8 -*-
"""附件回收策略回归：只删「过期且无会话引用」的孤儿，不删历史消息仍在用的附件。

修复前的行为：按 atime/mtime 超过 7 天无条件删除，而浏览器/SW 命中缓存时服务端
atime 根本不刷新 —— 老照片会被静默删掉，历史消息永久 404。

运行：python test_upload_retention.py
"""
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


def _touch(path: Path, age_days: float) -> None:
    ts = time.time() - age_days * 86400
    os.utime(path, (ts, ts))


def _att(name: str, age_days: float = 30.0) -> Path:
    p = server.UPLOAD_DIR / name
    p.write_bytes(b"x" * 8)
    _touch(p, age_days)
    return p


class sandbox:
    """把 UPLOAD_DIR / SESSIONS_PATH 重定向到临时目录，不碰真数据。"""

    def __enter__(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="att_retention_"))
        self._up = self._tmp / "uploads"
        self._up.mkdir()
        self._sessions = self._tmp / "sessions.json"
        self._old_up = server.UPLOAD_DIR
        self._old_sessions = server.SESSIONS_PATH
        server.UPLOAD_DIR = self._up
        server.SESSIONS_PATH = self._sessions
        return self

    def __exit__(self, *exc):
        server.UPLOAD_DIR = self._old_up
        server.SESSIONS_PATH = self._old_sessions
        return False

    def sessions(self, urls: list[str]):
        self._sessions.write_text(
            json.dumps({"sessions": [{"id": "s1", "history": [
                {"role": "user", "content": "", "attachments": [{"url": u} for u in urls]}
            ]}]}, ensure_ascii=False),
            encoding="utf-8",
        )


def test_reference_wins_over_age():
    with sandbox() as sb:
        sb.sessions(["/uploads/att_aaaaaaaaaaaa.png"])
        keep = _att("att_aaaaaaaaaaaa.png")          # 30 天前，但仍被引用
        gone = _att("att_bbbbbbbbbbbb.png")          # 30 天前，无人引用
        fresh = _att("att_cccccccccccc.png", age_days=1)  # 未过保留期
        other = _att("notes.txt")                    # 非 att_ 前缀
        expired = set(p.name for p in server._scan_expired_uploads(time.time()))
        check("被会话引用的老附件不删", "att_aaaaaaaaaaaa.png" not in expired and keep.is_file())
        check("无引用的老附件（孤儿）删除", "att_bbbbbbbbbbbb.png" in expired)
        check("保留期内的附件不删", "att_cccccccccccc.png" not in expired and fresh.is_file())
        check("非 att_ 文件不参与回收", "notes.txt" not in expired and other.is_file())


def test_url_inside_message_content_counts():
    with sandbox() as sb:
        sb._sessions.write_text(
            json.dumps({"sessions": [{"history": [
                {"role": "assistant", "content": "你看这张图 /uploads/att_dddddddddddd.jpg 好看吗"}
            ]}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        expired = set(p.name for p in server._scan_expired_uploads(time.time()))
        check("正文里直贴的 uploads 链接也算引用", "att_dddddddddddd.jpg" not in expired)


def test_inflight_leftovers_reclaimed_early():
    with sandbox() as sb:
        sb.sessions([])
        old = _att("att_inflight_deadbeef1234.png", age_days=2)
        new = _att("att_inflight_cafebabef00d.png", age_days=0.01)
        expired = set(p.name for p in server._scan_expired_uploads(time.time()))
        check("崩溃残留的 inflight 文件按小时回收", "att_inflight_deadbeef1234.png" in expired)
        check("正在上传的 inflight 文件不删", "att_inflight_cafebabef00d.png" not in expired
              and new.is_file())


def test_unreadable_sessions_skips_sweep():
    with sandbox() as sb:
        _att("att_eeeeeeeeeeee.png")
        sb._sessions.write_text("{ broken", encoding="utf-8")
        sb._sessions.unlink()  # 存储缺失：无法证明无引用
        expired = server._scan_expired_uploads(time.time())
        check("读不到会话存储时整轮跳过删除", expired == [], f"expired={expired}")
        check("跳过时文件仍在盘上", (server.UPLOAD_DIR / "att_eeeeeeeeeeee.png").is_file())


def test_referenced_names_parse():
    with sandbox() as sb:
        sb.sessions(["/uploads/att_0123456789ab.png", "/uploads/att_feedfacefeed.wav"])
        names = server._referenced_upload_names()
        check("引用集解析出文件名", names == {"att_0123456789ab.png", "att_feedfacefeed.wav"},
              f"names={names}")


if __name__ == "__main__":
    for fn in (test_reference_wins_over_age,
               test_url_inside_message_content_counts,
               test_inflight_leftovers_reclaimed_early,
               test_unreadable_sessions_skips_sweep,
               test_referenced_names_parse):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print("\n" + "=" * 50)
    print(f"附件回收测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
