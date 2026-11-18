import unittest

from scripts import ledger as lg


class MergeAmountsTest(unittest.TestCase):
    def test_adds_new_day(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        self.assertEqual(got, {"2026-11-16": {"weread": 900}})

    def test_same_day_takes_max_not_sum(self):
        """重复投递是幂等的：取 max，不叠加。"""
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        b = lg.merge_amounts(a, "weread", {"2026-11-16": 1200})
        self.assertEqual(b["2026-11-16"]["weread"], 1200)

    def test_lower_repeat_does_not_overwrite(self):
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 1200})
        b = lg.merge_amounts(a, "weread", {"2026-11-16": 900})
        self.assertEqual(b["2026-11-16"]["weread"], 1200)

    def test_other_sources_coexist(self):
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        b = lg.merge_amounts(a, "pom", {"2026-11-16": 50})
        self.assertEqual(b["2026-11-16"], {"weread": 900, "pom": 50})

    def test_negative_amount_ignored(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": -5})
        self.assertEqual(got, {})

    def test_none_amount_ignored(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": None})
        self.assertEqual(got, {})

    def test_does_not_mutate_input(self):
        original = {"2026-11-16": {"weread": 900}}
        lg.merge_amounts(original, "pom", {"2026-11-16": 50})
        self.assertEqual(original, {"2026-11-16": {"weread": 900}})


class PruneTest(unittest.TestCase):
    def test_keeps_today_and_recent(self):
        led = {"2026-11-14": {"weread": 900},
               "2026-11-16": {"weread": 900},
               "2026-11-17": {"weread": 900}}
        got = lg.prune(led, 3, "2026-11-16")
        self.assertEqual(sorted(got), ["2026-11-14", "2026-11-16"])

    def test_drops_future_days(self):
        led = {"2026-11-16": {"weread": 900}, "2026-11-20": {"weread": 900}}
        got = lg.prune(led, 30, "2026-11-16")
        self.assertEqual(sorted(got), ["2026-11-16"])

    def test_keeps_exactly_keep_days(self):
        led = {f"2026-11-{d:02d}": {"weread": 900} for d in range(1, 11)}
        got = lg.prune(led, 5, "2026-11-10")
        self.assertEqual(sorted(got), ["2026-11-06", "2026-11-07",
                                       "2026-11-08", "2026-11-09", "2026-11-10"])


class MigrateStateTest(unittest.TestCase):
    def test_v1_gets_version_two(self):
        st = lg.migrate_state({"version": 1, "collected": []})
        self.assertEqual(st["version"], 2)

    def test_v1_entries_get_sources_and_form(self):
        st = lg.migrate_state({"version": 1, "collected": [
            {"id": "0563", "nameZh": "迭失棺", "shiny": False, "at": "x", "date": "2026-11-14"}]})
        c = st["collected"][0]
        self.assertEqual(c["sources"], ["weread"])
        self.assertEqual(c["form"], "0563")

    def test_v1_gets_empty_ledger_and_chains(self):
        st = lg.migrate_state({"version": 1, "collected": []})
        self.assertEqual(st["ledger"], {})
        self.assertEqual(st["chains"], {})

    def test_already_v2_keeps_ledger(self):
        st = lg.migrate_state({"version": 2, "collected": [],
                               "ledger": {"2026-11-16": {"weread": 900}},
                               "chains": {"a→b": {"care": 1, "stage": 0}}})
        self.assertEqual(st["ledger"], {"2026-11-16": {"weread": 900}})
        self.assertEqual(st["chains"], {"a→b": {"care": 1, "stage": 0}})

    def test_v2_entry_keeps_explicit_sources(self):
        st = lg.migrate_state({"version": 2, "collected": [
            {"id": "0025", "sources": ["weread", "pom"], "form": "0026"}]})
        self.assertEqual(st["collected"][0]["sources"], ["weread", "pom"])
        self.assertEqual(st["collected"][0]["form"], "0026")

    def test_does_not_mutate_input(self):
        original = {"version": 1, "collected": [{"id": "1"}]}
        lg.migrate_state(original)
        self.assertEqual(original["collected"][0], {"id": "1"})

    def test_migrate_state_does_not_alias_ledger_or_chains(self):
        """返回值与入参不能共享 ledger / chains 的内层 dict。"""
        src = {"ledger": {"2026-11-14": {"weread": 900}},
               "chains": {"妙蛙种子": {"care": 1, "pendingShiny": 0,
                                       "nextShinyAt": 30}}}
        out = lg.migrate_state(src)
        out["ledger"]["2026-11-14"]["weread"] = 1800
        out["chains"]["妙蛙种子"]["care"] = 99
        self.assertEqual(src["ledger"]["2026-11-14"]["weread"], 900)
        self.assertEqual(src["chains"]["妙蛙种子"]["care"], 1)


class EmptyLedgerTest(unittest.TestCase):
    """空账本是新账本的起点：必须是空 dict，而且每次都返回新对象。"""

    def test_starts_empty(self):
        self.assertEqual(lg.empty_ledger(), {})

    def test_returns_fresh_dict_each_call(self):
        a = lg.empty_ledger()
        a["2026-11-16"] = {"weread": 900}
        # 每次调用返回新对象：调用方就地改自己的账本，不会污染别人的
        self.assertEqual(lg.empty_ledger(), {})


if __name__ == "__main__":
    unittest.main()
