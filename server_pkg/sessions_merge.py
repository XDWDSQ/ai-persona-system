# -*- coding: utf-8 -*-
"""多端会话合并纯函数（自 server.py 迁移，逻辑逐字节等价）。

只依赖传入的参数与本模块常量；墓碑淘汰阈值一并迁移，
调用方（server.py 会话读写路径）行为不变。
"""

# 删除墓碑：某端删掉的会话 id 记入此处（随 sessions.json 的 deleted 字段持久化），
# 防止另一端旧快照 PUT 时把已删会话"复活"。ts 为毫秒时间戳（与前端 Date.now() 一致）。
#
# 墓碑**不按时间过期**。会话 id 是 's'+Date.now().toString(36)+random，永不复用，
# 所以保留一条旧墓碑不可能误伤后来的新会话，代价只有每条约 20 字节。
# 反过来，一旦按 30 天丢弃就会丢数据：某台设备离线超过 30 天后重新上线，
# 它 PUT 上来的旧快照里那条已删会话既没有墓碑压着、也不在服务端 current 里，
# 于是被当成"本地独有会话"重新插入，并顺着合并传播到所有设备 —— 用户明明删掉了，
# 一个月后它带着全部历史自己回来了。
# 只保留一个硬上限兜住病态增长（正常用量远达不到），超限时淘汰最旧的。
_TOMBSTONE_MAX_COUNT = 2000
# 未来时间戳（时钟严重超前的端）依然要丢：它会永久压住同 id 的正常会话
_TOMBSTONE_FUTURE_SKEW_MS = 86400000


def _norm_tombstones(items, now_ms: float) -> dict:
    """规范化墓碑列表为 {id: ts_ms}：丢弃非法与未来时间戳，重复 id 取较新。"""
    out: dict = {}
    for t in items or []:
        if not isinstance(t, dict):
            continue
        sid, ts = t.get("id"), t.get("ts")
        if not isinstance(sid, str) or not sid:
            continue
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if ts > now_ms + _TOMBSTONE_FUTURE_SKEW_MS:
            continue
        if ts > out.get(sid, 0):
            out[sid] = ts
    return out


