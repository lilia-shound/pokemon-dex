import json
import unittest

from scripts import collect_reports as cr


def cfg(**over):
    """一份典型的注册表：weread 是 pull 源，pom 是 push 源，words 停用。"""
    base = {
        "version": 1,
        "perAward": 1,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "unit": "秒", "per": 900,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
            "words": {"name": "背单词", "unit": "个", "per": 20,
                      "mode": "push", "enabled": False, "maxPerDay": 500},
        },
    }
    base.update(over)
    return base


TODAY = "2026-11-20"


def report(**over):
    body = {"v": 1, "source": "pom", "days": {"2026-11-19": 50, "2026-11-20": 25}}
    body.update(over)
    # days 传集合时（造「很多天」的用例）转成字典
    if isinstance(body["days"], set):
        body["days"] = {day: 50 for day in body["days"]}
    return json.dumps(body, ensure_ascii=False)


class ParseReportTest(unittest.TestCase):
    """报告解析：任何一条校验不过就丢弃**整个**报告。"""

    def test_valid_report_returns_days(self):
        days, err = cr.parse_report(report(), cfg(), TODAY)
        self.assertIsNone(err)
        self.assertEqual(days, {"2026-11-19": 50, "2026-11-20": 25})

    def test_rejects_empty_body(self):
        days, err = cr.parse_report("", cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("空", err)

    def test_rejects_bad_json(self):
        days, err = cr.parse_report("{not json", cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("JSON", err)

    def test_rejects_json_that_is_not_object(self):
        days, err = cr.parse_report("[1,2,3]", cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("对象", err)

    def test_rejects_wrong_version(self):
        days, err = cr.parse_report(report(v=2), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("版本", err)

    def test_rejects_missing_version(self):
        days, err = cr.parse_report(report(v=None), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("版本", err)

    def test_rejects_unknown_source(self):
        days, err = cr.parse_report(report(source="nope"), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("没有这个源", err)

    def test_rejects_pull_source(self):
        """微信读书是 pull 源（服务端自己去拉），不能靠 issue 上报。"""
        days, err = cr.parse_report(
            report(source="weread", days={"2026-11-20": 900}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("push", err)

    def test_rejects_disabled_source(self):
        days, err = cr.parse_report(report(source="words"), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("停用", err)

    def test_rejects_days_not_object(self):
        days, err = cr.parse_report(report(days=[1, 2]), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("days", err)

    def test_rejects_empty_days(self):
        days, err = cr.parse_report(report(days={}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("没有天数", err)

    def test_rejects_too_many_days(self):
        many = {f"2026-10-{i:02d}" for i in range(1, 32)}
        days, err = cr.parse_report(report(days=many), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("最多", err)

    def test_accepts_exactly_max_days(self):
        days = {f"2026-10-{i:02d}" for i in range(1, 31)}
        got, err = cr.parse_report(report(days=days), cfg(), TODAY)
        self.assertIsNone(err)
        self.assertEqual(len(got), 30)

    def test_rejects_bad_date_format(self):
        days, err = cr.parse_report(report(days={"2026/11/19": 50}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("日期格式", err)

    def test_rejects_compact_date_format(self):
        """20261118 这种基本格式不算 —— 统一用 YYYY-MM-DD。"""
        days, err = cr.parse_report(report(days={"20261119": 50}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("日期格式", err)

    def test_rejects_impossible_date(self):
        days, err = cr.parse_report(report(days={"2026-02-30": 50}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("日期格式", err)

    def test_rejects_future_day(self):
        days, err = cr.parse_report(report(days={"2026-11-21": 50}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("未来", err)

    def test_rejects_negative_amount(self):
        days, err = cr.parse_report(report(days={"2026-11-19": -1}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("负数", err)

    def test_rejects_non_integer_amount(self):
        days, err = cr.parse_report(report(days={"2026-11-19": 25.5}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("整数", err)

    def test_rejects_bool_amount(self):
        """True 是 int 的子类，但语义不是「分钟数」。"""
        days, err = cr.parse_report(report(days={"2026-11-19": True}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("整数", err)

    def test_rejects_amount_over_source_max_per_day(self):
        days, err = cr.parse_report(report(days={"2026-11-19": 1441}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIn("上限", err)

    def test_accepts_amount_at_cap(self):
        got, err = cr.parse_report(report(days={"2026-11-19": 1440}), cfg(), TODAY)
        self.assertIsNone(err)
        self.assertEqual(got, {"2026-11-19": 1440})

    def test_one_bad_day_rejects_whole_report(self):
        """不能部分采纳 —— 一条坏数据不许只污染一半。"""
        days, err = cr.parse_report(
            report(days={"2026-11-18": 50, "2026-11-19": 99999}), cfg(), TODAY)
        self.assertEqual(days, {})
        self.assertIsNotNone(err)

    def test_source_without_max_per_day_has_no_cap(self):
        c = cfg()
        del c["sources"]["pom"]["maxPerDay"]
        got, err = cr.parse_report(report(days={"2026-11-19": 99999}), c, TODAY)
        self.assertIsNone(err)
        self.assertEqual(got, {"2026-11-19": 99999})

    def test_today_is_allowed(self):
        got, err = cr.parse_report(report(days={TODAY: 25}), cfg(), TODAY)
        self.assertIsNone(err)
        self.assertEqual(got, {TODAY: 25})


if __name__ == "__main__":
    unittest.main()
