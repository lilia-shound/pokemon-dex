import random
import unittest

from scripts import dex_core as dc


class ShinyProbabilityTest(unittest.TestCase):
    def test_probability_by_streak(self):
        self.assertAlmostEqual(dc.shiny_probability(0), 1 / 96)
        self.assertAlmostEqual(dc.shiny_probability(2), 1 / 96)
        self.assertAlmostEqual(dc.shiny_probability(3), 1 / 48)
        self.assertAlmostEqual(dc.shiny_probability(6), 1 / 48)
        self.assertAlmostEqual(dc.shiny_probability(7), 1 / 32)
        self.assertAlmostEqual(dc.shiny_probability(30), 1 / 16)
        self.assertAlmostEqual(dc.shiny_probability(99), 1 / 16)
        self.assertEqual(dc.shiny_probability(100), 1.0)
        self.assertEqual(dc.shiny_probability(999), 1.0)


class ComputeStreakTest(unittest.TestCase):
    def test_counts_consecutive_days(self):
        self.assertEqual(dc.compute_streak(["2026-11-12", "2026-11-13"], "2026-11-14"), 2)

    def test_today_included_when_awarded(self):
        self.assertEqual(dc.compute_streak(["2026-11-14"], "2026-11-14"), 1)

    def test_breaks_on_gap(self):
        self.assertEqual(dc.compute_streak(["2026-11-10", "2026-11-12"], "2026-11-13"), 1)

    def test_zero_when_today_and_yesterday_missing(self):
        self.assertEqual(dc.compute_streak(["2026-11-12"], "2026-11-14"), 0)

    def test_counts_from_yesterday_when_today_not_awarded(self):
        """今天还没读，但昨天连着读了三天 -> 显示 3（今天读满就变 4）。"""
        dates = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
        self.assertEqual(dc.compute_streak(dates, "2026-11-14"), 4)

    def test_empty(self):
        self.assertEqual(dc.compute_streak([], "2026-11-14"), 0)


class PickTest(unittest.TestCase):
    def test_excludes_owned(self):
        pool = [{"id": "0001", "nameZh": "妙蛙种子"}, {"id": "0002", "nameZh": "妙蛙草"}]
        got = dc.pick({"0001"}, pool, 0, random.Random(1))
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "0002")

    def test_returns_none_when_pool_empty(self):
        self.assertIsNone(dc.pick({"0001"}, [{"id": "0001"}], 0, random.Random(1)))

    def test_marks_shiny_flag(self):
        pool = [{"id": "0001", "nameZh": "妙蛙种子"}]
        got = dc.pick(set(), pool, 0, random.Random(0))
        self.assertIsNotNone(got)
        self.assertIn("shiny", got)
        self.assertIsInstance(got["shiny"], bool)

    def test_streak_100_always_shiny(self):
        pool = [{"id": f"{i:04d}", "nameZh": f"p{i}"} for i in range(1, 200)]
        for seed in range(30):
            got = dc.pick(set(), pool, 100, random.Random(seed))
            self.assertTrue(got["shiny"], f"seed={seed} 连读 100 天必须保底闪光")

    def test_does_not_mutate_pool_entry(self):
        pool = [{"id": "0001", "nameZh": "妙蛙种子"}]
        dc.pick(set(), pool, 0, random.Random(3))
        self.assertNotIn("shiny", pool[0])


