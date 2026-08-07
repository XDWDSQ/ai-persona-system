# -*- coding: utf-8 -*-
"""story_kpl2027 引擎单测：日历生成 / 确定性 / 跳转 / 比赛记录 / 状态机。"""
import os
import shutil
import tempfile
import unittest
from datetime import date

from story_kpl2027 import KPL2027Calendar, StoryManager, _d


class TmpDir:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="story_test_")
        return self.dir

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestCalendar(unittest.TestCase):
    def test_generate_deterministic(self):
        with TmpDir() as d1, TmpDir() as d2:
            c1 = KPL2027Calendar.generate()
            c2 = KPL2027Calendar.generate()
            self.assertEqual(c1["matches"], c2["matches"])
            self.assertEqual(len(c1["matches"]), len(c2["matches"]))

    def test_schedule_shape(self):
        cal = KPL2027Calendar.generate()
        matches = cal["matches"]
        self.assertGreaterEqual(len(matches), 45, "全年比赛应 ≥45 场")
        self.assertLessEqual(len(matches), 65, "全年比赛应 ≤65 场")
        # 每个赛段都有比赛且日期在赛段内
        for m in matches:
            stage = next(s for s in cal["stages"] if s["key"] == m["stage"])
            self.assertGreaterEqual(_d(m["date"]), _d(stage["start"]))
            self.assertLessEqual(_d(m["date"]), _d(stage["end"]))
        # 春季赛开赛日 2027-01-14 之后首场比赛应接近开赛
        first = min(m["date"] for m in matches if m["stage"].startswith("spring_r1"))
        self.assertGreaterEqual(_d(first), _d("2027-01-14"))
        self.assertLessEqual(_d(first), _d("2027-01-20"))
        # 总决赛存在
        finals = [m for m in matches if m["stage"].endswith("final")]
        self.assertEqual(len(finals), 3)

    def test_load_or_generate_persistent(self):
        with TmpDir() as d:
            c1 = KPL2027Calendar.load_or_generate(d)
            c2 = KPL2027Calendar.load_or_generate(d)
            self.assertEqual(c1.data["matches"], c2.data["matches"])


class TestStoryManager(unittest.TestCase):
    def _mk(self, tmp):
        return StoryManager(tmp)

    def test_clock_follow(self):
        with TmpDir() as d:
            sm = self._mk(d)
            # 锚点 2026-08-08 → 2026-08-08；2027-01-14 → 2027-01-14
            self.assertEqual(sm.current_date(date(2026, 8, 8)), date(2026, 8, 8))
            self.assertEqual(sm.current_date(date(2027, 1, 14)), date(2027, 1, 14))
            self.assertEqual(sm.current_date(date(2026, 12, 20)), date(2026, 12, 20))

    def test_jump_and_resume(self):
        with TmpDir() as d:
            sm = self._mk(d)
            self.assertTrue(sm.jump("2027-03-27"))
            self.assertEqual(sm.current_date(), date(2027, 3, 27))
            self.assertEqual(sm.state["mode"], "override")
            # 越界拒绝
            self.assertFalse(sm.jump("2025-01-01"))
            self.assertFalse(sm.jump("2028-01-01"))
            sm.resume()
            self.assertEqual(sm.state["mode"], "follow")
            self.assertEqual(sm.current_date(date(2026, 8, 9)), date(2026, 8, 9))

    def test_day_info_match_day(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2027-01-16")
            info = sm.day_info()
            self.assertEqual(info["kind"], "match")
            self.assertTrue(info["title"].startswith("比赛日"))

    def test_day_info_prelude_event(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2026-12-20")
            info = sm.day_info()
            self.assertEqual(info["kind"], "join")
            self.assertIn("岚风", info["title"])

    def test_record_result_and_stage(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2027-01-16")
            m = sm.cal.match_on(_d("2027-01-16"))
            self.assertIsNotNone(m)
            # 赢：战绩更新，阶段不动
            r = sm.record_result("2027-01-16", True, "3:1", "岚风")
            self.assertTrue(r["ok"])
            self.assertEqual(sm.state["record"]["win"], 1)
            self.assertEqual(sm.state["stage"], 0)
            # 重复记录拒绝
            r2 = sm.record_result("2027-01-16", False)
            self.assertFalse(r2["ok"])
            # 找一场后续比赛输了 → 首败 → 阶段 1
            next_m = next(mm for mm in sm.cal.data["matches"] if mm["date"] > "2027-01-16" and mm["status"] == "pending")
            sm.record_result(next_m["date"], False, "1:3")
            self.assertEqual(sm.state["stage"], 1)
            self.assertTrue(sm.state["flags"]["first_fight"])

    def test_stage_transitions(self):
        with TmpDir() as d:
            sm = self._mk(d)
            # 0→1 需要首败
            sm.set_flag("first_loss", True, "首败")
            self.assertEqual(sm.state["stage"], 1)
            # 1→2 指挥权
            sm.set_flag("command_win", True, "岚风赢下指挥权")
            self.assertEqual(sm.state["stage"], 2)
            # 2→3 攻略
            sm.set_flag("confession", True, "岚风表白了")
            self.assertEqual(sm.state["stage"], 3)

    def test_playoff_loss_skips_following(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2027-03-27")
            sm.record_result("2027-03-27", False, "1:4")
            m2 = sm.cal.match_on(_d("2027-03-31"))
            m3 = sm.cal.match_on(_d("2027-04-11"))
            self.assertEqual(m2["status"], "skipped")
            self.assertEqual(m3["status"], "skipped")

    def test_story_context(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2027-02-10")
            ctx = sm.story_context()
            self.assertIn("2027赛季·虚拟日历", ctx)
            self.assertIn("虚拟日期", ctx)
            self.assertIn("暗恋隐忍", ctx)
            self.assertIn("时间规则", ctx)
            self.assertIn("比赛结果由岚风在对话中宣布", ctx)

    def test_season_end_flag(self):
        with TmpDir() as d:
            sm = self._mk(d)
            sm.jump("2027-12-01")
            sm._update_stage()
            self.assertTrue(sm.state["flags"]["season_end"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