def _sess_updated_at(s: dict) -> float:
    """会话 updatedAt 规范化：非数字（畸形数据）按 0 处理，避免 str/int 混比抛 TypeError。"""
    try:
        return float(s.get("updatedAt") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_placeholder_session(s: dict) -> bool:
    """空占位会话：无 history、标题仍是前端默认「新对话」、未置顶未改名。

    前端只要以空 localStorage 打开一次 chat.html 就会生成一个这种会话，
    并随 pagehide 兜底 PUT 上服务端，经并集同步下发到每台设备，
    把会话列表淹成一片「新对话」。合并时直接丢弃；一旦发出消息、
    手动改名或置顶，即脱离占位形态，正常保留。"""
    if not isinstance(s, dict):
        return False
    h = s.get("history")
    if isinstance(h, list) and h:
        return False
    if s.get("pinned") or s.get("manualTitle"):
        return False
    title = s.get("title")
    return not title or title == "新对话"


def _merge_sessions(current: list, incoming: list, tombstones: dict) -> list:
    """多端合并：按 id 归并（updatedAt 新者胜，平手取 incoming），再按墓碑过滤。

    竞态防御（防止聊天记录被旧快照整段抹掉）：
    同 id 会话的 incoming 与 current 出现三种关系，分别处理：
    1. incoming 是 current 的"严格前缀"（消息数更少且逐条一致）→ incoming 是纯旧
       快照（时钟偏移/并发双写，没带来任何新消息）→ 保留 current，绝不覆盖；
       例外：updatedAt 仅比 current 新 ≤60s（用户刚删除最后一条消息的快速操作）时
       视为删除、生效。阈值权衡：防静默丢数据优先于删除最后一条的精确性。
    2. incoming 是 current 的"有序子序列"（允许跳项，即删除后的剩余序列）→ 这是
       用户删除消息/清空的操作 → updatedAt 新者胜（删除生效）。
    3. 其余（分歧：两端各有对方没有的消息，如并发各发各话）→ 消息级合并：以
       updatedAt 新者的消息顺序为基底，把另一端独有的消息按原顺序补回，双方消息
       都不丢（顺序以新者为准，独有消息追加在后）。
    真实事故背景：手机持 130 条旧副本、时钟超前，覆盖了服务端 131 条，丢 2 条。

    空占位会话（_is_placeholder_session）不参与结果，存量垃圾随任意一次 PUT 自动清出。
    会话 updatedAt 新于墓碑时间视为"复活"（保留会话并移除墓碑）；
    返回按 updatedAt 倒序的前 500 条。会就地修改 tombstones（弹出失效项）。"""

    def _msg_key(m: dict):
        # 消息指纹：role+content+style+附件。同一文本配不同图片/附件
        # 不得视为同一条（旧实现只比前三项，同文不同图的并发合并会丢消息）。
        # 注意 audio（TTS 持久化 URL）故意不进指纹：它是任一端事后独立写入的
        # 易变元数据，两端同一条消息一方带 audio 一方不带时若视为不同，
        # 分歧合并会把"同一条"追加两次（相邻双胞胎 bug）。
        atts = m.get("attachments") or []
        try:
            att_sig = tuple(
                (a.get("name"), a.get("size"))
                for a in atts
                if isinstance(a, dict)
            )
        except (TypeError, AttributeError):
            att_sig = ()
        return (m.get("role"), m.get("content"), m.get("style") or "", att_sig)

    def _is_strict_prefix(shorter: list, longer: list) -> bool:
        """shorter 是否严格是 longer 的前缀（按消息指纹逐条比对）。"""
        if not shorter or len(shorter) >= len(longer):
            return False
        for i, m in enumerate(shorter):
            if _msg_key(m) != _msg_key(longer[i]):
                return False
        return True

    def _is_subsequence(sub: list, sup: list) -> bool:
        """sub 是否严格是 sup 的有序子序列（允许跳过 sup 中的若干消息）。
        删除消息后提交的序列恰为此形态。"""
        if not sub or len(sub) >= len(sup):
            return False
        i, n = 0, len(sup)
        for m in sub:
            k = _msg_key(m)
            while i < n and _msg_key(sup[i]) != k:
                i += 1
            if i >= n:
                return False
            i += 1
        return True

    def _union_history(base: list, extra: list) -> list:
        """消息级合并：保留 base 顺序，extra 中不在 base 里的消息按原顺序追加。"""
        seen = set()
        for m in base:
            seen.add(_msg_key(m))
        out = list(base)
        for m in extra:
            k = _msg_key(m)
            if k not in seen:
                seen.add(k)
                out.append(m)
        return out

    by_id: dict = {}

    def _hist(sess: dict) -> list:
        # history 畸形（磁盘损坏/旧客户端写入 dict 等非列表）归一为空列表，
        # 否则前缀/子序列/并集分支会对字符串 key 调 .get 抛 AttributeError → 500
        h = sess.get("history")
        return h if isinstance(h, list) else []

    for s in [*current, *incoming]:
        if not isinstance(s, dict):
            continue
        if _is_placeholder_session(s):
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        old = by_id.get(sid)
        if old is None:
            by_id[sid] = s
            continue
        # old=已入桶（current 优先），s=当前遍历项（incoming 在后）
        old_hist = _hist(old)
        new_hist = _hist(s)
        new_wins = _sess_updated_at(s) >= _sess_updated_at(old)
        if _is_strict_prefix(new_hist, old_hist):
            # incoming 是 old 的纯旧快照：默认保留 old 防时钟偏移覆盖；
            # 例外：updatedAt 仅比 old 新 ≤60s（用户刚删除最后一条消息的快速操作）→ 删除生效
            if _sess_updated_at(s) - _sess_updated_at(old) > 60000:
                continue
            if new_wins:
                by_id[sid] = s
            continue
        if new_hist == [] and old_hist:
            # 清空全部消息：视为删除操作
            if new_wins:
                by_id[sid] = s
            continue
        if _is_subsequence(new_hist, old_hist):
            # 入参是文件的子序列：这一端删过消息 → 正常删除语义，updatedAt 新者胜
            if new_wins:
                by_id[sid] = s
            continue
        if _is_subsequence(old_hist, new_hist) and old_hist:
            # 入参是文件的**超集**：这一端只是多聊了几条，没有删除。
            # 原来两个方向合并成一条分支、统一要求 new_wins，于是慢时钟的端
            # （手机休眠后时钟落后、或从备份恢复）刚发出去的消息会被判成旧快照
            # 整段丢弃 —— 而这些消息在文件里根本不存在，丢弃就是永久消失。
            # 收下新历史不会丢任何内容，与两侧时钟无关；其余字段仍以较新的一端为准。
            host = s if new_wins else old
            merged_s = dict(host)
            merged_s["history"] = new_hist
            merged_s["updatedAt"] = max(_sess_updated_at(s), _sess_updated_at(old))
            by_id[sid] = merged_s
            continue
        if _is_subsequence(old_hist, new_hist):
            by_id[sid] = s
            continue
        # 分歧：双方各有独有消息 → 消息级合并，双方都不丢
        base, extra = (s, old) if new_wins else (old, s)
        merged_s = dict(base)
        merged_s["history"] = _union_history(_hist(base), _hist(extra))
        merged_s["updatedAt"] = max(_sess_updated_at(s), _sess_updated_at(old))
        by_id[sid] = merged_s
    out = []
    for sid, s in by_id.items():
        ts = tombstones.get(sid)
        if ts is None:
            out.append(s)
        elif _sess_updated_at(s) > ts:
            tombstones.pop(sid, None)
            out.append(s)
        # 其余：最后更新早于删除时间 → 维持删除状态
    out.sort(key=_sess_updated_at, reverse=True)
    return out[:500]


def _sessions_msg_count(sessions: list) -> int:
    """统计会话列表的总消息数（history 字段）。"""
    total = 0
    for s in sessions or []:
        if not isinstance(s, dict):
            continue
        h = s.get("history")
        if isinstance(h, list):
            total += len(h)
    return total