class NewStateTest(unittest.TestCase):
    def _award(self, pid="0001", shiny=False):
        return {"id": pid, "nameZh": "妙蛙种子", "shiny": shiny,
                "at": "2026-11-14T22:00:00+08:00"}

    def test_appends_award(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        new = dc.new_state(old, self._award(), "2026-11-14", 1800)
        self.assertEqual(len(new["collected"]), 1)
        self.assertEqual(new["collected"][0]["id"], "0001")
        self.assertEqual(new["lastAwardDate"], "2026-11-14")
        self.assertEqual(new["streak"], 1)
        self.assertEqual(new["today"]["seconds"], 1800)
        self.assertEqual(new["today"]["threshold"], dc.THRESHOLD_SECONDS)

    def test_is_idempotent_same_day(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        once = dc.new_state(old, self._award(), "2026-11-14", 1800)
        twice = dc.new_state(once, self._award(), "2026-11-14", 2000)
        self.assertEqual(len(twice["collected"]), 1)
        self.assertEqual(twice["today"]["seconds"], 2000)

    def test_no_award_keeps_collected_and_updates_today(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        new = dc.new_state(old, None, "2026-11-14", 900)
        self.assertEqual(len(new["collected"]), 0)
        self.assertIsNone(new["lastAwardDate"])
        self.assertEqual(new["today"]["seconds"], 900)

    def test_second_day_extends_streak(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        day1 = dc.new_state(old, self._award("0001"), "2026-11-13", 1800)
        day2 = dc.new_state(day1, self._award("0002"), "2026-11-14", 1800)
        self.assertEqual(len(day2["collected"]), 2)
        self.assertEqual(day2["streak"], 2)

    def test_shiny_flag_preserved(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        new = dc.new_state(old, self._award(shiny=True), "2026-11-14", 1800)
        self.assertTrue(new["collected"][0]["shiny"])

    def test_collected_entries_carry_date(self):
        old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
        new = dc.new_state(old, self._award(), "2026-11-14", 1800)
        self.assertEqual(new["collected"][0]["date"], "2026-11-14")


if __name__ == "__main__":
    unittest.main()


class CatchUpTest(unittest.TestCase):
    """补发：微信读书延迟上报时，昨天读满的数据今天才到。"""

    def _state_with(self, dates):
        return {"collected": [{"id": f"{i:04d}", "date": d, "shiny": False}
                              for i, d in enumerate(dates)],
                "streak": 0, "lastAwardDate": None, "today": {}}

    def test_awards_for_past_date_not_today(self):
        old = self._state_with([])
        award = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                 "at": "2026-11-15T00:20:00+08:00"}
        new = dc.new_state(old, award, today="2026-11-15", today_secs=0,
                            award_date="2026-11-14")
        self.assertEqual(len(new["collected"]), 1)
        self.assertEqual(new["collected"][0]["date"], "2026-11-14")
        self.assertEqual(new["lastAwardDate"], "2026-11-14")
        # 今天的秒数照常记，进度条要显示今天读了多久
        self.assertEqual(new["today"]["seconds"], 0)
        self.assertEqual(new["today"]["date"], "2026-11-15")

    def test_streak_counts_up_to_award_date(self):
        old = self._state_with(["2026-11-12", "2026-11-13"])
        award = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                 "at": "2026-11-15T00:20:00+08:00"}
        new = dc.new_state(old, award, today="2026-11-15", today_secs=0,
                            award_date="2026-11-14")
        self.assertEqual(new["streak"], 3)

    def test_same_date_never_awarded_twice(self):
        old = self._state_with(["2026-11-14"])
        award = {"id": "0002", "nameZh": "妙蛙草", "shiny": False,
                 "at": "2026-11-15T00:20:00+08:00"}
        new = dc.new_state(old, award, today="2026-11-15", today_secs=0,
                            award_date="2026-11-14")
        self.assertEqual(len(new["collected"]), 1)
        self.assertEqual(new["collected"][0]["id"], "0000")

    def test_last_award_date_is_max_of_collected(self):
        old = self._state_with(["2026-11-14"])
        award = {"id": "0002", "nameZh": "妙蛙草", "shiny": False,
                 "at": "2026-11-15T22:00:00+08:00"}
        new = dc.new_state(old, award, today="2026-11-15", today_secs=2000)
        self.assertEqual(new["lastAwardDate"], "2026-11-15")
        self.assertEqual(len(new["collected"]), 2)
