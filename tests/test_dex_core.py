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


class BuildStateRegressionTest(unittest.TestCase):
    """v1 的 new_state 已被 build_state 取代，这些是它的回归等价物。"""

    def _cfg(self):
        return {
            "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
            "ledgerDays": 30,
            "sources": {"weread": {"name": "微信读书", "unit": "秒", "per": 900,
                                   "mode": "pull", "enabled": True,
                                   "maxPerDay": 86400}},
        }

    def _award(self, pid="0001", day="2026-11-14", shiny=False):
        return {"id": pid, "nameZh": "妙蛙种子", "shiny": shiny,
                "at": f"{day}T22:00:00+08:00", "date": day,
                "sources": ["weread"], "form": pid}

    def test_appends_award(self):
        st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
                            {"weread": 1800}, {}, {}, [self._award()])
        self.assertEqual(len(st["collected"]), 1)
        self.assertEqual(st["collected"][0]["id"], "0001")
        self.assertEqual(st["lastAwardDate"], "2026-11-14")
        self.assertEqual(st["streak"], 1)
        self.assertEqual(st["today"]["seconds"], 1800)
        self.assertEqual(st["today"]["threshold"], 1800)

    def test_is_idempotent_same_day(self):
        """同一天重复结算不会多出一条 —— 幂等由调用方保证，这里验证状态组装。"""
        once = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
                              {"weread": 1800}, {}, {}, [self._award()])
        twice = dc.build_state(once, self._cfg(), "2026-11-14",
                               {"weread": 2400}, {}, {}, [])
        self.assertEqual(len(twice["collected"]), 1)
        self.assertEqual(twice["today"]["seconds"], 2400)

    def test_awards_for_past_date_not_today(self):
        old = {"collected": []}
        st = dc.build_state(old, self._cfg(), "2026-11-15", {},
                            {}, {}, [self._award("0001", "2026-11-14")])
        self.assertEqual(st["collected"][0]["date"], "2026-11-14")
        self.assertEqual(st["lastAwardDate"], "2026-11-14")
        self.assertEqual(st["today"]["seconds"], 0)
        self.assertEqual(st["today"]["date"], "2026-11-15")

    def test_shiny_flag_preserved(self):
        st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
                            {}, {}, {}, [self._award(shiny=True)])
        self.assertTrue(st["collected"][0]["shiny"])

    def test_collected_entries_carry_date(self):
        st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
                            {}, {}, {}, [self._award()])
        self.assertEqual(st["collected"][0]["date"], "2026-11-14")

    def test_streak_decays_after_a_gap(self):
        """断签后连读天数归零 —— 相对 v1 的有意变更。"""
        old = {"collected": [self._award("0001", "2026-11-12"),
                             self._award("0002", "2026-11-13")]}
        st = dc.build_state(old, self._cfg(), "2026-11-16", {}, {}, {},
                            [self._award("0003", "2026-11-14")])
        self.assertEqual(st["streak"], 0)

    def test_does_not_alias_ledger_or_chains(self):
        """账本与链状态是共享状态 —— 返回值不能跟入参共用内层 dict。"""
        ledger_data = {"2026-11-14": {"weread": 1800}}
        chains = {"妙蛙种子": {"care": 1, "pendingShiny": 0, "nextShinyAt": 30}}
        st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
                            {"weread": 1800}, ledger_data, chains, [])
        st["ledger"]["2026-11-14"]["weread"] = 9999
        st["chains"]["妙蛙种子"]["care"] = 99
        self.assertEqual(ledger_data["2026-11-14"]["weread"], 1800)
        self.assertEqual(chains["妙蛙种子"]["care"], 1)


if __name__ == "__main__":
    unittest.main()

