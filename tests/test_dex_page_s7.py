"""图鉴页的历史块（S7c）。

两块：
- **热力图** —— 最近 30 天，每天一格，颜色 = 当天微信读书的秒数（分档按额度刻度）
- **堆叠柱** —— 每天一根，四个源堆叠，**Y 轴 = 额度**（跟今日额度面板同口径）

★ 最要紧的一条：**额度折算必须复用现有的 `creditForSource()`**
（`site/app.js:39`）—— 它与服务端 `scripts/sources.py` 的 `credit_for_day` 逐字同构，
评审用 3731 组跨语言对拍守着。另写一份并行实现，那条对拍会立刻炸。

跑法：

    python3 -B run_tests.py
    python3 -B -m tests.test_dex_page_s7
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "site" / "app.js"
INDEX_HTML = ROOT / "site" / "index.html"
STYLE_CSS = ROOT / "site" / "style.css"


class HistoryMarkupTest(unittest.TestCase):
    def test_the_page_has_a_history_section(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertTrue("historyCard" in html, "页面里要有 historyCard")

    def test_the_page_has_placeholders_for_the_two_charts(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertTrue("heatMap" in html, "要有热力图的容器")
        self.assertTrue("creditBars" in html, "要有堆叠柱的容器")

    def test_the_script_fetches_the_history_file(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("history.json" in js, "要拉站根的 history.json")

    def test_the_script_exposes_the_history_helpers(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("historyDays" in js, "要有 historyDays")
        self.assertTrue("creditForDay" in js, "要有 creditForDay")

    def test_the_window_is_thirty_days(self):
        js = APP_JS.read_text(encoding="utf-8")
        m = re.search(r"(?:const|let|var)\s+HISTORY_DAYS\s*=\s*(\d+)", js)
        self.assertIsNotNone(m, "要有写死的 HISTORY_DAYS 常量")
        self.assertEqual(int(m.group(1)), 30)

    def test_the_credit_reuses_the_existing_isomorphic_helper(self):
        """★★ 折算额度**必须**走 `creditForSource()` —— 不能另写一份。"""
        js = APP_JS.read_text(encoding="utf-8")
        m = re.search(r"function\s+creditForDay\s*\([^)]*\)\s*\{(.*?)\n\}", js, re.S)
        self.assertIsNotNone(m, "要有 creditForDay 函数")
        body = m.group(1)
        self.assertTrue("creditForSource(" in body,
                        "creditForDay 必须复用 creditForSource（与服务端逐字同构）")
        self.assertTrue("registrySources(" in body,
                        "creditForDay 必须走 registrySources（只算启用的源）")

    def test_the_fetch_reuses_the_existing_helper(self):
        """拉 JSON 要用现成的 `fetchJSON`（带超时 + 重试）—— 别写裸 fetch。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r'fetchJSON\(\s*["\']history\.json["\']', js) is not None,
                        "要用 fetchJSON 拉 history.json")

    def test_the_history_block_is_rendered(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"renderHistory\s*\(", js) is not None,
                        "要有 renderHistory")


class HistoryStyleTest(unittest.TestCase):
    def test_the_style_has_heat_map_and_bar_rules(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        self.assertTrue(".heat" in css, "要有热力图的样式")
        self.assertTrue(".cbar" in css, "要有堆叠柱的样式")


if __name__ == "__main__":
    unittest.main()
