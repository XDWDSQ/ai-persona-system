# -*- coding: utf-8 -*-
"""server_pkg.sessions_merge 多端会话合并单元测试。

运行：python test_sessions_merge.py
覆盖：旧快照防覆盖 / 删除生效 / 分歧消息级合并 / 等长分叉 / 畸形 history 容错 / 墓碑
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_pkg.sessions_merge import _merge_sessions

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAIL += 1
    print(f"[{mark}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def msg(role, content):
    return {"role": role, "content": content, "style": "", "attachments": []}


def sess(sid, hist, updated_at, title=None):
    return {"id": sid, "title": title or "标题", "history": hist,
            "updatedAt": updated_at, "pinned": False, "manualTitle": True}


def find(merged, sid):
    return next((s for s in merged if s["id"] == sid), None)


def test_prefix_old_snapshot_protected():
    # 手机持 2 条旧副本、时钟超前 10 分钟：不得覆盖服务端 3 条新副本
    now = time.time() * 1000
    h3 = [msg("user", "a"), msg("assistant", "b"), msg("user", "c")]
    h2 = h3[:2]
    current = [sess("s1", h3, now)]
    incoming = [sess("s1", h2, now + 600000)]
    out = _merge_sessions(current, incoming, {})
    check("严格前缀+时钟超前：保留消息更多的 current", len(find(out, "s1")["history"]) == 3)


def test_delete_last_message_takes_effect():
    # 删除最后一条的快速操作（60s 内）：即便 incoming 是前缀也生效
    now = time.time() * 1000
    h2 = [msg("user", "a"), msg("assistant", "b")]
    current = [sess("s1", h2 + [msg("user", "c")], now)]
    incoming = [sess("s1", h2, now + 1000)]
    out = _merge_sessions(current, incoming, {})
    check("前缀+60s 内：删除最后一条生效", len(find(out, "s1")["history"]) == 2)


def test_subsequence_delete_middle():
    now = time.time() * 1000
    full = [msg("user", "a"), msg("assistant", "b"), msg("user", "c")]
    deleted = [full[0], full[2]]  # 删掉中间一条
    current = [sess("s1", full, now)]
    incoming = [sess("s1", deleted, now + 1000)]
    out = _merge_sessions(current, incoming, {})
    check("有序子序列：删除中间消息生效", len(find(out, "s1")["history"]) == 2)


def test_diverge_union_keeps_both():
    now = time.time() * 1000
    base = [msg("user", "a"), msg("assistant", "b")]
    h_local = base + [msg("user", "本地话"), msg("assistant", "本地回复")]
    h_remote = base + [msg("user", "远程话"), msg("assistant", "远程回复")]
    current = [sess("s1", h_local, now + 1000)]
    incoming = [sess("s1", h_remote, now)]
    out = _merge_sessions(current, incoming, {})
    texts = [m["content"] for m in find(out, "s1")["history"]]
    check("不等长分歧：消息级合并双方都不丢",
          "本地话" in texts and "远程话" in texts and "本地回复" in texts and "远程回复" in texts,
          str(texts))


def test_equal_length_diverge():
    # 两端几乎同时各发一条：长度相同、仅末尾分叉——必须走合并而非时间新者胜
    now = time.time() * 1000
    base = [msg("user", "a"), msg("assistant", "b")]
    h_a = base + [msg("user", "A 端消息")]
    h_b = base + [msg("user", "B 端消息")]
    current = [sess("s1", h_a, now + 2000)]
    incoming = [sess("s1", h_b, now + 1000)]
    out = _merge_sessions(current, incoming, {})
    texts = [m["content"] for m in find(out, "s1")["history"]]
    check("等长分叉：两条消息都保留（不被时间新者整段覆盖）",
          "A 端消息" in texts and "B 端消息" in texts, str(texts))
    # 等长全等：正常演进，不重复不报错
    out2 = _merge_sessions([sess("s1", h_a, now)], [sess("s1", h_a, now + 1000)], {})
    check("等长全等：视作同一版本不重复",
          len([m for m in find(out2, "s1")["history"] if m["content"] == "A 端消息"]) == 1)


def test_malformed_history_not_dict():
    now = time.time() * 1000
    # 磁盘损坏/旧客户端写入：history 是 dict 而非 list，合并不得抛 AttributeError
    current = [sess("s1", [msg("user", "a")], now)]
    bad = {"id": "s1", "title": "坏", "history": {"oops": True}, "updatedAt": now + 1000}
    try:
        out = _merge_sessions(current, [bad], {})
    except Exception as exc:  # noqa: BLE001
        check("畸形 history(dict)：不抛异常", False, repr(exc))
        return
    check("畸形 history(dict)：归一为空列表且不丢会话", find(out, "s1") is not None)


def test_malformed_history_non_dict_items():
    """第九轮回归：history 是 list、但**元素不是 dict**。

    旧实现在 _msg_key 里直接 m.get(...)，非 dict 元素抛 AttributeError →
    PUT /api/sessions 500，而且是**粘性**的：新会话带畸形 history 会被原样落盘，
    此后任何一端正常 PUT 这个会话都崩在同一行，正常写入路径永远无法自愈
    （只能手改 sessions.json）。上面那条只覆盖了"history 是 dict"。
    """
    now = time.time() * 1000
    # 1) 畸形元素混在 list 里：前缀/子序列/并集三条分支都不能崩
    try:
        out = _merge_sessions(
            [sess("s1", [msg("user", "a"), msg("assistant", "b")], now)],
            [{"id": "s1", "title": "坏", "history": ["x", None, 42], "updatedAt": now + 1000}],
            {},
        )
        check("畸形 history(list 含非 dict)：不抛异常", find(out, "s1") is not None)
    except Exception as exc:  # noqa: BLE001
        check("畸形 history(list 含非 dict)：不抛异常", False, repr(exc))
        return

    # 2) 粘性场景：畸形会话已落盘后，正常一端再 PUT 同一会话仍必须成功
    try:
        poisoned = [{"id": "s2", "title": "毒", "history": ["x"], "updatedAt": now}]
        out1 = _merge_sessions([], poisoned, {})
        out2 = _merge_sessions(out1, [sess("s2", [msg("user", "新")], now + 5000)], {})
        check("已落盘的畸形会话可被正常写入路径自愈",
              find(out2, "s2") is not None)
    except Exception as exc:  # noqa: BLE001
        check("已落盘的畸形会话可被正常写入路径自愈", False, repr(exc))

    # 3) 嵌套 list 元素同样走 _msg_key 的类型防御（入口兜底）
    try:
        _merge_sessions([{"id": "s3", "history": [{"role": "user", "content": "ok"}]}],
                        [{"id": "s3", "history": [["nested"], {"role": "user", "content": "ok"}]}], {})
        check("嵌套 list 元素也被兜住", True)
    except Exception as exc:  # noqa: BLE001
        check("嵌套 list 元素也被兜住", False, repr(exc))


def test_tombstone_filters_deleted():
    now = time.time() * 1000
    current = []
    incoming = [sess("s1", [msg("user", "a")], now - 1000)]
    out = _merge_sessions(current, incoming, {"s1": now})
    check("最后更新早于墓碑时间：会话保持删除", find(out, "s1") is None)
    # 更新时间新于墓碑 → 复活
    incoming2 = [sess("s1", [msg("user", "a")], now + 2000)]
    tombs = {"s1": now}
    out2 = _merge_sessions([], incoming2, tombs)
    check("更新时间新于墓碑：会话复活并弹出墓碑",
          find(out2, "s1") is not None and "s1" not in tombs)


def test_placeholder_dropped():
    now = time.time() * 1000
    placeholder = {"id": "p1", "title": "新对话", "history": [],
                   "updatedAt": now, "pinned": False, "manualTitle": False}
    out = _merge_sessions([], [placeholder], {})
    check("空占位会话不入库", find(out, "p1") is None)


def main():
    tests = [
        test_prefix_old_snapshot_protected,
        test_delete_last_message_takes_effect,
        test_subsequence_delete_middle,
        test_diverge_union_keeps_both,
        test_equal_length_diverge,
        test_malformed_history_not_dict,
        test_malformed_history_non_dict_items,
        test_tombstone_filters_deleted,
        test_placeholder_dropped,
    ]
    for t in tests:
        t()
    print(f"\n{'=' * 50}\n共 {len(tests)} 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
