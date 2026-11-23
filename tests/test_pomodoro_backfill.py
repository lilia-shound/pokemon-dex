"""番茄钟页的补录（改过去某一天的分钟数）。

真值只有一处：`S().days[day].minutes`（`S()` 就是 `Store.st.data`）。
`syncDays()` 已经会把所有「有分钟的天」一起投上去（最近 30 天）⇒
补录**不需要动投递逻辑**，只要能把某一天的分钟数写进去。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_pomodoro_backfill
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
POM_DIR = ROOT / "site" / "pomodoro"
APP_JS = POM_DIR / "app.js"
INDEX_HTML = POM_DIR / "index.html"


class PomodoroBackfillTest(unittest.TestCase):
    """静态断言：页面有日期格子、有写回 Store 的入口。"""

    def test_the_page_has_a_day_picker(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertTrue("dayStrip" in html, "页面里要有 dayStrip")

    def test_the_day_strip_is_empty_in_the_markup(self):
        """格子由 JS 渲染 —— 静态 HTML 里不该有写死的天数。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(html, r'id="dayStrip"[^>]*>\s*<button[^>]*class="day"')

    def test_the_script_writes_back_to_the_store(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("dayStrip" in js, "脚本里要有 dayStrip")
        self.assertTrue(re.search(r"S\(\)\.days\[", js) is not None, "要写回 S().days[day]")

    def test_the_script_limits_the_picker_to_seven_days(self):
        js = APP_JS.read_text(encoding="utf-8")
        m = re.search(r"(?:const|var|let)\s+PICK_DAYS\s*=\s*(\d+)", js)
        self.assertIsNotNone(m, "页面里要有一个写死的 PICK_DAYS 常量")
        self.assertEqual(int(m.group(1)), 7)

    def test_the_script_persists_through_the_real_store_api(self):
        """★ `Store` 只有 `write()`，没有 `save()` —— 写错了等于没保存。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("Store.write()" in js, "要用真实存在的 Store.write()")
        self.assertNotIn("Store.save()", js)

    def test_the_picker_does_not_redeclare_the_sync_window(self):
        """`MAX_SYNC_DAYS` 原本就有 —— 重复声明会让常量含义不清。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?:const|var|let)\s+MAX_SYNC_DAYS", js)), 1)

    def test_the_day_picker_is_rendered_on_load(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertTrue("renderDayStrip()" in js, "要调用 renderDayStrip()")


if __name__ == "__main__":
    unittest.main()
