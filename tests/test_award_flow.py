#!/usr/bin/env python3
"""发放器的「同一天只发一张」回归测试。

背景：new_state 按日期去重，一天只该发一张。但 award.py 原先在「今天已经发过
奖」时仍然会抽一张、打印「★ 获得」，最后被 new_state 悄悄丢掉 —— 假消息。
定时任务每 3 小时跑一次，当天会连报好几次（甚至报出好几只不存在的宝可梦）。
"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import award

# 2026-11-13 / 11-14 00:00 Asia/Shanghai 的桶 key
KEY_20261113 = 1794499200
KEY_20261114 = 1794585600
DAY = "2026-11-14"


class SameDayAwardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        award.DEX_PATH = self.dex_path

    def _run(self, buckets, today_secs, argv=()):
        out = io.StringIO()
        with mock.patch.object(award, "fetch_buckets", return_value=buckets), \
             mock.patch.object(award.wc, "today_seconds",
                               return_value=(KEY_20261114, today_secs)):
            with contextlib.redirect_stdout(out):
                rc = award.main(["--date", DAY, "--no-push", *argv])
        return rc, out.getvalue()

    def _state(self):
        return json.loads(self.dex_path.read_text(encoding="utf-8"))

    def test_first_run_awards_once(self):
        rc, out = self._run({KEY_20261114: 1782}, 1782)
        self.assertEqual(rc, 0)
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 1)
        self.assertEqual(state["collected"][0]["date"], DAY)
        self.assertEqual(state["streak"], 1)

    def test_second_run_same_day_does_not_award_again(self):
        self._run({KEY_20261114: 1782}, 1782)
        rc, out = self._run({KEY_20261114: 2400}, 2400)
        self.assertEqual(rc, 0)
        self.assertIn("今天已经发过奖了", out)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_second_run_still_refreshes_today_seconds(self):
        """不重复发奖，但「今天已读多久」要继续更新（图鉴页要显示）。"""
        self._run({KEY_20261114: 1782}, 1782)
        self._run({KEY_20261114: 2400}, 2400)
        state = self._state()
        self.assertEqual(state["today"]["seconds"], 2400)
        self.assertEqual(state["today"]["date"], DAY)

    def test_force_does_not_duplicate_same_day(self):
        """--force 只无视达标线，不该违反「一天一张」。"""
        self._run({KEY_20261114: 1782}, 1782)
        rc, out = self._run({KEY_20261114: 2400}, 2400, argv=["--force"])
        self.assertEqual(rc, 0)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_catch_up_awards_past_day_not_today(self):
        """延迟上报：昨天读满的数据今天才到 → 补发昨天，今天已发过的不再抽。"""
        self._run({KEY_20261114: 1782}, 1782)
        # 昨天(11-13)的桶今天才出现且已达标，但 11-14 已经发过奖了
        rc, out = self._run({KEY_20261113: 1000, KEY_20261114: 2400}, 2400)
        self.assertEqual(rc, 0)
        self.assertIn("补发 2026-11-13", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual(state["collected"][1]["date"], "2026-11-13")


if __name__ == "__main__":
    unittest.main()
