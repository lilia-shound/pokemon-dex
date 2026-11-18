#!/usr/bin/env python3
"""多源结算：额度合并、一次结清、链养成、保底闪光。"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import award


def make_config():
    return {
        "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "icon": "📖", "unit": "秒", "per": 900,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
        },
    }


KEY_20261113 = 1794499200
KEY_20261114 = 1794585600
DAY = "2026-11-14"


class MultiSourceAwardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        # award.DEX_PATH 是模块全局量 —— 用完必须还原，
        # 否则套件跑完之后全局仍指向这个已删除的临时目录，
        # 将来别的测试忘了设 DEX_PATH 就会静默写到陈旧路径。
        orig = award.DEX_PATH
        self.addCleanup(setattr, award, "DEX_PATH", orig)
        award.DEX_PATH = self.dex_path

    def _run(self, buckets, config=None, argv=()):
        out = io.StringIO()
        with mock.patch.object(award, "fetch_buckets", return_value=buckets), \
             mock.patch.object(award, "load_config", return_value=config or make_config()):
            with contextlib.redirect_stdout(out):
                rc = award.main(["--date", DAY, "--no-push", *argv])
        return rc, out.getvalue()

    def _state(self):
        return json.loads(self.dex_path.read_text(encoding="utf-8"))

    def test_single_source_below_threshold_awards_nothing(self):
        rc, out = self._run({KEY_20261114: 900})
        self.assertEqual(rc, 0)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 0)

    def test_single_source_at_threshold_awards(self):
        rc, out = self._run({KEY_20261114: 1800})
        self.assertIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_today_credit_recorded(self):
        self._run({KEY_20261114: 1800})
        today = self._state()["today"]
        self.assertEqual(today["sources"], {"weread": 1800})
        self.assertEqual(today["credit"], 2)
        self.assertEqual(today["perAward"], 2)

    def test_ledger_records_the_day(self):
        self._run({KEY_20261114: 1800})
        self.assertEqual(self._state()["ledger"][DAY], {"weread": 1800})

    def test_collected_entry_records_source(self):
        self._run({KEY_20261114: 1800})
        self.assertEqual(self._state()["collected"][0]["sources"], ["weread"])

    def test_collected_entry_records_form(self):
        self._run({KEY_20261114: 1800})
        entry = self._state()["collected"][0]
        self.assertEqual(entry["form"], entry["id"])

    def test_two_days_catch_up_in_one_run(self):
        """11-13 和 11-14 都达标 → 一次跑完，发两张，按日期升序。"""
        rc, out = self._run({KEY_20261113: 1800, KEY_20261114: 1800})
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual([c["date"] for c in state["collected"]],
                         ["2026-11-13", "2026-11-14"])
        self.assertEqual(state["streak"], 2)

    def test_backfill_rerun_does_not_award_again(self):
        """多日补发重跑同一批数据 —— 幂等，不重复发（设计文档 §10 验收标准）。"""
        buckets = {KEY_20261113: 1800, KEY_20261114: 1800}
        rc, out = self._run(buckets)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._state()["collected"]), 2)
        rc, out = self._run(buckets)
        self.assertEqual(rc, 0)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 2)
        self.assertEqual([c["date"] for c in self._state()["collected"]],
                         ["2026-11-13", "2026-11-14"])

    def test_backfill_capped_at_max_backfill(self):
        """五天都达标，但 maxBackfill=3 → 只补三天。

        桶 key 必须是北京时间午夜。已核实的对照表：
        2026-11-10 → 1794240000
        2026-11-11 → 1794326400
        2026-11-12 → 1794412800
        2026-11-13 → 1794499200
        2026-11-14 → 1794585600
        2026-11-15 → 1794672000
        """
        keys = {1794240000: 1800,   # 11-10
                1794326400: 1800,   # 11-11
                1794412800: 1800,   # 11-12
                1794499200: 1800,   # 11-13
                1794585600: 1800}   # 11-14
        rc, out = self._run(keys)
        self.assertEqual(rc, 0)
        # 这行提示是积压场景唯一的可见信号 —— 被跳过的天必须让用户看见
        self.assertIn("[跳过] 超过 maxBackfill", out)
        self.assertIn("2026-11-13", out)
        self.assertIn("2026-11-14", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 3)
        self.assertEqual([c["date"] for c in state["collected"]],
                         ["2026-11-10", "2026-11-11", "2026-11-12"])

    def test_one_day_one_award_even_with_large_amount(self):
        """一天不管读多久都只掉一只（额度够多也不多发）。"""
        rc, out = self._run({KEY_20261114: 3600})
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_two_sources_can_reach_threshold_together(self):
        """两个源各出一部分额度，凑够达标线也发 —— 只发一只。

        S1 的生产路径只有 weread（push 源的 Issue 接收端明确不在 S1），
        所以第二个源只能直接往账本里注入。
        """
        self._seed_ledger({DAY: {"pom": 50}})     # 番茄钟 50 分钟 = 2 额度
        rc, out = self._run({KEY_20261114: 900})   # 微信读书 900 秒 = 1 额度
        self.assertEqual(rc, 0)
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 1)
        self.assertEqual(state["collected"][0]["sources"], ["weread", "pom"])
        self.assertEqual(state["today"]["sources"], {"weread": 900, "pom": 50})
        self.assertEqual(state["today"]["credit"], 3)

    def test_chain_state_created_after_award(self):
        self._run({KEY_20261114: 1800})
        chains = self._state()["chains"]
        self.assertEqual(len(chains), 1)
        entry = list(chains.values())[0]
        self.assertEqual(entry["care"], 1)
        self.assertEqual(entry["pendingShiny"], 0)
        self.assertEqual(entry["nextShinyAt"], 30)
        self.assertNotIn("stage", entry)   # 形态改写在 S6，S1 的链状态没有 stage

    # ---- 保底闪光：消费与强制闪光 ----

    def _seed_ledger(self, ledger):
        """往状态里塞一份账本 —— 用来注入非 weread 的源。"""
        state = {
            "version": 2, "collected": [], "streak": 0, "lastAwardDate": None,
            "ledger": ledger, "chains": {},
        }
        self.dex_path.write_text(json.dumps(state), encoding="utf-8")

    def _seed_chain(self, key, care=30, pending=1):
        """往状态里塞一条已经养到保底线的链。"""
        state = {
            "version": 2, "collected": [], "streak": 0, "lastAwardDate": None,
            "ledger": {},
            "chains": {key: {"care": care, "pendingShiny": pending,
                             "nextShinyAt": care + 30}},
        }
        self.dex_path.write_text(json.dumps(state), encoding="utf-8")

    def test_guarantee_is_consumed_and_forces_shiny(self):
        """养到保底线 → 这条链下一次发奖必闪，且保底次数减 1。"""
        self._seed_chain("妙蛙种子", care=30, pending=1)
        fake = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
        with mock.patch.object(award.dc, "pick", return_value=dict(fake)):
            rc, out = self._run({KEY_20261114: 1800})
        self.assertIn("[保底]", out)
        state = self._state()
        self.assertTrue(state["collected"][0]["shiny"])
        self.assertEqual(state["chains"]["妙蛙种子"]["pendingShiny"], 0)

    def test_guarantee_not_consumed_by_other_chain(self):
        """保底是按链算的 —— 抽到别的链不吃这条链的保底。"""
        self._seed_chain("妙蛙种子", care=30, pending=1)
        fake = {"id": "0025", "nameZh": "皮卡丘", "shiny": False,
                "evolution": ["皮丘", "皮卡丘", "雷丘"]}
        with mock.patch.object(award.dc, "pick", return_value=dict(fake)):
            rc, out = self._run({KEY_20261114: 1800})
        self.assertNotIn("[保底]", out)
        state = self._state()
        self.assertEqual(state["chains"]["妙蛙种子"]["pendingShiny"], 1)
        self.assertEqual(state["chains"]["皮丘"]["care"], 1)

    def test_chain_care_counts_days_not_cards(self):
        """两天补发各抽到同一链 → care +2（一天一次），不是 +4。"""
        fake_a = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                  "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
        fake_b = {"id": "0002", "nameZh": "妙蛙草", "shiny": False,
                  "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
        with mock.patch.object(award.dc, "pick",
                               side_effect=[dict(fake_a), dict(fake_b)]):
            rc, out = self._run({KEY_20261113: 1800, KEY_20261114: 1800})
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual(state["chains"]["妙蛙种子"]["care"], 2)

    def test_guarantee_consumed_once_when_chain_picked_twice(self):
        """同一轮里同一条链抽到两次 —— 保底只消费一次（第二次不再必闪）。"""
        self._seed_chain("妙蛙种子", care=30, pending=1)
        fake_a = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                  "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
        fake_b = {"id": "0002", "nameZh": "妙蛙草", "shiny": False,
                  "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
        with mock.patch.object(award.dc, "pick",
                               side_effect=[dict(fake_a), dict(fake_b)]):
            rc, out = self._run({KEY_20261113: 1800, KEY_20261114: 1800})
        self.assertEqual(out.count("[保底]"), 1)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        shiniest = [c for c in state["collected"] if c["shiny"]]
        self.assertEqual(len(shiniest), 1)     # 保底只兑现一次
        self.assertEqual(state["chains"]["妙蛙种子"]["pendingShiny"], 0)

    # ---- --force：无视达标线发今天，积压日照常补发 ----

    def test_force_adds_today_on_top_of_backlog(self):
        """--force = 无视达标线把今天也发掉；积压日仍然照常补发。"""
        self._run({KEY_20261113: 1800})          # 先补掉 11-13
        rc, out = self._run({KEY_20261114: 100},  # 今天没达标
                            argv=["--force"])
        self.assertEqual(rc, 0)
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual(state["collected"][1]["date"], DAY)

    # ---- 迁移（--migrate-only）----

    def test_migrate_only_does_not_carry_stale_today_seconds(self):
        """迁移不能把几天前的读数写成今天的。"""
        state = {
            "version": 1, "collected": [], "streak": 0,
            "lastAwardDate": None,
            "today": {"date": "2026-11-10", "seconds": 1234, "threshold": 900},
        }
        self.dex_path.write_text(json.dumps(state), encoding="utf-8")
        rc, out = self._run({}, argv=["--migrate-only"])
        self.assertEqual(rc, 0)
        migrated = self._state()
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["today"]["seconds"], 0)
        self.assertEqual(migrated["today"]["sources"], {})
        self.assertEqual(migrated["today"]["credit"], 0)
        # 迁移不抽卡
        self.assertEqual(migrated["collected"], [])
        self.assertNotIn("★ 获得", out)

    def test_migrate_only_keeps_today_seconds_when_date_matches(self):
        """旧状态里 today.date 就是今天时，读数照常带过去。"""
        state = {
            "version": 1, "collected": [], "streak": 0,
            "lastAwardDate": None,
            "today": {"date": DAY, "seconds": 1234, "threshold": 900},
        }
        self.dex_path.write_text(json.dumps(state), encoding="utf-8")
        rc, out = self._run({}, argv=["--migrate-only"])
        self.assertEqual(rc, 0)
        migrated = self._state()
        self.assertEqual(migrated["today"]["seconds"], 1234)
        self.assertEqual(migrated["today"]["sources"], {"weread": 1234})
        self.assertEqual(migrated["today"]["credit"], 1)


if __name__ == "__main__":
    unittest.main()
