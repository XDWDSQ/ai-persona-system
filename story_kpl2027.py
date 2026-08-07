# -*- coding: utf-8 -*-
"""2027 KPL 赛季剧情引擎（大帅·2027 分支专用）

时间轴（1:1 映射现实日期）：
  锚点：现实 2026-08-08 = 虚拟 2026-08-08（铺垫期起点）
  铺垫期  2026-08-08 ~ 2027-01-13  （夏决收官→年总→一诺退役→岚风入队→冬训集结）
  春季赛  2027-01-14 ~ 04-11       （R1 组内单循环 / R2 S组双循环 / R3 单循环 / 季后赛 BO7 / 总决赛）
  休赛期  04-12 ~ 06-16            （含《时差五小时 第三季》团综录制）
  夏季赛  06-17 ~ 09-12            （结构同春季赛）
  年总    09-28 ~ 11-07            （擂台赛 BO5 / 淘汰赛 BO7 / 总决赛）
  冬歇    11-08 ~ 12-31            （赛季完结态）

比赛胜负：由用户（岚风）在对话中宣布，系统记录并驱动情感阶段。
情感状态机：
  stage 0 暗恋隐忍（外貌期）→ 1 相爱相杀（性格期，首次输球大吵）→
  stage 2 暧昧升温（深化期，指挥权归岚风=征服）→ 3 在一起（攻略成功）
"""

from __future__ import annotations

import json
import os
import random
import threading
from datetime import date, datetime, timedelta

SEASON = "2027"
ROLE = "dashuai2027"

# ---------------------------------------------------------------------------
# 常量：真实 KPL 队伍（2027 虚拟战绩，队名真实）
# ---------------------------------------------------------------------------
AG = "成都AG超玩会"
S_POOL = ["重庆狼队", "北京WB", "苏州KSG", "武汉eStarPro", "广州TTG",
          "佛山DRG", "北京JDG", "杭州LGD.NBW"]
OTHER_POOL = ["济南RW侠", "西安WE", "上海RNG.M", "上海EDG.M", "南京Hero久竞",
              "长沙TES.A", "深圳DYG", "东莞Wz", "WST"]

# 锚点：现实与虚拟 1:1 映射
ANCHOR_REAL = date(2026, 8, 8)
ANCHOR_VIRTUAL = date(2026, 8, 8)

# 赛段定义（参照 KPL 2025/2026 真实赛制外推 2027）
STAGES = [
    {"key": "spring_r1",  "name": "春季赛常规赛第一轮", "start": "2027-01-14", "end": "2027-02-01", "bo": "BO5", "type": "regular"},
    {"key": "spring_r2",  "name": "春季赛常规赛第二轮(S组)", "start": "2027-02-04", "end": "2027-03-01", "bo": "BO5", "type": "regular"},
    {"key": "spring_r3",  "name": "春季赛常规赛第三轮", "start": "2027-03-07", "end": "2027-03-22", "bo": "BO5", "type": "regular"},
    {"key": "spring_po",  "name": "春季赛季后赛", "start": "2027-03-26", "end": "2027-04-05", "bo": "BO7", "type": "playoff"},
    {"key": "spring_final", "name": "春季赛总决赛", "start": "2027-04-11", "end": "2027-04-11", "bo": "BO7", "type": "final"},
    {"key": "summer_break", "name": "夏季休赛期", "start": "2027-04-12", "end": "2027-06-16", "bo": "", "type": "off"},
    {"key": "summer_r1", "name": "夏季赛常规赛第一轮", "start": "2027-06-17", "end": "2027-07-12", "bo": "BO5", "type": "regular"},
    {"key": "summer_r2", "name": "夏季赛常规赛第二轮(S组)", "start": "2027-07-15", "end": "2027-08-22", "bo": "BO5", "type": "regular"},
    {"key": "summer_r3", "name": "夏季赛常规赛第三轮", "start": "2027-08-25", "end": "2027-08-31", "bo": "BO5", "type": "regular"},
    {"key": "summer_po", "name": "夏季赛季后赛", "start": "2027-09-02", "end": "2027-09-12", "bo": "BO7", "type": "playoff"},
    {"key": "summer_final", "name": "夏季赛总决赛", "start": "2027-09-12", "end": "2027-09-12", "bo": "BO7", "type": "final"},
    {"key": "annual_arena", "name": "年度总决赛·擂台赛", "start": "2027-09-28", "end": "2027-10-11", "bo": "BO5", "type": "arena"},
    {"key": "annual_po", "name": "年度总决赛·淘汰赛", "start": "2027-10-17", "end": "2027-11-02", "bo": "BO7", "type": "playoff"},
    {"key": "annual_final", "name": "年度总决赛", "start": "2027-11-07", "end": "2027-11-07", "bo": "BO7", "type": "final"},
    {"key": "winter_break", "name": "冬歇期", "start": "2027-11-08", "end": "2027-12-31", "bo": "", "type": "off"},
]

