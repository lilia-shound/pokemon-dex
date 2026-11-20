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


def issue(number=1, title="[report] pom 2026-11-20", body=None,
          assoc="OWNER", **extra):
    it = {"number": number, "title": title, "body": body if body is not None else report(),
          "author_association": assoc}
    it.update(extra)
    return it


class ProcessIssuesTest(unittest.TestCase):
    """编排层：纯函数，不联网。"""

    def test_accepted_report_merges_into_ledger(self):
        ledger, results = cr.process_issues([issue()], cfg(), TODAY, {})
        self.assertEqual(ledger, {"2026-11-19": {"pom": 50},
                                  "2026-11-20": {"pom": 25}})
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(results[0]["source"], "pom")

    def test_accepted_report_reports_credits(self):
        _, results = cr.process_issues([issue()], cfg(), TODAY, {})
        # 50 分钟 → 2 额度，25 分钟 → 1 额度
        self.assertEqual(results[0]["credits"], {"2026-11-19": 2, "2026-11-20": 1})

    def test_non_report_title_is_skipped(self):
        ledger, results = cr.process_issues([issue(title="随便聊聊")], cfg(), TODAY, {})
        self.assertEqual(ledger, {})
        self.assertEqual(results[0]["action"], "skipped")

    def test_untrusted_issue_is_not_merged(self):
        ledger, results = cr.process_issues([issue(assoc="NONE")], cfg(), TODAY, {})
        self.assertEqual(ledger, {})
        self.assertEqual(results[0]["action"], "untrusted")

    def test_missing_association_is_untrusted(self):
        it = issue()
        del it["author_association"]
        ledger, results = cr.process_issues([it], cfg(), TODAY, {})
        self.assertEqual(ledger, {})
        self.assertEqual(results[0]["action"], "untrusted")

    def test_outsider_association_is_untrusted(self):
        ledger, results = cr.process_issues([issue(assoc="CONTRIBUTOR")], cfg(), TODAY, {})
        self.assertEqual(ledger, {})
        self.assertEqual(results[0]["action"], "untrusted")

    def test_rejected_report_is_not_merged(self):
        body = json.dumps({"v": 1, "source": "pom", "days": {"2026-11-19": 99999}})
        ledger, results = cr.process_issues([issue(body=body)], cfg(), TODAY, {})
        self.assertEqual(ledger, {})
        self.assertEqual(results[0]["action"], "rejected")
        self.assertIsNotNone(results[0]["reason"])

    def test_duplicate_report_takes_max(self):
        """重复投递取 max，不叠加 —— 这就是幂等。"""
        a = issue(number=1)
        b = issue(number=2, body=json.dumps(
            {"v": 1, "source": "pom", "days": {"2026-11-19": 20}}))
        ledger, results = cr.process_issues([a, b], cfg(), TODAY, {})
        self.assertEqual(ledger["2026-11-19"]["pom"], 50)  # max(50, 20)，不是 70
        self.assertEqual([r["action"] for r in results], ["accepted", "accepted"])

    def test_report_does_not_overwrite_other_sources(self):
        ledger = {"2026-11-19": {"weread": 900}}
        new_ledger, _ = cr.process_issues([issue()], cfg(), TODAY, ledger)
        self.assertEqual(new_ledger["2026-11-19"]["weread"], 900)
        self.assertEqual(new_ledger["2026-11-19"]["pom"], 50)

    def test_original_ledger_not_mutated(self):
        ledger = {}
        cr.process_issues([issue()], cfg(), TODAY, ledger)
        self.assertEqual(ledger, {})

    def test_no_issues_leaves_ledger_untouched(self):
        ledger = {"2026-11-19": {"pom": 50}}
        new_ledger, results = cr.process_issues([], cfg(), TODAY, ledger)
        self.assertEqual(new_ledger, ledger)
        self.assertEqual(results, [])

    def test_bad_issue_does_not_stop_good_one(self):
        """一条坏的不能连累其余 —— 其余 issue 照常处理。"""
        bad = issue(number=1, body="{not json")
        good = issue(number=2)
        ledger, results = cr.process_issues([bad, good], cfg(), TODAY, {})
        self.assertEqual(ledger, {"2026-11-19": {"pom": 50},
                                  "2026-11-20": {"pom": 25}})
        self.assertEqual([r["action"] for r in results], ["rejected", "accepted"])


class FilterReportIssuesTest(unittest.TestCase):
    """issues 接口会把 PR 一起返回，要挑出来。"""

    def test_keeps_report_issue(self):
        got = cr.filter_report_issues([issue()])
        self.assertEqual(len(got), 1)

    def test_drops_pull_request(self):
        it = issue()
        it["pull_request"] = {"url": "..."}
        self.assertEqual(cr.filter_report_issues([it]), [])

    def test_drops_other_titles(self):
        self.assertEqual(cr.filter_report_issues([issue(title="bug")]), [])

    def test_tolerates_garbage(self):
        self.assertEqual(cr.filter_report_issues([None, "x", 3]), [])

    def test_empty_list(self):
        self.assertEqual(cr.filter_report_issues([]), [])


class NoteTest(unittest.TestCase):
    """写给 issue 的评论。"""

    def test_note_mentions_days_and_credits(self):
        _, results = cr.process_issues([issue()], cfg(), TODAY, {})
        note = cr._note(results[0])
        self.assertIn("2026-11-19", note)
        self.assertIn("3", note)          # 2 + 1 = 3 个额度
        self.assertIn("分钟", note)        # 番茄钟的单位


if __name__ == "__main__":
    unittest.main()
