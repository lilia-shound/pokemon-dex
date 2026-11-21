"""KOReader 源的服务端行为：注册、额度折算、Issue 上报解析。

**为什么 KOReader 是独立源，不是并进 weread**：账本对「同源同一天」取 max
（幂等的关键）。微信读书 1800 秒、KOReader 600 秒，取 max = 1800 ——
KOReader 那份直接被吃掉。独立源的额度照样相加进同一份额度 ✓。
"""

import json
import pathlib
import unittest

from scripts import collect_reports as cr
from scripts import sources as src

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "sources.json"
TODAY = "2026-11-21"


def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


class KoreaderSourceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = registry()

    def test_koreader_source_registered(self):
        self.assertIn("koreader", self.cfg["sources"])

    def test_koreader_is_push_source(self):
        """mode 不是 push 的话，Issue 上报会被 parse_report 拒掉。"""
        self.assertEqual(self.cfg["sources"]["koreader"]["mode"], "push")

    def test_koreader_enabled(self):
        self.assertTrue(self.cfg["sources"]["koreader"]["enabled"])

    def test_koreader_unit_is_minute(self):
        self.assertEqual(self.cfg["sources"]["koreader"]["unit"], "分钟")

    def test_koreader_per_is_fifteen_minutes(self):
        """15 分钟 = 1 个额度（与微信读书的 900 秒同价）。"""
        self.assertEqual(self.cfg["sources"]["koreader"]["per"], 15)

    def test_registry_passes_validation(self):
        src.validate_config(self.cfg)

    def test_koreader_minutes_to_credit(self):
        got = src.credit_for_day(self.cfg, {"koreader": 25})
        self.assertEqual(got["koreader"], 1)

    def test_koreader_zero_minutes_is_zero_credit(self):
        got = src.credit_for_day(self.cfg, {"koreader": 14})
        self.assertEqual(got["koreader"], 0)

    def test_koreader_credit_sums_with_weread(self):
        """微信读书 1 个额度 + KOReader 1 个额度 = 2 ⇒ 达到 perAward。"""
        got = src.day_credit(self.cfg, {"weread": 900, "koreader": 25})
        self.assertEqual(got, 2)
        self.assertGreaterEqual(got, self.cfg["perAward"])

    def test_koreader_clip_to_max_per_day(self):
        got = src.credit_for_day(self.cfg, {"koreader": 9999})
        self.assertEqual(got["koreader"], 1440 // 15)

    def test_koreader_does_not_change_weread_threshold(self):
        """today.threshold 是微信读书口径（perAward × weread.per），不受 KOReader 影响。"""
        self.assertEqual(self.cfg["perAward"] * self.cfg["sources"]["weread"]["per"], 1800)


class KoreaderReportTest(unittest.TestCase):
    """KOReader 的报告能过 Issue 投递箱的校验。"""

    def setUp(self):
        self.cfg = registry()

    def test_report_parser_accepts_koreader(self):
        body = json.dumps({"v": 1, "source": "koreader",
                           "days": {"2026-11-21": 25, "2026-11-20": 50}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertIsNone(err)
        self.assertEqual(days, {"2026-11-21": 25, "2026-11-20": 50})

    def test_report_parser_rejects_weread_still(self):
        """微信读书是 pull 源，仍然不能靠 issue 上报。"""
        body = json.dumps({"v": 1, "source": "weread", "days": {"2026-11-21": 900}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertEqual(days, {})
        self.assertIsNotNone(err)

    def test_koreader_over_cap_is_rejected(self):
        body = json.dumps({"v": 1, "source": "koreader", "days": {"2026-11-21": 1441}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertEqual(days, {})
        self.assertIn("上限", err)

    def test_process_issues_merges_koreader_into_ledger(self):
        issue = {
            "number": 7, "title": "[report] koreader 2026-11-21",
            "author_association": "OWNER",
            "body": json.dumps({"v": 1, "source": "koreader", "days": {"2026-11-21": 25}}),
        }
        ledger, results = cr.process_issues([issue], self.cfg, TODAY, {})
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(ledger["2026-11-21"]["koreader"], 25)
        # 额度按该源口径折算：25 分钟 // 15 = 1
        self.assertEqual(results[0]["credits"], {"2026-11-21": 1})

    def test_koreader_does_not_disturb_weread_column(self):
        issue = {
            "number": 8, "title": "[report] koreader 2026-11-21",
            "author_association": "OWNER",
            "body": json.dumps({"v": 1, "source": "koreader", "days": {"2026-11-21": 25}}),
        }
        ledger, _ = cr.process_issues([issue], self.cfg, TODAY,
                                       {"2026-11-21": {"weread": 900}})
        self.assertEqual(ledger["2026-11-21"]["weread"], 900)
        self.assertEqual(ledger["2026-11-21"]["koreader"], 25)


if __name__ == "__main__":
    unittest.main()