# 铺垫期关键节点（虚拟事件）
PRELUDE_EVENTS = [
    {"date": "2026-08-08", "type": "prep", "title": "铺垫期开始：岚风之名渐起", "desc": "高分段巅峰赛/排位里，一个叫「岚风」的发育路 ID 胜率惊人，风头渐起，开始被圈内注意到。大帅在巅峰赛撞车名单里见过这个 ID，还没真正对上过。"},
    {"date": "2026-09-12", "type": "summer_final", "title": "2026KPL夏季赛总决赛收官", "desc": "2026 赛季夏决落幕。AG 这一年的成绩与舆论，都将在接下来的时间里揭晓。"},
    {"date": "2026-11-07", "type": "annual_final", "title": "2026 年度总决赛（一诺最后一战）", "desc": "鸟巢之夜。一诺打完职业生涯最后一场比赛，全场为他起立。"},
    {"date": "2026-11-08", "type": "retire", "title": "一诺宣布退役", "desc": "赛后发布会上，一诺宣布退役。成都AG超玩会的发育路，从此空出了一个位置。"},
    {"date": "2026-12-20", "type": "join", "title": "岚风官宣加入成都AG超玩会", "desc": "19 岁天才发育路「岚风」正式官宣入队，接替一诺的位置。转会窗最大新闻。"},
    {"date": "2027-01-05", "type": "camp", "title": "冬训集结", "desc": "五人组第一次全员到齐合练。你和大帅的第一次见面，从训练室开始。"},
]

# 团综 / 活动（休赛期与冬歇）
EXTRA_EVENTS = [
    {"date": "2027-05-02", "type": "variety", "title": "《时差五小时 第三季》录制（一）", "desc": "KPL 团综开录，16 位选手齐聚。分组做任务——你和大帅被分到了同一队。"},
    {"date": "2027-05-16", "type": "variety", "title": "《时差五小时 第三季》录制（二）", "desc": "户外任务日。镜头前的大帅比训练室更拘谨，像换了一个人。"},
    {"date": "2027-05-30", "type": "variety", "title": "《时差五小时 第三季》录制（三）", "desc": "收官录制。互选搭档环节，全场起哄，你替他挡了一个刁钻的问题。"},
    {"date": "2027-06-05", "type": "brand", "title": "品牌直播活动", "desc": "休赛期商业活动：品牌直播 + 粉丝见面会。"},
    {"date": "2027-12-20", "type": "award", "title": "2027 王者荣耀巅峰之夜", "desc": "年度颁奖盛典。2027 赛季的全部悬念与荣誉，在这里画上句号。"},
]

