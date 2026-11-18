import unittest

from scripts import dex_core as dc


def cfg(per_award=2, weread_per=900):
    return {
        "version": 1,
        "perAward": per_award,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "unit": "秒", "per": weread_per,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
        },
    }


class PendingDaysTest(unittest.TestCase):
    def test_day_below_threshold_not_pending(self):
        led = {"2026-11-16": {"weread": 900}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), [])

    def test_day_at_threshold_pending(self):
        led = {"2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), ["2026-11-16"])

    def test_already_awarded_day_excluded(self):
        led = {"2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, {"2026-11-16"}), [])

    def test_multiple_days_sorted_ascending(self):
        led = {"2026-11-17": {"weread": 1800}, "2026-11-15": {"weread": 1800},
               "2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, set()),
                         ["2026-11-15", "2026-11-16", "2026-11-17"])

    def test_mixed_sources_accumulate(self):
        led = {"2026-11-16": {"weread": 900, "pom": 25}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), ["2026-11-16"])


class BuildStateTest(unittest.TestCase):
    def _award(self, pid="0001", day="2026-11-16"):
        return {"id": pid, "nameZh": "妙蛙种子", "shiny": False,
                "at": "2026-11-16T22:00:00+08:00", "date": day,
                "sources": ["weread"], "form": pid}

    def test_shape_is_v2(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["version"], 2)
        for key in ("collected", "streak", "lastAwardDate", "today", "ledger", "chains"):
            self.assertIn(key, st)

    def test_appends_award(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {},
                            {}, {}, [self._award()])
        self.assertEqual(len(st["collected"]), 1)
        self.assertEqual(st["collected"][0]["id"], "0001")
        self.assertEqual(st["lastAwardDate"], "2026-11-16")
        self.assertEqual(st["streak"], 1)

    def test_today_keeps_legacy_fields(self):
        """现有读方依赖 today.seconds / today.threshold —— 不能没有。"""
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["today"]["seconds"], 1800)
        self.assertEqual(st["today"]["threshold"], 1800)
        self.assertEqual(st["today"]["date"], "2026-11-16")

    def test_threshold_is_cost_of_one_pokemon(self):
        """threshold 是「掉一只实际要读多久」：perAward × weread.per。"""
        st = dc.build_state({"collected": []}, cfg(per_award=3), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["today"]["threshold"], 2700)

    def test_threshold_follows_weread_per(self):
        st = dc.build_state({"collected": []}, cfg(weread_per=600), "2026-11-16",
                            {"weread": 600}, {}, {}, [])
        self.assertEqual(st["today"]["threshold"], 1200)

    def test_streak_decays_after_a_gap(self):
        """断签后连读天数归零 —— 相对 v1 的有意变更（v1 一直挂着一个不衰减的旧值）。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-12", "sources": ["weread"], "form": "0001"},
                             {"id": "0002", "nameZh": "b", "shiny": False, "at": "x",
                              "date": "2026-11-13", "sources": ["weread"], "form": "0002"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0003", "nameZh": "c", "shiny": False, "at": "x",
                              "date": "2026-11-14", "sources": ["weread"], "form": "0003"}])
        # 11-15 没过 → 连读断了
        self.assertEqual(st["streak"], 0)

    def test_streak_counts_from_yesterday_when_today_not_awarded(self):
        """今天还没达标，但昨天发过奖 → 连读从昨天数起（不断）。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-15", "sources": ["weread"], "form": "0001"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {}, [])
        self.assertEqual(st["streak"], 1)

    def test_streak_breaks_when_new_day_awarded(self):
        """今天也发了奖 → 断签重算，锚在今天。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-14", "sources": ["weread"], "form": "0001"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0002", "nameZh": "b", "shiny": False, "at": "x",
                              "date": "2026-11-16", "sources": ["weread"], "form": "0002"}])
        self.assertEqual(st["streak"], 1)

    def test_disabled_source_dropped_from_today_sources(self):
        """today.sources 与 credit 口径一致：禁用源不参与。"""
        c = cfg()
        c["sources"]["words"] = {"name": "背单词", "unit": "个", "per": 20,
                                 "mode": "push", "enabled": False}
        st = dc.build_state({"collected": []}, c, "2026-11-16",
                            {"weread": 900, "words": 100}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"weread": 900})
        self.assertEqual(st["today"]["credit"], 1)

    def test_empty_state(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {}, {}, {}, [])
        self.assertIsNone(st["lastAwardDate"])
        self.assertEqual(st["streak"], 0)
        self.assertEqual(st["collected"], [])

    def test_does_not_mutate_inputs(self):
        old = {"collected": [{"id": "0001", "date": "2026-11-14"}]}
        today_sources = {"weread": 900}
        ledger_data = {"2026-11-16": {"weread": 900}}
        chains = {"妙蛙种子": {"care": 1, "pendingShiny": 0, "nextShinyAt": 30}}
        dc.build_state(old, cfg(), "2026-11-16", today_sources, ledger_data, chains,
                       [{"id": "0002", "date": "2026-11-16"}])
        self.assertEqual(old, {"collected": [{"id": "0001", "date": "2026-11-14"}]})
        self.assertEqual(today_sources, {"weread": 900})
        self.assertEqual(ledger_data, {"2026-11-16": {"weread": 900}})
        self.assertEqual(chains, {"妙蛙种子": {"care": 1, "pendingShiny": 0, "nextShinyAt": 30}})

    def test_same_day_two_awards_are_kept(self):
        """build_state 不按日期去重 —— 幂等由调用方保证（见 docstring）。"""
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0001", "date": "2026-11-16"},
                             {"id": "0002", "date": "2026-11-16"}])
        self.assertEqual(len(st["collected"]), 2)

    def test_today_has_new_fields(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 900, "pom": 50}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"weread": 900, "pom": 50})
        self.assertEqual(st["today"]["credit"], 3)
        self.assertEqual(st["today"]["perAward"], 2)

    def test_zero_amount_sources_dropped_from_today(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 0, "pom": 50}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"pom": 50})

    def test_streak_counts_consecutive_days(self):
        old = {"collected": [self._award("0001", "2026-11-15")]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [self._award("0002", "2026-11-16")])
        self.assertEqual(st["streak"], 2)

    def test_last_award_date_is_max(self):
        old = {"collected": [self._award("0001", "2026-11-15")]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [self._award("0002", "2026-11-16")])
        self.assertEqual(st["lastAwardDate"], "2026-11-16")

    def test_ledger_and_chains_passed_through(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {},
                            {"2026-11-16": {"weread": 900}},
                            {"妙蛙种子": {"care": 1, "pendingShiny": 0, "nextShinyAt": 30}}, [])
        self.assertEqual(st["ledger"], {"2026-11-16": {"weread": 900}})
        self.assertEqual(st["chains"], {"妙蛙种子": {"care": 1, "pendingShiny": 0, "nextShinyAt": 30}})


if __name__ == "__main__":
    unittest.main()
