"""备考（背单词）源的服务端行为：注册、额度折算、Issue 上报解析。

**为什么是独立源**：账本对「同源同一天」取 max（重复投递要幂等）。
备考的单位是「个词」，跟阅读的秒数、番茄钟的分钟数都不可通约 ⇒
独立成一个源，各自**按天**向下取整算出自己的额度，再**相加**进同一份额度。

**`per` 为什么是 100 而不是占位时的 20**：`per` 的含义是「1 额度要多少原始单位」，
不是「1 只要多少」。20 ⇒ 1 只只要 40 个词 ⇒ 一天背 20 个新词加一堆复习就白送一只 ⇒
额度会通胀。100 ⇒ 1 只 = `perAward × per` = 2 × 100 = **200 个词**（与一个番茄钟的量级相当）。

服务端**零改动**：`scripts/collect_reports.py` 的 `parse_report` 对任意启用的 push 源一视同仁，
这一组用例就是证明这件事的。
"""

import json
import pathlib
import unittest

from scripts import collect_reports as cr
from scripts import sources as src

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "sources.json"
SITE_REGISTRY = ROOT / "site" / "sources.json"
TODAY = "2026-11-22"


def registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


class WordsSourceTest(unittest.TestCase):
    """`words` 源的注册信息与额度折算。"""

    def setUp(self):
        self.cfg = registry()

    def test_words_source_registered(self):
        self.assertIn("words", self.cfg["sources"])

    def test_words_is_enabled(self):
        """S4 之后备考参与结算（之前是 `enabled: false` 的占位）。"""
        self.assertIs(self.cfg["sources"]["words"]["enabled"], True)

    def test_words_unit_is_ke(self):
        self.assertEqual(self.cfg["sources"]["words"]["unit"], "个")

    def test_words_per_is_hundred(self):
        """100 个词 = 1 个额度。"""
        self.assertEqual(self.cfg["sources"]["words"]["per"], 100)

    def test_words_is_push_source(self):
        """mode 不是 push 的话，Issue 上报会被 parse_report 拒掉。"""
        self.assertEqual(self.cfg["sources"]["words"]["mode"], "push")

    def test_words_max_per_day_is_five_hundred(self):
        self.assertEqual(self.cfg["sources"]["words"]["maxPerDay"], 500)

    def test_registry_passes_validation(self):
        src.validate_config(self.cfg)

    def test_one_hundred_words_is_one_credit(self):
        got = src.credit_for_day(self.cfg, {"words": 100})
        self.assertEqual(got["words"], 1)

    def test_ninety_nine_words_is_zero_credit(self):
        """零头不计：99 个词 = 0 个额度。"""
        got = src.credit_for_day(self.cfg, {"words": 99})
        self.assertEqual(got["words"], 0)

    def test_two_hundred_fifty_words_is_two_credits(self):
        got = src.credit_for_day(self.cfg, {"words": 250})
        self.assertEqual(got["words"], 2)

    def test_credits_are_floored_per_day_not_over_the_total(self):
        """**按天 floor 再相加**：150 个 + 50 个 ⇒ 1 + 0 = 1（不是 floor(200/100) = 2）。"""
        ledger = {"2026-11-22": {"words": 150}, "2026-11-21": {"words": 50}}
        credits = src.credits_for_days(self.cfg, ledger)
        self.assertEqual(credits["2026-11-22"], 1)
        self.assertEqual(credits["2026-11-21"], 0)
        self.assertEqual(sum(credits.values()), 1)

    def test_two_days_below_the_threshold_add_up_to_nothing(self):
        """25 个 + 50 个 = 75 个 ⇒ 两个零头凑不成一个额度 ⇒ 0 个额度。"""
        ledger = {"2026-11-22": {"words": 25}, "2026-11-21": {"words": 50}}
        credits = src.credits_for_days(self.cfg, ledger)
        self.assertEqual(sum(credits.values()), 0)

    def test_credits_sum_with_weread(self):
        """微信读书 1 个额度 + 备考 1 个额度 = 2 ⇒ 达到 perAward。"""
        got = src.day_credit(self.cfg, {"weread": 900, "words": 100})
        self.assertEqual(got, 2)
        self.assertGreaterEqual(got, self.cfg["perAward"])

    def test_credits_sum_with_pom_and_koreader(self):
        """三个源各 1 个额度 ⇒ 3 个额度（当天最多掉 1 只，多的额度只是没用完）。"""
        got = src.day_credit(self.cfg, {"pom": 25, "koreader": 15, "words": 100})
        self.assertEqual(got, 3)

    def test_words_clip_to_max_per_day(self):
        """单日上限 500：报 9999 个也只按 500 个折算（5 个额度）。"""
        got = src.credit_for_day(self.cfg, {"words": 9999})
        self.assertEqual(got["words"], 500 // 100)

    def test_negative_words_is_zero_credit(self):
        """负数夹到 0（Python 的 `//` 会向下取整，负额度比不发放更糟）。"""
        got = src.credit_for_day(self.cfg, {"words": -50})
        self.assertEqual(got["words"], 0)

    def test_one_pokemon_costs_two_hundred_words(self):
        """1 只宝可梦 = perAward × per = 2 × 100 = 200 个词。"""
        self.assertEqual(self.cfg["perAward"], 2)
        self.assertEqual(self.cfg["perAward"] * self.cfg["sources"]["words"]["per"], 200)

    def test_the_old_placeholder_would_have_been_inflationary(self):
        """**为什么不能留着 per=20**：40 个词就掉 1 只 ⇒ 额度会通胀。

        这一条不是断言「现在的口径对」，而是把「占位值为什么必须改」钉住：
        将来谁想把 per 调回 20，这条用例会提醒他当初改的理由。
        """
        old = json.loads(json.dumps(self.cfg))
        old["sources"]["words"]["per"] = 20
        # 一天背 20 个新词 + 一堆复习，按老口径已经能白送一只
        self.assertGreaterEqual(src.credit_for_day(old, {"words": 40})["words"],
                                self.cfg["perAward"])

    def test_the_other_three_sources_are_untouched(self):
        """微信读书 / 番茄钟 / KOReader 的口径**一个字都不能动**。"""
        self.assertEqual(self.cfg["sources"]["weread"]["per"], 900)
        self.assertEqual(self.cfg["sources"]["weread"]["mode"], "pull")
        self.assertEqual(self.cfg["sources"]["weread"]["enabled"], True)
        self.assertEqual(self.cfg["sources"]["pom"]["per"], 25)
        self.assertEqual(self.cfg["sources"]["pom"]["mode"], "push")
        self.assertEqual(self.cfg["sources"]["pom"]["enabled"], True)
        self.assertEqual(self.cfg["sources"]["koreader"]["per"], 15)
        self.assertEqual(self.cfg["sources"]["koreader"]["mode"], "push")
        self.assertEqual(self.cfg["sources"]["koreader"]["enabled"], True)

    def test_per_award_is_still_two(self):
        """`perAward` 不许被 S4 改动。"""
        self.assertEqual(self.cfg["perAward"], 2)

    def test_words_does_not_change_the_weread_threshold(self):
        """today.threshold 是微信读书口径（perAward × weread.per），不受备考源影响。"""
        self.assertEqual(self.cfg["perAward"] * self.cfg["sources"]["weread"]["per"], 1800)

    def test_the_site_copy_of_the_registry_matches(self):
        """线上那份（site/sources.json）是 data/sources.json 的**部署产物** —— 两边要**整份**相等。

        不一致的话：服务端按一份算、页面按另一份显示 ⇒ 数字对不上。
        （`scripts/deploy.sh` 每次部署都会重新 `cp data/sources.json site/sources.json`，
        仓库里这份只是同步好的快照 ⇒ 漂了就说明有人手改过其中一份。）
        """
        self.assertTrue(SITE_REGISTRY.exists(), "site/sources.json 不存在")
        site_cfg = json.loads(SITE_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(site_cfg, self.cfg, "site/sources.json 与 data/sources.json 不一致")


class WordsReportTest(unittest.TestCase):
    """备考的报告能过 Issue 投递箱的校验（服务端零改动，但要证明）。"""

    def setUp(self):
        self.cfg = registry()

    def test_report_parser_accepts_words(self):
        body = json.dumps({"v": 1, "source": "words",
                           "days": {"2026-11-22": 150, "2026-11-21": 80}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertIsNone(err)
        self.assertEqual(days, {"2026-11-22": 150, "2026-11-21": 80})

    def test_report_parser_rejects_weread_still(self):
        """微信读书是 pull 源，仍然不能靠 issue 上报。"""
        body = json.dumps({"v": 1, "source": "weread", "days": {"2026-11-22": 900}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertEqual(days, {})
        self.assertIsNotNone(err)

    def test_words_over_cap_is_rejected(self):
        """超过单日上限 ⇒ 整份报告被拒（所以页面上要先夹住）。"""
        body = json.dumps({"v": 1, "source": "words", "days": {"2026-11-22": 501}})
        days, err = cr.parse_report(body, self.cfg, TODAY)
        self.assertEqual(days, {})
        self.assertIn("上限", err)

    def test_process_issues_merges_words_into_ledger(self):
        issue = {
            "number": 9, "title": "[report] words 2026-11-22",
            "author_association": "OWNER",
            "body": json.dumps({"v": 1, "source": "words", "days": {"2026-11-22": 150}}),
        }
        ledger, results = cr.process_issues([issue], self.cfg, TODAY, {})
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(ledger["2026-11-22"]["words"], 150)
        # 额度按该源口径折算：150 // 100 = 1
        self.assertEqual(results[0]["credits"], {"2026-11-22": 1})

    def test_words_does_not_disturb_other_columns(self):
        """备考的量进自己的列，不动别的源（同源同天才取 max）。"""
        issue = {
            "number": 10, "title": "[report] words 2026-11-22",
            "author_association": "OWNER",
            "body": json.dumps({"v": 1, "source": "words", "days": {"2026-11-22": 150}}),
        }
        ledger, _ = cr.process_issues([issue], self.cfg, TODAY,
                                      {"2026-11-22": {"weread": 900}})
        self.assertEqual(ledger["2026-11-22"]["weread"], 900)
        self.assertEqual(ledger["2026-11-22"]["words"], 150)

    def test_repeated_reports_take_the_max_not_the_sum(self):
        """重复投递取 max（不叠加）—— 报两次 150 与 200，结果是 200。"""
        issues = [
            {"number": 11, "title": "[report] words 2026-11-22",
             "author_association": "OWNER",
             "body": json.dumps({"v": 1, "source": "words", "days": {"2026-11-22": 150}})},
            {"number": 12, "title": "[report] words 2026-11-22",
             "author_association": "OWNER",
             "body": json.dumps({"v": 1, "source": "words", "days": {"2026-11-22": 200}})},
        ]
        ledger, _ = cr.process_issues(issues, self.cfg, TODAY, {})
        self.assertEqual(ledger["2026-11-22"]["words"], 200)


if __name__ == "__main__":
    unittest.main()
