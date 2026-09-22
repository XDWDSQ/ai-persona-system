# -*- coding: utf-8 -*-
"""会话合并的两处数据丢失回归：慢时钟端的新增被吞、过期墓碑导致已删会话复活。

运行：python test_merge_resurrection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_pkg.sessions_merge import (  # noqa: E402
    _TOMBSTONE_MAX_COUNT, _merge_sessions, _norm_tombstones,
)

_FAIL = 0
DAY = 86400000
NOW = 1_790_000_000_000


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def _role_of(text: str) -> str:
    """角色必须由内容稳定决定。

    之前按列表下标分配 role，会让同一条消息在两个端上拿到不同角色
    （["a1","b1","c1"] 里的 c1 是 user，删剩 ["a1","c1"] 后 c1 变 assistant），
    于是子序列判定失败、误入并集分支，测出 ['a1','c1','b1','c1'] 这种假象。
    真实消息的 role 是跟着消息走的，不会因为邻居被删而改变。
    """
    return "assistant" if text[:1] in ("b", "d") else "user"


def sess(sid, texts, updated_at, **extra):
    d = {"id": sid, "title": sid, "updatedAt": updated_at,
         "history": [{"role": _role_of(t), "content": t} for t in texts]}
    d.update(extra)
    return d


def texts(session):
    return [m["content"] for m in (session or {}).get("history", [])]


def only(merged, sid):
    return next((s for s in merged if s.get("id") == sid), None)


def test_slow_clock_append_is_not_lost():
    """手机休眠后时钟落后：它只是多聊了两条，不该被判成旧快照整段丢弃。"""
    file_s = sess("s1", ["a1", "b1"], NOW + 10 * DAY)          # 服务端（时钟准）
    phone = sess("s1", ["a1", "b1", "c2", "d2"], NOW - 2 * DAY)  # 手机慢 12 天，但内容更全
    merged = _merge_sessions([file_s], [phone], {})
    got = texts(only(merged, "s1"))
    check("慢时钟端新增的消息被保留", "c2" in got and "d2" in got, f"got={got}")
    check("服务端原有消息不丢", "a1" in got and "b1" in got, f"got={got}")
    check("updatedAt 取两者较大", only(merged, "s1")["updatedAt"] == NOW + 10 * DAY,
          str(only(merged, "s1")["updatedAt"]))


def test_append_direction_keeps_fresher_metadata():
    """超集方向收内容，但标题等仍以较新的一端为准，不得被旧端覆盖回去。"""
    file_s = sess("s1", ["a1"], NOW + 5 * DAY, title="新标题", pinned=True)
    old_end = sess("s1", ["a1", "a2"], NOW, title="旧标题", pinned=False)
    merged = only(_merge_sessions([file_s], [old_end], {}), "s1")
    check("保留较新一端的标题", merged.get("title") == "新标题", str(merged.get("title")))
    check("保留较新一端的置顶", merged.get("pinned") is True, str(merged.get("pinned")))
    check("同时收下新增消息", texts(merged) == ["a1", "a2"], str(texts(merged)))


def test_true_delete_still_newer_wins():
    """真删除的契约：删除端时间更新（且在 60s 宽限内）时删除生效。

    注意 strict-prefix 分支优先于子序列分支，且**故意**不信慢端的收缩：
    「入参恰好是文件的前缀」既可能是真删除、也可能只是一份还没聊到后面的旧快照，
    两者无法区分时选择保数据。所以这里用 5 秒内的新时间戳表达"刚刚删的"。
    """
    file_s = sess("s1", ["a1", "b1", "c1"], NOW)
    deleter = sess("s1", ["a1", "b1"], NOW + 5000)          # 刚删，时间戳更新
    merged = only(_merge_sessions([file_s], [deleter], {}), "s1")
    check("新端的删除生效", texts(merged) == ["a1", "b1"], str(texts(merged)))

    stale = sess("s1", ["a1", "b1"], NOW - DAY)             # 慢端拿前缀旧快照来覆盖
    merged2 = only(_merge_sessions([file_s], [stale], {}), "s1")
    check("慢端的伪删除不覆盖（保数据优先）", "c1" in texts(merged2), str(texts(merged2)))

    # 非前缀的删除（中间那条没了）走子序列分支，新端时间即生效
    file_s2 = sess("s2", ["a1", "b1", "c1"], NOW)
    mid_del = sess("s2", ["a1", "c1"], NOW + 5000)
    merged3 = only(_merge_sessions([file_s2], [mid_del], {}), "s2")
    check("中间删除也能生效", texts(merged3) == ["a1", "c1"], str(texts(merged3)))


def test_tombstones_never_expire_by_age():
    """离线超过原 30 天窗口的设备回来，不得把已删会话连同历史复活。"""
    old_tomb_ts = NOW - 200 * DAY
    tombs = _norm_tombstones([{"id": "s-dead", "ts": old_tomb_ts}], NOW)
    check("200 天前的墓碑仍然保留", tombs.get("s-dead") == old_tomb_ts, str(tombs))

    away = sess("s-dead", ["很久以前的 200 条历史"], old_tomb_ts - DAY)
    merged = _merge_sessions([], [away], tombs)
    check("久别的端重新上线不复活已删会话",
          only(merged, "s-dead") is None, f"merged={[s.get('id') for s in merged]}")


def test_future_and_invalid_tombstones_dropped():
    tombs = _norm_tombstones([
        {"id": "s-far", "ts": NOW + 400 * DAY},   # 严重超前的时钟：会永久压住同 id
        {"id": "s-bad", "ts": "not-a-number"},
        {"id": "", "ts": NOW},
        "not-a-dict",
        {"id": "s-ok", "ts": NOW},
    ], NOW)
    check("未来时间戳墓碑被丢", "s-far" not in tombs, str(tombs))
    check("非法墓碑条目被丢", set(tombs) == {"s-ok"}, str(tombs))


def test_duplicate_tombstone_ids_take_newest():
    tombs = _norm_tombstones([{"id": "s1", "ts": 100}, {"id": "s1", "ts": 900},
                              {"id": "s1", "ts": 500}], NOW)
    check("重复 id 取最新 ts", tombs == {"s1": 900}, str(tombs))


def test_tombstone_capacity():
    items = [{"id": f"s{i}", "ts": 1000 + i} for i in range(_TOMBSTONE_MAX_COUNT + 500)]
    tombs = _norm_tombstones(items, NOW)
    check("墓碑数量上限远大于真实用量", _TOMBSTONE_MAX_COUNT >= 2000,
          str(_TOMBSTONE_MAX_COUNT))
    check("规范化不丢合法墓碑", len(tombs) == _TOMBSTONE_MAX_COUNT + 500, str(len(tombs)))


if __name__ == "__main__":
    for fn in (test_slow_clock_append_is_not_lost,
               test_append_direction_keeps_fresher_metadata,
               test_true_delete_still_newer_wins,
               test_tombstones_never_expire_by_age,
               test_future_and_invalid_tombstones_dropped,
               test_duplicate_tombstone_ids_take_newest,
               test_tombstone_capacity):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print("\n" + "=" * 50)
    print(f"合并语义回归测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