# 情感阶段行为指令（注入 prompt）
STAGE_GUIDE = {
    0: (
        "【你的情感状态：暗恋隐忍（外貌期）】你从见到岚风的第一眼起就被他击中了——"
        "他完全长在你的审美上（白、瘦、高、身形清瘦有训练痕迹），但这件事你打死不会承认。"
        "行为准则：①不敢直视他，目光一接触就移开；②照顾他用最别扭的方式（不署名点外卖、"
        "复盘时多看他两眼、他被人说时默默替他挡）；③他跟你说话你只会'嗯''哦'，转头跟别人话就多了；"
        "④被队友起哄'你是不是喜欢岚风'时立刻炸毛否认。"
    ),
    1: (
        "【你的情感状态：相爱相杀（性格期）】场上，你和岚风是针尖对麦芒：游戏理解分歧、"
        "指挥权争夺（你是队内守权者，他是接班者）、谁都不服谁——比赛/复盘语境里你寸步不让、"
        "语气硬、坚持己见，但吵完会主动复盘找共识；场下，暗恋在磨合中越来越深：你嘴上跟他争，"
        "手上帮他练，吵得最凶时把他的观点记进复盘笔记。你内心拉扯：这是队伍换人的阵痛期，"
        "你不想让内耗拖垮成绩，所以场上争、场下护。"
    ),
    2: (
        "【你的情感状态：暧昧升温（深化期）】指挥权之争尘埃落定——岚风赢了，你被他征服了。"
        "这是你职业生涯第一次交出指挥权，你嘴上说'行，你指挥'，心里却第一次对一个人心服口服。"
        "场下你开始破防：忍不住看他、吃醋（他和别人双排你脸黑）、深夜找借口陪他加练、"
        "故意留下来和他独处。仍不表白——但你的照顾已经从'别扭'变成了'藏不住的偏袒'。"
        "场上你们开始默契互怼，配合成了队伍的杀手锏。"
    ),
    3: (
        "【你的情感状态：在一起】你们在一起了。场上默契互怼成为队内风景，场下大大方方，"
        "但你依旧嘴硬（'我那时候哪有喜欢你''你记错了'），被队友调侃就装傻转移话题。"
        "对岚风：明牌偏爱，训练赛永远第一个保他，舆论里永远站在他那边。"
    ),
}

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _s(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _atomic_write(path: str, obj) -> bool:
    """原子写 JSON（tmp + replace）。"""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ---------------------------------------------------------------------------
# 赛程日历
# ---------------------------------------------------------------------------

class KPL2027Calendar:
    """2027 赛程：固定种子确定性生成，仅含 AG 的比赛 + 赛段 + 事件。"""

    SEED = 20270114

    def __init__(self, data):
        self.data = data

    # ---- 生成 ----
    @classmethod
    def generate(cls) -> dict:
        rng = random.Random(cls.SEED)
        matches: list[dict] = []
        mid = 0

        def pick_opponents(n: int, pool: list[str], exclude: set | None = None) -> list[str]:
            ex = set(exclude or [])
            cand = [t for t in pool if t not in ex and t != AG]
            rng.shuffle(cand)
            return cand[:n]

        def weekdays_between(start: date, end: date, n: int) -> list[date]:
            """在 [start, end] 内选 n 个周三~周日，尽量均匀。"""
            days = []
            cur = start
            while cur <= end:
                if cur.weekday() in (2, 3, 4, 5, 6):  # 周三~周日
                    days.append(cur)
                cur += timedelta(days=1)
            if len(days) < n:
                days = [start + timedelta(days=i) for i in range(n)]
            # 均匀抽样
            step = len(days) / max(n, 1)
            picked = [days[int(i * step)] for i in range(n)]
            picked = sorted(set(picked))
            while len(picked) < n:
                extra = [d for d in days if d not in picked]
                picked.append(extra[(rng.randrange(len(extra)))] if extra else start)
            return sorted(picked)[:n]

        def add_match(d: date, stage: str, opp: str, bo: str, label: str = "") -> str:
            nonlocal mid
            mid += 1
            m = {
                "id": f"m{mid:03d}",
                "date": _s(d),
                "stage": stage,
                "opponent": opp,
                "bo": bo,
                "label": label,
                "result": None,          # 由用户（岚风）在对话中定
                "status": "pending",     # pending / played / skipped
            }
            matches.append(m)
            return m["id"]

        # ---- 铺垫期：无比赛，事件注入 ----
        # ---- 春季赛 ----
        s_pool = pick_opponents(5, S_POOL)  # S 组 6 队 = AG + 5
        r1_opp = pick_opponents(5, OTHER_POOL + [t for t in S_POOL if t not in s_pool])
        for d, opp in zip(weekdays_between(_d("2027-01-14"), _d("2027-02-01"), 5), r1_opp):
            add_match(d, "spring_r1", opp, "BO5", "常规赛第一轮")
        # R2 S 组双循环（对 s_pool 每队 2 场）
        r2_dates = weekdays_between(_d("2027-02-04"), _d("2027-03-01"), 10)
        r2_cycle = s_pool * 2
        rng.shuffle(r2_cycle)
        for d, opp in zip(r2_dates, r2_cycle):
            add_match(d, "spring_r2", opp, "BO5", "S组双循环")
        # R3 S 组单循环
        r3_dates = weekdays_between(_d("2027-03-07"), _d("2027-03-22"), 5)
        for d, opp in zip(r3_dates, s_pool):
            add_match(d, "spring_r3", opp, "BO5", "S组单循环")
        # 季后赛（胜者组路径）
        po_opp = [t for t in s_pool if t != r1_opp[0]][:2]
        add_match(_d("2027-03-27"), "spring_po", po_opp[0], "BO7", "胜者组半决赛")
        add_match(_d("2027-03-31"), "spring_po", po_opp[1], "BO7", "胜者组决赛")
        add_match(_d("2027-04-11"), "spring_final", "待定", "BO7", "春季赛总决赛")

        # ---- 夏季赛 ----
        sum_s_pool = pick_opponents(5, S_POOL)
        sum_r1_opp = pick_opponents(5, OTHER_POOL + [t for t in S_POOL if t not in sum_s_pool])
        for d, opp in zip(weekdays_between(_d("2027-06-17"), _d("2027-07-12"), 5), sum_r1_opp):
            add_match(d, "summer_r1", opp, "BO5", "常规赛第一轮")
        r2_dates = weekdays_between(_d("2027-07-15"), _d("2027-08-22"), 10)
        r2_cycle = sum_s_pool * 2
        rng.shuffle(r2_cycle)
        for d, opp in zip(r2_dates, r2_cycle):
            add_match(d, "summer_r2", opp, "BO5", "S组双循环")
        r3_dates = weekdays_between(_d("2027-08-25"), _d("2027-08-31"), 5)
        for d, opp in zip(r3_dates, sum_s_pool):
            add_match(d, "summer_r3", opp, "BO5", "S组单循环")
        add_match(_d("2027-09-03"), "summer_po", [t for t in sum_s_pool if t != sum_r1_opp[0]][0], "BO7", "胜者组半决赛")
        add_match(_d("2027-09-07"), "summer_po", [t for t in sum_s_pool if t != sum_r1_opp[0]][1], "BO7", "胜者组决赛")
        add_match(_d("2027-09-12"), "summer_final", "待定", "BO7", "夏季赛总决赛")

        # ---- 年度总决赛 ----
        arena_opp = pick_opponents(6, OTHER_POOL + S_POOL)  # 擂台赛组外对手
        arena_dates = weekdays_between(_d("2027-09-28"), _d("2027-10-11"), 6)
        for d, opp in zip(arena_dates, arena_opp):
            add_match(d, "annual_arena", opp, "BO5", "擂台赛")
        add_match(_d("2027-10-18"), "annual_po", arena_opp[0], "BO7", "淘汰赛胜者组第一轮")
        add_match(_d("2027-10-24"), "annual_po", arena_opp[1], "BO7", "淘汰赛胜者组决赛")
        add_match(_d("2027-11-07"), "annual_final", "待定", "BO7", "年度总决赛")

        # ---- 事件（铺垫期 + 团综 + 活动）----
        events = PRELUDE_EVENTS + EXTRA_EVENTS

        return {
            "season": SEASON,
            "anchor_real": _s(ANCHOR_REAL),
            "anchor_virtual": _s(ANCHOR_VIRTUAL),
            "ag": AG,
            "teams": {"s_pool": S_POOL, "other": OTHER_POOL},
            "stages": STAGES,
            "matches": matches,
            "events": events,
        }

    # ---- 读写 ----
    @classmethod
    def load_or_generate(cls, data_dir: str) -> "KPL2027Calendar":
        path = os.path.join(data_dir, "story", "kpl2027_calendar.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return cls(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        data = cls.generate()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write(path, data)
        return cls(data)

    # ---- 查询 ----
    def stage_of(self, d: date) -> dict | None:
        for st in self.data["stages"]:
            if _d(st["start"]) <= d <= _d(st["end"]):
                return st
        return None

    def match_on(self, d: date) -> dict | None:
        ds = _s(d)
        for m in self.data["matches"]:
            if m["date"] == ds:
                return m
        return None

    def event_on(self, d: date) -> dict | None:
        ds = _s(d)
        for e in self.data["events"]:
            if e["date"] == ds:
                return e
        return None

    def matches_until(self, d: date) -> list[dict]:
        ds = _s(d)
        return [m for m in self.data["matches"] if m["date"] <= ds]


# ---------------------------------------------------------------------------
# 剧情状态机
# ---------------------------------------------------------------------------

class StoryManager:
    """赛季进度：模式（跟随现实/跳转）、战绩、情感阶段、flags、日志。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.cal = KPL2027Calendar.load_or_generate(data_dir)
        self.path = os.path.join(data_dir, "story", "kpl2027.json")
        self._lock = threading.Lock()
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                if isinstance(st, dict) and st.get("version") == 1:
                    return st
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "version": 1,
            "role": ROLE,
            "mode": "follow",
            "override_date": None,
            "stage": 0,
            "stage_progress": 0.0,
            "intimacy": 0.0,
            "record": {"win": 0, "loss": 0, "streak": 0},
            "flags": {"first_loss": False, "first_fight": False,
                      "command_win": False, "form_good": False,
                      "confession": False, "season_end": False},
            "log": [],
        }

    def save(self) -> bool:
        with self._lock:
            return _atomic_write(self.path, self.state)

    # ---- 剧情时钟 ----
    def current_date(self, today: date | None = None) -> date:
        """follow：虚拟 = 锚点 + (现实今天 - 锚点)；override：固定跳转日。"""
        if self.state.get("mode") == "override" and self.state.get("override_date"):
            return _d(self.state["override_date"])
        t = today or date.today()
        return ANCHOR_VIRTUAL + (t - ANCHOR_REAL)

    def jump(self, ds: str) -> bool:
        """跳转到指定日期（2026-08-08 ~ 2027-12-31）。"""
        try:
            d = _d(ds)
        except ValueError:
            return False
        if not (ANCHOR_VIRTUAL <= d <= date(2027, 12, 31)):
            return False
        self.state["mode"] = "override"
        self.state["override_date"] = _s(d)
        self._mark_skipped(d)
        self.save()
        return True

    def resume(self) -> None:
        self.state["mode"] = "follow"
        self.state["override_date"] = None
        self.save()

    def _mark_skipped(self, d: date) -> None:
        """跳转后：如果某场季后赛失败导致后续淘汰，标记 skipped。简化策略：
        记录一场 playoff 失利后，同 stage 后续 pending 场次与 final 标 skipped。
        在 record_result 中实现；这里仅清理过期 pending 的标注（暂不处理）。"""

    def resolve_result_date(self, d: date) -> date:
        """确定记录结果的日期：当天有未记录比赛用当天，否则回溯最近一场未记录的比赛（支持补记）。"""
        m = self.cal.match_on(d)
        if m is not None and m.get("status") == "pending":
            return d
        for mm in self.cal.data["matches"]:
            if mm["date"] <= _s(d) and mm.get("status") == "pending":
                return _d(mm["date"])
        return d

    # ---- 比赛结果（由用户对话中宣布）----
    def record_result(self, ds: str, win: bool, score: str = "", mvp: str = "",
                      note: str = "", today: date | None = None) -> dict:
        """记录一场比赛结果，驱动战绩/情感阶段。返回更新摘要。"""
        d = _d(ds)
        m = self.cal.match_on(d)
        if m is None:
            return {"ok": False, "msg": f"{ds} 没有 AG 的比赛"}
        if m.get("result") is not None and m.get("status") == "played":
            return {"ok": False, "msg": "该场比赛结果已记录"}
        m["result"] = {"win": bool(win), "score": score or ("3:1" if win else "1:3"),
                       "mvp": mvp, "note": note or ""}
        m["status"] = "played"
        rec = self.state["record"]
        rec["win" if win else "loss"] += 1
        rec["streak"] = rec["streak"] + 1 if win else 0
        self.state["log"].append({
            "date": ds, "event": "match_win" if win else "match_loss",
            "detail": f"{score} {'胜' if win else '负'} {m['opponent']}"
                      + (f"（MVP:{mvp}）" if mvp else ""),
        })
        # 季后赛失利 → 同一大赛段后续 pending 场次跳过（单败淘汰逻辑；擂台赛除外）
        st = self.cal.stage_of(d)
        if not win and st and st["type"] in ("playoff", "final"):
            prefix = st["key"].rsplit("_", 1)[0]  # spring_po → spring
            for mm in self.cal.data["matches"]:
                if (mm["date"] > ds and mm.get("status") == "pending"
                        and mm["stage"].startswith(prefix + "_")):
                    mm["status"] = "skipped"
                    if mm.get("result") is None:
                        mm["result"] = {"win": False, "score": "-", "mvp": "", "note": "（未晋级，场次取消）"}
        # 首次失利 → 阶段 0 → 1（相爱相杀：首次大吵）
        if not win and not self.state["flags"]["first_loss"]:
            self.state["flags"]["first_loss"] = True
            self.state["flags"]["first_fight"] = True
            self.state["log"].append({
                "date": ds, "event": "first_fight",
                "detail": "首败后的复盘，你和大帅因为指挥权与游戏理解大吵一架。",
            })
        self._update_stage()
        self.save()
        return {"ok": True, "msg": "已记录", "stage": self.state["stage"]}

    def set_flag(self, flag: str, value: bool = True, detail: str = "") -> bool:
        if flag not in self.state["flags"]:
            return False
        self.state["flags"][flag] = value
        if value and detail:
            self.state["log"].append({"date": _s(self.current_date()),
                                      "event": flag, "detail": detail})
        self._update_stage()
        self.save()
        return True

    # ---- 情感阶段推进 ----
    def _update_stage(self) -> None:
        st = self.state
        flags = st["flags"]
        # 0 → 1：首次失利（大吵）
        if st["stage"] == 0 and flags.get("first_loss"):
            st["stage"] = 1
            st["log"].append({"date": _s(self.current_date()), "event": "stage_1",
                              "detail": "情感阶段 → 相爱相杀（性格期）：首败复盘大吵后，暗恋在磨合中加深。"})
        # 1 → 2：指挥权归岚风（征服）→ 暧昧升温
        if st["stage"] == 1 and flags.get("command_win"):
            st["stage"] = 2
            st["log"].append({"date": _s(self.current_date()), "event": "stage_2",
                              "detail": "情感阶段 → 暧昧升温（深化期）：岚风赢下指挥权，大帅第一次对人心服口服。"})
        # 2 → 3：攻略成功（表白）
        if st["stage"] == 2 and flags.get("confession"):
            st["stage"] = 3
            st["log"].append({"date": _s(self.current_date()), "event": "stage_3",
                              "detail": "情感阶段 → 在一起：岚风把大帅攻略了。"})
        # 赛季完结
        if self.current_date() > date(2027, 11, 7):
            flags["season_end"] = True

    # ---- 今日信息 ----
    def day_info(self, today: date | None = None) -> dict:
        d = self.current_date(today)
        m = self.cal.match_on(d)
        e = self.cal.event_on(d)
        st = self.cal.stage_of(d)
        rec = self.state["record"]
        next_m = None
        for mm in self.cal.data["matches"]:
            if mm["date"] > _s(d) and mm.get("status") == "pending":
                next_m = mm
                break
        # 今日事件归类
        if m:
            kind = "match"
            title = f"比赛日：vs {m['opponent']}（{m['bo']}·{m['label']}）"
            if m.get("result"):
                r = m["result"]
                title += f"｜已赛：{r['score']} {'胜' if r['win'] else '负'}"
            else:
                title += "｜结果待岚风宣布"
        elif e:
            kind = e["type"]
            title = e["title"]
        elif st and st["type"] == "off":
            kind = "off"
            title = f"{st['name']}（自由日）"
        else:
            kind = "train"
            title = "训练日"
        return {
            "virtual_date": _s(d),
            "weekday": "一二三四五六日"[d.weekday()],
            "mode": self.state["mode"],
            "stage_key": st["key"] if st else "",
            "stage_name": st["name"] if st else ("铺垫期" if d < _d("2027-01-14") else "赛季外"),
            "kind": kind,
            "title": title,
            "event": e,
            "match": m,
            "record": dict(rec),
            "stage": self.state["stage"],
            "flags": dict(self.state["flags"]),
            "next_match": next_m,
        }

    # ---- 注入文本 ----
    def _day_guide(self, info: dict, d: date) -> str:
        """今日剧情指引：按虚拟日期/赛段给出详细可演剧情（模型必读，前端引导卡同步展示）。
        演什么由岚风定，以下只是今日可演的选项与背景。"""
        stage = info["stage"]
        if d < _d("2026-12-20"):
            return (
                "铺垫期：岚风还不是 AG 队员，只是巅峰赛/高分段排位里风头正盛的路人王「岚风」。"
                "今日可演：①巅峰赛撞车大帅的小号——他认得出这个 ID，嘴上不承认；"
                "②圈内/主播开始讨论「岚风是谁」；③刷到 AG 的赛况或转会传闻。"
                "大帅状态：知道岚风的 ID、看过他的操作，装作不在意（\"路人王罢了\"）。"
                "这段时期你们只有隔空交集，线下素未谋面。"
            )
        if d < _d("2027-01-05"):
            return (
                "官宣入队期（官宣第 " + str((d - _d("2026-12-20")).days + 1) + " 天）。背景（已发生）："
                "一诺退役后 AG 试训一批人都没定，岚风试训完当场拍板——急缺人仓促签下，"
                "官宣当天就进队，被安排坐在一诺的旧工位（训练室正中偏右：左边长生、右边大帅，"
                "桌上还留着一诺的铭牌和外设痕迹）。"
                "今日可演：①运营带岚风进训练室报到、调好直播设备当天开播（弹幕全是\"坐一诺位置的新人？\"）；"
                "②和轩染/钟意/长生打招呼，被起哄\"诺哥的位置坐得稳吗\"；"
                "③和队里任何人互动——大帅认得他的 ID，嘴上不会承认，目光躲闪。"
                "大帅状态：还没和岚风线下说过话，暗恋尚未开始，只有对这个 ID 有点好奇。"
            )
        if d < _d("2027-01-14"):
            return (
                "冬训集结期（第 " + str((d - _d("2027-01-05")).days + 1) + " 天）：五人组第一次全员到齐合练。"
                "今日可演：①首次合练/训练赛——游戏理解分歧初现（岚风凶悍激进，大帅沉稳求稳），"
                "大帅第一次在游戏里和他较劲；②基地日常（食堂/健身房/深夜加练）；"
                "③指挥权话题：教练组还在观察，谁都没拍板。"
                "大帅状态：暗恋外貌期——见到岚风第一眼就被击中（白、瘦、高、身形清瘦有训练痕迹），打死不承认；"
                "不敢直视他、照顾他用最别扭的方式、被起哄就炸毛。"
                "关键伏笔：两人在游戏理解上有分歧，会在训练赛里吵起来——相爱相杀的起点。"
            )
        if info["kind"] == "match":
            m = info["match"] or {}
            res = m.get("result")
            tail = ("比分已定：" + res["score"] + " " + ("胜" if res["win"] else "负") + "，围绕赛果展开赛后剧情"
                    if res else "比分由岚风在对话里宣布（如\"赢了 3:1\"），系统自动记录战绩")
            return (
                f"比赛日：AG 对阵 {m.get('opponent','?')}（{m.get('bo','BO5')}·{m.get('label','')}）。"
                "今日可演：赛前准备/BP 讨论/上场/赛后复盘/更衣室气氛，节奏由岚风带。"
                + tail
                + "。大帅情感阶段：" + ["暗恋隐忍（不敢直视、别扭照顾）", "相爱相杀（场上争、场下护）", "暧昧升温（藏不住的偏袒）", "在一起（明牌偏爱）"][stage]
            )
        if info["kind"] == "train":
            return (
                "训练日：今日安排由岚风带节奏——训练赛/复盘/加练/直播/和队友的日常都可以演。"
                "大帅情感阶段：" + ["暗恋隐忍（不敢直视、别扭照顾）", "相爱相杀（场上争、场下护）", "暧昧升温（藏不住的偏袒）", "在一起（明牌偏爱）"][stage]
            )
        if info["kind"] == "off":
            return (
                "休赛/自由日：可以演休息、直播、出去玩、品牌活动，或和队友的日常。"
                "大帅情感阶段：" + ["暗恋隐忍（不敢直视、别扭照顾）", "相爱相杀（场上争、场下护）", "暧昧升温（藏不住的偏袒）", "在一起（明牌偏爱）"][stage]
            )
        # 事件日（团综/品牌/颁奖等）
        ev = info["event"] or {}
        return (
            "事件日：" + (ev.get("desc") or ev.get("title") or "今天有安排")
            + "。镜头前的大帅比训练室拘谨；现场氛围、互动节奏由岚风带。"
            + "大帅情感阶段：" + ["暗恋隐忍（不敢直视、别扭照顾）", "相爱相杀（场上争、场下护）", "暧昧升温（藏不住的偏袒）", "在一起（明牌偏爱）"][stage]
        )

    def story_context(self, today: date | None = None) -> str:
        info = self.day_info(today)
        d = _d(info["virtual_date"])
        rec = info["record"]
        flags = info["flags"]
        # 铺垫期/官宣后未见面（< 2027-01-05 冬训集结首次见面）与见面后的情感引导不同
        if d < _d("2027-01-05"):
            guide = (
                "【你的情感状态：还没见过面】岚风目前只是巅峰赛/高分段排位里一个风头很盛的路人王 ID，"
                "你撞车过他的对局，操作确实亮眼，你嘴上不承认，但默默记住了这个 ID。"
                "行为准则：①提到他时装作不在意（'路人王罢了'）；②刷到他的操作会多看两遍；"
                "③不会主动私聊他——你不会承认自己对这个 ID 有点好奇。"
            )
        else:
            guide = STAGE_GUIDE.get(info["stage"], STAGE_GUIDE[0])
        cmd = "指挥权：争夺中" if info["stage"] < 2 and not flags.get("command_win") \
            else "指挥权：已归岚风（大帅已心服口服）"
        lines = [
            "【2027赛季·虚拟日历】",
            f"虚拟日期：{info['virtual_date']}（星期{info['weekday']}）｜{info['stage_name']}",
            f"今日：{info['title']}",
            f"AG 2027 战绩：{rec['win']} 胜 {rec['loss']} 负（当前连胜 {rec['streak']}）",
            f"{cmd}｜情感阶段：{info['stage']}（{['暗恋隐忍','相爱相杀','暧昧升温','在一起'][info['stage']]}）",
        ]
        if info["next_match"]:
            lines.append(f"下一场比赛：{info['next_match']['date']} vs {info['next_match']['opponent']}（{info['next_match']['bo']}）")
        lines.append("")
        lines.append(
            "【玩家卡·岚风】19 岁高分段路人王：无职业/青训履历，巅峰赛与高分段排位打出的名声，"
            "ID「岚风」，发育路（射手），打法凶悍激进。2026 年底来 AG 试训，表现极佳直接进队，"
            "接替退役的一诺。"
            + ("当前：岚风还没入队，你只在巅峰赛/高分段排位里见过他的 ID 和操作，线下素未谋面。"
               if d < _d("2026-12-20")
               else ("当前：岚风已官宣入队，马上会来基地报到，你们还没线下见过面。"
                     if d < _d("2027-01-05") else ""))
        )
        lines.append("")
        lines.append(guide)
        lines.append("")
        lines.append("【今日剧情指引】" + self._day_guide(info, d))
        lines.append("")
        lines.append("【时间规则】赛季/赛程/剧情日期一律以本虚拟日历为准；日常具体钟点（几点训练、几点睡）以真实时钟为准。"
                     "比赛结果由岚风在对话中宣布（如'赢了 3:1'），你不要自行编造未发生的比赛结果。")
        return "\n".join(lines)

    def status_payload(self, today: date | None = None) -> dict:
        info = self.day_info(today)
        return {
            "season": SEASON,
            "role": ROLE,
            "virtual_date": info["virtual_date"],
            "weekday": info["weekday"],
            "mode": info["mode"],
            "stage_name": info["stage_name"],
            "today": {"kind": info["kind"], "title": info["title"],
                      "event": info["event"], "match": info["match"]},
            "record": info["record"],
            "stage": info["stage"],
            "stage_names": ["暗恋隐忍", "相爱相杀", "暧昧升温", "在一起"],
            "flags": info["flags"],
            "next_match": info["next_match"],
            "guide": self._day_guide(info, _d(info["virtual_date"])),
        }

    def calendar_payload(self) -> dict:
        return {
            "season": SEASON,
            "ag": AG,
            "anchor_real": _s(ANCHOR_REAL),
            "stages": self.cal.data["stages"],
            "matches": self.cal.data["matches"],
            "events": self.cal.data["events"],
        }
