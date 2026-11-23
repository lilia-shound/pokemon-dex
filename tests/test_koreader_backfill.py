"""KOReader 落地页的补录（手动加一天）。

这个页面正常是「扫码 → 短链里带天数 → 页面解出来」。但短链可能截断
（那一屏叫「没读出有效的时长」），或者她想补前天忘了报的那天
⇒ 页面要能**手动加一天**。

★ 手动加的天**也要过单日上限** —— `recompute()` 里的 `clampDays(state.days, cap)`
就是干这个的（不然能被刷额度）。

跑法：

    python3 -B run_tests.py
    python3 -B -m tests.test_koreader_backfill
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "site" / "koreader" / "app.js"
INDEX_HTML = ROOT / "site" / "koreader" / "index.html"


class KoreaderBackfillTest(unittest.TestCase):
    def test_the_page_has_a_manual_day_form(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertTrue("manualDay" in html, "页面里要有 manualDay")

    def test_the_form_is_outside_the_main_box(self):
        """★ 短链坏了的时候 mainBox 是藏着的 —— 手动补记的入口不能被一起藏掉。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        main_at = html.index('id="mainBox"')
        # mainBox 的结束：紧接着的那个 </div>
        main_end = html.index("</div>", html.index("</section>", main_at))
        manual_at = html.index("manualDay")
        self.assertTrue(manual_at > main_end,
                        "手动补记的表单要在 mainBox 外面（短链坏了也得能填）")

    def test_the_script_merges_manual_days_into_state(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"state\.days\[", js) is not None,
                        "要把手动加的天写进 state.days")

    def test_manual_days_are_clamped_too(self):
        """手动加的天也要过单日上限 —— 不然能被刷额度。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"clampDays\(state\.days", js) is not None,
                        "recompute() 要拿 state.days 去夹上限")

    def test_the_report_still_only_sends_one_request(self):
        """子页纪律：只许发一个网络请求 —— 别为补录引新脚本。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
        self.assertLessEqual(len(scripts), 1, "子页只许引一个脚本")

    def test_the_script_binds_the_manual_form(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("bindManualDay" in js, "要绑定手动补记的按钮")


if __name__ == "__main__":
    unittest.main()
