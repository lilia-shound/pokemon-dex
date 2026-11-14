import datetime
import unittest
import zoneinfo
from unittest import mock

from scripts import weread_client as wc

SH = zoneinfo.ZoneInfo("Asia/Shanghai")


def _ts(y, m, d, hh=0, mm=0):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=SH).timestamp())


class ReadTimesTest(unittest.TestCase):
    def test_returns_int_keyed_seconds(self):
        with mock.patch.object(wc, "call", lambda api, **p: {"readTimes": {"1794585600": 1200}}):
            rt = wc.read_times("monthly")
        self.assertEqual(rt, {1794585600: 1200})
        self.assertTrue(all(isinstance(k, int) and isinstance(v, int) for k, v in rt.items()))

    def test_tolerates_missing_key(self):
        with mock.patch.object(wc, "call", lambda api, **p: {}):
            self.assertEqual(wc.read_times("monthly"), {})

    def test_skips_unparsable_entries(self):
        raw = {"1794585600": "1200", "bad": "x", "1794585601": 30}
        with mock.patch.object(wc, "call", lambda api, **p: {"readTimes": raw}):
            self.assertEqual(wc.read_times("monthly"), {1794585600: 1200, 1794585601: 30})

    def test_call_without_key_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(wc.WeReadError):
                wc.call("/readdata/detail", mode="monthly")


class TodaySecondsTest(unittest.TestCase):
    def test_picks_latest_bucket_of_today(self):
        today = _ts(2026, 11, 14)
        yesterday = _ts(2026, 11, 13)
        key, secs = wc.today_seconds({yesterday: 600, today: 900})
        self.assertEqual(key, today)
        self.assertEqual(secs, 900)

    def test_zero_when_latest_bucket_is_yesterday(self):
        yesterday = _ts(2026, 11, 13)
        key, secs = wc.today_seconds({yesterday: 600})
        self.assertIsNone(key)
        self.assertEqual(secs, 0)

    def test_ignores_future_buckets(self):
        today = _ts(2026, 11, 14)
        tomorrow = _ts(2026, 11, 15)
        key, secs = wc.today_seconds({today: 300, tomorrow: 999})
        self.assertEqual(key, today)
        self.assertEqual(secs, 300)

    def test_empty_read_times(self):
        self.assertEqual(wc.today_seconds({}), (None, 0))

    def test_bucket_key_is_beijing_midnight(self):
        """桶 key 落在北京时间的自然日 00:00。"""
        today = _ts(2026, 11, 14)
        key, _ = wc.today_seconds({today: 100})
        self.assertEqual(
            datetime.datetime.fromtimestamp(key, SH),
            datetime.datetime(2026, 11, 14, 0, 0, tzinfo=SH),
        )

    def test_accepts_explicit_now(self):
        now = datetime.datetime(2026, 11, 14, 10, 0, tzinfo=SH)
        today = _ts(2026, 11, 14)
        yesterday = _ts(2026, 11, 13)
        key, secs = wc.today_seconds({yesterday: 10, today: 20}, now=now)
        self.assertEqual(key, today)
        self.assertEqual(secs, 20)


if __name__ == "__main__":
    unittest.main()


class BucketSecondsTest(unittest.TestCase):
    def test_returns_seconds_for_given_day(self):
        rt = {_ts(2026, 11, 13): 600, _ts(2026, 11, 14): 900}
        day = datetime.date(2026, 11, 14)
        self.assertEqual(wc.bucket_seconds(rt, day), 900)

    def test_missing_day_is_zero(self):
        rt = {_ts(2026, 11, 14): 900}
        day = datetime.date(2026, 11, 13)
        self.assertEqual(wc.bucket_seconds(rt, day), 0)

    def test_empty_read_times(self):
        self.assertEqual(wc.bucket_seconds({}, datetime.date(2026, 11, 14)), 0)
