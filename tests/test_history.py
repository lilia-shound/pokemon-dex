"""历史数据（`scripts/history.py`）的测试。

★ 最重要的那条：**往 history 里回填历史量，不能影响发奖判定**。
账本（ledger）是结算用的 —— 往里塞历史会让 `pending_days()` 以为那些天「该发奖」，
一口气补发十几只。所以历史必须单独存（`data/history.json`）。

跑法：

    python3 -B run_tests.py
    python3 -B -m tests.test_history
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest

from scripts import dex_core as dc
from scripts import history as hs


class BucketDayTest(unittest.TestCase):
    """桶时间戳 → 日期（北京时间）。"""

    def test_a_bucket_maps_to_its_beijing_day(self):
        # 1795276800 = 2026-11-22 00:00 +08:00（桶 key 就是北京时间的当天零点）
        self.assertEqual(hs.history_day(1795276800), "2026-11-22")
        self.assertEqual(hs.history_day(1795363200), "2026-11-23")

    def test_the_boundary_is_beijing_midnight(self):
        """桶 key 落在北京时间当天零点 —— 差 8 小时就是别的日子。"""
        # 2026-11-22 20:00 +08:00 的桶，属于 11-22；换成 UTC 算是 11-22 12:00
        self.assertEqual(hs.history_day(1795276800 + 20 * 3600), "2026-11-22")
        # 早 8 小时（北京时间 11-22 00:00 之前）就属于 11-21
        self.assertEqual(hs.history_day(1795276800 - 3600), "2026-11-21")


class MergeTest(unittest.TestCase):
    def test_same_source_same_day_takes_max(self):
        got = hs.merge_history({"2026-11-23": {"weread": 100}},
                                {"2026-11-23": {"weread": 300}})
        self.assertEqual(got["2026-11-23"]["weread"], 300)

    def test_a_new_source_is_added(self):
        got = hs.merge_history({"2026-11-23": {"weread": 100}},
                                {"2026-11-23": {"pom": 25}})
        self.assertEqual(got["2026-11-23"], {"weread": 100, "pom": 25})

    def test_negative_amounts_are_dropped(self):
        got = hs.merge_history({}, {"2026-11-23": {"weread": -5}})
        self.assertEqual(got, {})

    def test_merging_does_not_mutate_the_input(self):
        old = {"2026-11-23": {"weread": 100}}
        hs.merge_history(old, {"2026-11-23": {"weread": 300}})
        self.assertEqual(old["2026-11-23"]["weread"], 100)


class PruneTest(unittest.TestCase):
    def test_days_inside_the_window_are_kept(self):
        old = {"2026-11-01": {"weread": 100}, "2026-11-23": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertIn("2026-11-23", got)
        self.assertIn("2026-11-01", got)   # 22 天前，还在窗口里

    def test_days_beyond_the_window_are_dropped(self):
        old = {"2026-09-01": {"weread": 100}, "2026-11-23": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertNotIn("2026-09-01", got)

    def test_future_days_are_dropped(self):
        old = {"2026-12-01": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertNotIn("2026-12-01", got)


class BuildHistoryTest(unittest.TestCase):
    def test_buckets_become_days(self):
        dex = {"ledger": {"2026-11-22": {"weread": 1753, "pom": 25}}}
        buckets = {1795276800: 1800}   # = 2026-11-22
        got = hs.build_history(dex, buckets, "2026-11-23")
        self.assertEqual(got["days"]["2026-11-22"]["weread"], 1800)

    def test_ledger_sources_are_copied_in(self):
        dex = {"ledger": {"2026-11-22": {"weread": 1753, "pom": 25}}}
        got = hs.build_history(dex, {}, "2026-11-23")
        self.assertEqual(got["days"]["2026-11-22"]["pom"], 25)

    def test_a_bucket_bigger_than_the_ledger_wins(self):
        dex = {"ledger": {"2026-11-22": {"weread": 100}}}
        got = hs.build_history(dex, {1795276800: 1800}, "2026-11-23")
        self.assertEqual(got["days"]["2026-11-22"]["weread"], 1800)

    def test_the_result_has_a_version_and_updated_at(self):
        got = hs.build_history({"ledger": {}}, {}, "2026-11-23")
        self.assertEqual(got["version"], 1)
        self.assertEqual(got["updatedAt"], "2026-11-23")

    def test_building_history_does_not_change_the_award_decision(self):
        """★★ 守门测试：回填历史**绝不能**改变 `pending_days()` 的结果。"""
        cfg = {
            "perAward": 2,
            "maxBackfill": 3,
            "ledgerDays": 30,
            "sources": {
                "weread": {"per": 900, "enabled": True, "maxPerDay": 86400},
                "pom": {"per": 25, "enabled": True, "maxPerDay": 1440},
            },
        }
        ledger = {"2026-11-20": {"weread": 1800}}
        awarded = []
        before = dc.pending_days(cfg, ledger, awarded)
        # 回填 13 天历史进 history —— 不碰 ledger
        hs.build_history({"ledger": ledger}, {1795104000: 1800}, "2026-11-23")
        after = dc.pending_days(cfg, ledger, awarded)
        self.assertEqual(before, after)
        self.assertEqual(len(after), 1)   # 只有 11-20 那天待发奖


class HistoryFileTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history.json")
            hs.write_history({"version": 1, "days": {}}, p)
            got = hs.load_history(p)
            self.assertEqual(got["days"], {})

    def test_a_broken_file_gives_an_empty_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history.json")
            pathlib.Path(p).write_text("{oops", encoding="utf-8")
            got = hs.load_history(p)
            self.assertEqual(got["days"], {})

    def test_a_missing_file_gives_an_empty_history(self):
        with tempfile.TemporaryDirectory() as d:
            got = hs.load_history(os.path.join(d, "nope.json"))
            self.assertEqual(got["days"], {})

    def test_the_deploy_script_syncs_the_history_file(self):
        """图鉴页读的是站根那份 ⇒ 部署必须把它带上去。"""
        p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
        text = p.read_text(encoding="utf-8")
        self.assertTrue("data/history.json" in text, "deploy.sh 要同步历史数据")
        self.assertTrue("site/history.json" in text, "deploy.sh 要同步到站根那份")

    def test_the_committed_history_file_is_valid(self):
        p = pathlib.Path(__file__).resolve().parent.parent / "data" / "history.json"
        self.assertTrue(p.exists(), "data/history.json 要入库（哪怕是空的）")
        got = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("days", got)

    def test_main_only_writes_the_history_file(self):
        """★ 铁律：main() **不许**碰 data/dex.json。"""
        with tempfile.TemporaryDirectory() as d:
            dex_p = os.path.join(d, "dex.json")
            pathlib.Path(dex_p).write_text(
                json.dumps({"ledger": {"2026-11-22": {"weread": 100}}}),
                encoding="utf-8")
            hist_p = os.path.join(d, "history.json")
            before = pathlib.Path(dex_p).read_text(encoding="utf-8")
            hs.main(dex_path=dex_p, history_path=hist_p,
                    buckets={1795276800: 1800}, today="2026-11-23")
            after = pathlib.Path(dex_p).read_text(encoding="utf-8")
            self.assertEqual(before, after, "main() 改了 dex.json —— 这是铁律不允许的")
            got = json.loads(pathlib.Path(hist_p).read_text(encoding="utf-8"))
            self.assertEqual(got["days"]["2026-11-22"]["weread"], 1800)


if __name__ == "__main__":
    unittest.main()
