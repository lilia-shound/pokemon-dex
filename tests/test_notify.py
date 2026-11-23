"""邮件通知（`scripts/notify.py`）的测试。

重点盯三件事：

1. **补发的宝可梦也要通知** —— `award.py` 会一次结清所有待发奖日，
   所以「今天」这轮可能收服的是前天甚至大前天那只 ⇒ 判定不能只看 date == 今天。
2. **幂等** —— 同一条目（同一天同一只）永远只通知一次。
3. **连读提醒的四个条件** —— streak≥2、今天没达标、≥21:00、今天没发过。

跑法：

    python3 -B run_tests.py                          # 整套
    python3 -B -m tests.test_notify                 # 只跑这个文件
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile
import unittest

from scripts import notify


class AwardDetectTest(unittest.TestCase):
    """判定「哪些条目还没通知过」。"""

    def test_key_is_date_and_id(self):
        self.assertEqual(notify.award_key({"date": "2026-11-22", "id": "0163"}),
                         "2026-11-22#0163")

    def test_a_fresh_entry_is_pending(self):
        got = notify.pending_awards([{"date": "2026-11-22", "id": "0163"}], [])
        self.assertEqual(len(got), 1)

    def test_an_already_notified_entry_is_not_pending(self):
        entries = [{"date": "2026-11-22", "id": "0163"}]
        got = notify.pending_awards(entries, ["2026-11-22#0163"])
        self.assertEqual(got, [])

    def test_a_backfilled_older_day_is_still_pending(self):
        """★ 补发的也要通知 —— 不能按「是不是今天」筛。"""
        entries = [{"date": "2026-11-20", "id": "0471"}]
        got = notify.pending_awards(entries, [])
        self.assertEqual([e["id"] for e in got], ["0471"])

    def test_the_same_id_on_two_days_counts_twice(self):
        entries = [{"date": "2026-11-20", "id": "0471"},
                   {"date": "2026-11-22", "id": "0471"}]
        got = notify.pending_awards(entries, ["2026-11-20#0471"])
        self.assertEqual([e["date"] for e in got], ["2026-11-22"])

    def test_empty_input_gives_empty_output(self):
        self.assertEqual(notify.pending_awards([], []), [])


class StreakWarnTest(unittest.TestCase):
    """判定「连读快断了」。"""

    def _now(self, hour):
        return datetime.datetime(2026, 11, 23, hour, 0, 0)

    def test_warns_when_streak_is_two_and_not_reached(self):
        self.assertTrue(notify.should_warn_streak(2, 1, 2, self._now(21), False))

    def test_does_not_warn_when_streak_is_one(self):
        """连读只有 1 天时断了无所谓 —— 不打扰。"""
        self.assertFalse(notify.should_warn_streak(1, 0, 2, self._now(21), False))

    def test_does_not_warn_before_nine_pm(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(20), False))

    def test_does_not_warn_after_reaching_the_goal(self):
        self.assertFalse(notify.should_warn_streak(4, 2, 2, self._now(21), False))

    def test_does_not_warn_twice_in_one_day(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(22), True))

    def test_warns_at_exactly_nine_pm(self):
        self.assertTrue(notify.should_warn_streak(4, 0, 2, self._now(21), False))


class StateFileTest(unittest.TestCase):
    """幂等标记存在哪儿。"""

    def test_missing_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            st = notify.load_state(os.path.join(d, "nope.json"))
            self.assertEqual(st, {"awarded": [], "streak": {}})

    def test_broken_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            pathlib.Path(p).write_text("{ not json", encoding="utf-8")
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], [])

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": ["a#b"], "streak": {"2026-11-23": True}}, p)
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], ["a#b"])
            self.assertTrue(st["streak"]["2026-11-23"])

    def test_state_file_is_written_as_utf8_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": [], "streak": {"2026-11-23": True}}, p)
            raw = pathlib.Path(p).read_text(encoding="utf-8")
            self.assertIn("2026-11-23", raw)
            self.assertEqual(json.loads(raw)["streak"]["2026-11-23"], True)


if __name__ == "__main__":
    unittest.main()
