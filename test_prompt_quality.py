# -*- coding: utf-8 -*-
"""人设 / 提示词 / 剧情注入质量回归（2026-09 第八轮「内容质量」专场）。

运行：python test_prompt_quality.py
与功能测试不同，本套件锁定的是「内容资产的质量契约」，防的是：
  - 已修正的错别字/事实错误回流（暖昧、杭州亚运）
  - 核心履历锚点被手抖删掉（角色会"失忆"）
  - 互相打架的指令回流（只能叫老公 vs 要求换花样）
  - chat/greeting 提示词注入顺序漂移（事实在前、时间收尾）
  - 剧情阶段简述/日期边界常量被改散
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")

import server  # noqa: E402
import story_kpl2027 as story  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


ROOT = Path(__file__).resolve().parent


def _load_real_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ 大帅人设质量
def test_dashuai_persona_quality():
    cfg = _load_real_config()
    p = cfg["roles"]["dashuai"]["persona"]
    check("大帅人设存在且长度合理（1000~4500 字，防误删/防灌水）",
          1000 <= len(p) <= 4500, f"len={len(p)}")

    # —— 已修正的错别字/事实错误不得回流 ——
    check("无错别字「暖昧」（应为暧昧）", "暖昧" not in p, f"出现 {p.count('暖昧')} 次")
    check("无「杭州亚运」（2026 亚运为爱知·名古屋）", "杭州亚运" not in p)
    check("含修正后的「爱知·名古屋」亚运履历", "爱知·名古屋" in p and "亚运" in p)

    # —— 称呼规则：核心保留，但不得再有自相矛盾的绝对句 ——
    check("称呼核心保留：叫老公、自称老婆的规则在",
          "老公" in p and "老婆" in p)
    check("不再有「任何时候都只能称呼用户为老公」的绝对句（与换花样规则打架）",
          "都只能称呼用户为" not in p and "只能称呼用户为" not in p)
    check("「老公为主 + 偶尔换花样」的协调表述存在",
          "主称呼永远叫「老公」" in p and ("宝贝" in p and "我家那位" in p))
    check("男生性别底线在（不女性化自称）",
          "人家" in p and "本宝宝" in p and "不夹杂英文" in p)

    # —— 核心履历锚点（删掉任何一个角色都会在相关话题上"失忆"）——
    anchors = ["游走位", "孟家俊", "武汉", "榜眼", "苏烈", "FMVP",
               "KWC", "广州TTG", "六连冠", "轩染", "钟意", "长生", "一诺",
               "杨玉环", "太乙真人", "盾山", "巅峰赛全国第一", "高达"]
    missing = [a for a in anchors if a not in p]
    check("核心履历/队友/英雄锚点齐全", not missing, f"缺失: {missing}")

    # —— 高价值性格原话保留（模型模仿说话风格的语料）——
    quotes = ["自主意识强", "一切都在情理之中", "蛋蛋", "一旦没做到就很尴尬"]
    missing_q = [q for q in quotes if q not in p]
    check("性格原话语料保留", not missing_q, f"缺失: {missing_q}")

    # —— 去重收益：同一规则不应在人设里高频刷屏 ——
    check("「老公」出现次数收敛（去重前 18 次）", p.count("老公") <= 18,
          f"{p.count('老公')} 次")

    # —— 与系统层的分工：人设不必再重复整套服从铁律（系统固定注入）——
    # 允许角色语气版表达，但系统铁律原文不应被复制进人设
    check("人设不复制系统铁律原文（避免同一段指令发两遍）",
          "对方用户的具体关系——恋人/朋友——以该角色自己的人设为准" not in p)


def test_xiaoni_persona_basic():
    cfg = _load_real_config()
    p = cfg["roles"]["xiaoni"]["persona"]
    check("小拟人设存在且为好友向（不含恋人称呼）",
          bool(p) and "老公" not in p and "好朋友" in p)


# ------------------------------------------------------------ 提示词注入顺序契约
def test_dynamic_hints_assembly_contract():
    # 纯函数：同输入同输出（chat 与 greeting 共用，任何一条链路看到的序列都一样）
    ctx = {"time": "x", "self": "x", "news": "", "location": "", "weather": ""}
    a = server._assemble_dynamic_hints("【此刻上下文】", ctx, "", "", "")
    b = server._assemble_dynamic_hints("【此刻上下文】", dict(ctx), "", "", "")
    check("动态段组装为确定性纯函数", a == b)

    # 序列契约：事实块在前、时间指令收尾（时间永远是最后一条动态指令）。
    # 用各块起始位置断言顺序，不绑定具体文案（文案润色不应导致测试失败）。
    check("注入顺序：事实块开头", a.startswith("【此刻上下文】"))
    time_pos = a.rfind("【当前真实时间】")
    check("注入顺序：时间块存在且收尾", time_pos > 0 and a.rstrip().find("。", time_pos) > time_pos)
    for later_marker in ("【你此刻在哪里", "【你的现实动态】", "【用户位置】",
                         "【今日天气】", "【2027赛季"):
        pos = a.find(later_marker)
        check(f"{later_marker} 不得排在时间块之后", not (0 <= time_pos < pos),
              f"time={time_pos} {later_marker}={pos}")
    story_pos = a.find("【2027赛季")
    check("剧情块在时间指令之前", 0 <= story_pos < time_pos or story_pos == -1,
          f"story={story_pos} time={time_pos}")

    # 空上下文：不产生事实块，只保留该出现的指令（非 story 角色无剧情块）
    empty = server._assemble_dynamic_hints("", {}, "", "", "")
    check("空上下文无事实块", "【此刻上下文】" not in empty)

    # 统一上下文构建：引擎关闭直接空，绝不抛异常
    off = asyncio.run(server._build_role_context(
        False, "r", {}, "q", "", "", "", "", "", log_label="t"))
    check("引擎关闭时上下文为空且不抛错", off == ("", {}))


# ------------------------------------------------------------ 剧情常量与注入
def test_story_constants_and_guides():
    check("阶段名常量 4 档", len(story.STAGE_NAMES) == 4
          and story.STAGE_NAMES[0] == "暗恋隐忍" and story.STAGE_NAMES[3] == "在一起")
    check("阶段简述常量 4 档且与阶段名对应",
          len(story.STAGE_SHORT) == 4
          and all(story.STAGE_NAMES[i] in story.STAGE_SHORT[i] for i in range(4)))
    check("日期边界顺序正确（入队 < 集结 < 开赛 < 年总 < 年末）",
          story.DATE_JOIN < story.DATE_CAMP < story.DATE_SEASON_START
          < story.DATE_ANNUAL_FINAL < story.DATE_SEASON_END)
    check("日期边界与锚点一致", story.ANCHOR_VIRTUAL == date(2026, 8, 8))


def test_story_day_guide_uses_stage_short(tmp_path=None):
    """_day_guide 各日类型输出必须带当前阶段简述（常量是唯一来源）。"""
    import tempfile
    from pathlib import Path as _P
    d = _P(tempfile.mkdtemp(prefix="pq_story_"))
    try:
        sm = story.StoryManager(str(d))

        def guide_for(virtual: str, stage: int = 0) -> str:
            sm.state["stage"] = stage
            info = sm.day_info(story._d(virtual))
            return sm._day_guide(info, story._d(virtual))

        # 铺垫期（入队前）含路人王设定
        g_pre = guide_for("2026-10-01", 0)
        check("铺垫期指引含路人王/素未谋面",
              "路人王" in g_pre and "素未谋面" in g_pre, g_pre[:60])
        # 官宣后、见面前
        g_joined = guide_for("2026-12-25", 0)
        check("官宣入队期指引含旧工位/首日设定",
              "一诺的旧工位" in g_joined and "还没和岚风线下说过话" in g_joined,
              g_joined[:60])

        # 开赛后按 kind 分流的四个分支（直接构造 info，避开固定种子赛程日的干扰；
        # 日期选开赛后保证不落入三个铺垫期提前返回分支）
        d_season = story.DATE_SEASON_START

        def guide_kind(kind: str, stage: int, match=None) -> str:
            sm.state["stage"] = stage
            info = {"stage": stage, "kind": kind, "match": match, "event": None}
            return sm._day_guide(info, d_season)

        check("训练日指引含阶段简述",
              story.STAGE_SHORT[1] in guide_kind("train", 1))
        check("休赛日指引含阶段简述",
              story.STAGE_SHORT[2] in guide_kind("off", 2))
        check("事件日指引含阶段简述",
              story.STAGE_SHORT[3] in guide_kind("event", 3))
        g_match_pending = guide_kind("match", 0, {"opponent": "北京WB", "bo": "BO5",
                                                  "label": "常规赛第一轮", "result": None})
        check("待赛比赛日指引含对阵与阶段简述",
              "北京WB" in g_match_pending and story.STAGE_SHORT[0] in g_match_pending,
              g_match_pending[:80])
        g_match_done = guide_kind("match", 1, {"opponent": "北京WB", "bo": "BO5",
                                               "label": "常规赛",
                                               "result": {"win": True, "score": "3:1"}})
        check("已赛比赛日指引围绕已定赛果",
              "3:1" in g_match_done and "胜" in g_match_done, g_match_done[:80])
        g_match_skip = guide_kind("match", 1, {"opponent": "北京WB", "bo": "BO7",
                                               "label": "季后赛",
                                               "result": {"win": False, "score": "-"}})
        check("取消场次指引说明未晋级", "取消" in g_match_skip and "未晋级" in g_match_skip)

        # status_payload 暴露阶段名常量
        payload = sm.status_payload()
        check("status_payload 阶段名来自常量",
              payload["stage_names"] == list(story.STAGE_NAMES))
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------ 标注器枚举单一真源
def test_story_flags_single_source():
    import role_engine
    check("STORY_FLAGS 枚举含两个剧情节点",
          role_engine.STORY_FLAGS == ("command_win", "confession"))
    # PostProcessor 解析必须引用同一枚举：非法 flag 归零
    ann = role_engine.PostProcessor._parse(
        '{"emotion":{"valence":0,"arousal":0},"energy_delta":0,'
        '"memories":[],"story_result":null,"story_flag":"made_up_flag"}')
    check("未知 flag 被统一枚举拒绝", ann is not None and ann["story_flag"] is None)
    ann2 = role_engine.PostProcessor._parse(
        '{"emotion":{"valence":0,"arousal":0},"energy_delta":0,'
        '"memories":[],"story_result":null,"story_flag":"confession"}')
    check("合法 flag 正常通过", ann2 is not None and ann2["story_flag"] == "confession")


# ------------------------------------------------------------ 标注提示词按角色裁剪
def test_postprocessor_prompt_role_aware():
    import role_engine
    full = role_engine.PostProcessor.build_system(True)
    lite = role_engine.PostProcessor.build_system(False)

    check("剧情版标注提示词含赛果/表白指令",
          "story_result" in full and "岚风" in full
          and all(f in full for f in role_engine.STORY_FLAGS))
    check("普通版标注提示词不含任何剧情字段/角色名（省 token 防串戏）",
          "story_result" not in lite and "story_flag" not in lite and "岚风" not in lite,
          f"len={len(lite)}")
    check("普通版仍保留情绪/精力/记忆核心标注",
          "emotion" in lite and "energy_delta" in lite and "memories" in lite)
    check("普通版明显更短（实测约省 48%）", len(lite) < len(full) * 0.6,
          f"{len(lite)} vs {len(full)}")
    # 默认构造保持旧行为（含剧情段），既有直调方零改动
    class _FakeLLM:
        async def __call__(self, *a):
            return "{}"
    pp_default = role_engine.PostProcessor(_FakeLLM())
    check("默认构造兼容旧行为（剧情段在）", pp_default.system == full)
    pp_lite = role_engine.PostProcessor(_FakeLLM(), include_story=False)
    check("include_story=False 生效", pp_lite.system == lite)
    # 剧情版必须仍是合法的「输出示例 JSON」前缀（大括号成对）
    check("两版示例 JSON 大括号成对", full.count("{") == full.count("}")
          and lite.count("{") == lite.count("}"))


def test_postprocessor_factory_buckets_by_role():
    # 当前激活角色（dashuai，非剧情）→ 普通标注器
    if server._story_role():
        return  # 测试环境恰好是剧情角色时跳过此分支（下面手动覆盖两个方向）
    pp = server._get_post_processor()
    check("当前非剧情角色的标注器不含岚风", "岚风" not in pp.system)

    orig = server._story_role
    try:
        server._story_role = lambda: True
        pp_story = server._get_post_processor()
        check("切到剧情角色后标注器含剧情指令", "岚风" in pp_story.system)
        check("剧情/普通标注器是两个独立实例（运行时切换安全）", pp_story is not pp)
        server._story_role = lambda: False
        check("切回普通角色复用原实例", server._get_post_processor() is pp)
    finally:
        server._story_role = orig


def test_recognizer_prompt_uses_flag_enum():
    import role_engine
    s = server._STORY_RECOGNIZER_SYSTEM
    check("识别器提示词插值了枚举里的两个 flag",
          all(f in s for f in role_engine.STORY_FLAGS))
    check("识别器示例 JSON 大括号成对（无 f-string 转义残留）",
          s.count("{") == s.count("}") and "{{" not in s)


def main():
    test_dashuai_persona_quality()
    test_xiaoni_persona_basic()
    test_dynamic_hints_assembly_contract()
    test_story_constants_and_guides()
    test_story_day_guide_uses_stage_short()
    test_story_flags_single_source()
    test_postprocessor_prompt_role_aware()
    test_postprocessor_factory_buckets_by_role()
    test_recognizer_prompt_uses_flag_enum()
    print(f"\n{'=' * 50}\n人设/提示词质量测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
