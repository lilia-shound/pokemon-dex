"""客户端的「今天」必须按 **UTC+8** 算 —— 与服务端同一个口径。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_client_timezone

★ 为什么要有这条：
  服务端（scripts/dex_core.py、scripts/award.py、worker/lib/validate.cjs）
  一律按 Asia/Shanghai 分天；客户端以前用的是**浏览器本地时区** ⇒
  她的设备不在中国时区时（出差、换设备、手动改过时区），页面算出来的「今天」
  会和服务端差一天 ⇒ 三种坏结果：
    - 番茄钟记到的那天算错（记成昨天）；
    - 补录时选到的那天被服务端当成「未来的日期」⇒ **整份报告被拒收**；
    - 图鉴页热力图的窗口和服务端错开 ⇒ 最后一天那格看着是空的。

★ 这条测试怎么造出「本地时区 ≠ UTC+8」的环境：
  浏览器时区设成 **Pacific/Kiritimati（UTC+14）**，时钟钉在
  **2026-11-25T15:30:00Z**
  ＝ 北京时间 2026-11-25 23:30 ＝ 本地时间 2026-11-26 05:30
  ⇒ **本地日期与服务端日期正好差一天** ⇒ 实现只要还用本地时区就会露馅。

  ★ 另有一条用例专门断言「此刻的本地日期确实是 2026-11-26」——
    免得哪天这台机器的时区变了、测试变成「什么都没测」还照样绿。
"""

from __future__ import annotations

import http.server
import pathlib
import shutil
import socketserver
import threading
import unittest

from tests import browser_path

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

# 钉死的时刻：2026-11-25T15:30:00Z
#   北京时间（UTC+8）＝ 2026-11-25 23:30 ⇒ 服务端认的「今天」是 2026-11-25
#   UTC+14（本地）   ＝ 2026-11-26 05:30 ⇒ 本地日期是 2026-11-26（**差一天**）
FAKE_NOW_MS = 1795620600000
CST_TODAY = "2026-11-25"
LOCAL_TODAY = "2026-11-26"

# 浏览器时区：比 UTC+8 快 6 小时 ⇒ 深夜那一段本地日期会比服务端快一天
LOCAL_TZ = "Pacific/Kiritimati"

try:  # 这台机器没装 chromium 时不该让整个测试文件导入失败
    CHROME = browser_path.chromium_path()
except Exception:  # pragma: no cover - 环境问题
    CHROME = ""

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # noqa: BLE001
    HAVE_PLAYWRIGHT = False


# 把页面的 Date 钉死在一个时刻（版本无关 ⇒ 不用 Playwright 的 clock API）
FAKE_DATE_SCRIPT = """
(() => {
  const FIXED = %d;
  const RealDate = Date;
  function FakeDate(...args) {
    if (args.length === 0) return new RealDate(FIXED);
    return new RealDate(...args);
  }
  FakeDate.now = () => FIXED;
  FakeDate.parse = RealDate.parse;
  FakeDate.UTC = RealDate.UTC;
  FakeDate.prototype = RealDate.prototype;
  window.Date = FakeDate;
})();
""" % FAKE_NOW_MS


def _make_server(directory: pathlib.Path):
    """在 directory 上起一个静态服务器（端口 0 = 随机端口，免跟别的测试撞车）。"""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):  # 别把日志糊满终端
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class ClientTimezoneBrowserTest(unittest.TestCase):
    """真浏览器 + 非 UTC+8 时区 + 假时钟 ⇒ 客户端算的「今天」必须还是服务端那个。"""

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过时区口径的浏览器测试")
        if not CHROME:
            raise unittest.SkipTest("找不到 chromium，跳过时区口径的浏览器测试")
        cls.httpd = _make_server(SITE_DIR)
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def _open(self, path: str, ready_js: str = ""):
        """开一个标签页：本地时区 = UTC+14，时钟钉死在 FAKE_NOW_MS。"""
        context = self.browser.new_context(
            timezone_id=LOCAL_TZ, viewport={"width": 390, "height": 844}
        )
        page = context.new_page()
        page.add_init_script(FAKE_DATE_SCRIPT)
        # service worker 会把页面缓存起来 ⇒ 测试里别让它插手
        page.route("**/sw.js", lambda route: route.abort())
        page.goto(self.origin + path, wait_until="load")
        if ready_js:
            # 等页面的脚本真的跑起来（load 只保证资源加载完，脚本可能还在后面）
            page.wait_for_function(ready_js, timeout=30000)
        return context, page

    # -- 用例 -----------------------------------------------------------

    def test_the_test_environment_really_is_not_utc_plus_8(self):
        """★ 先证明「此刻本地日期 ≠ 服务端日期」—— 不然下面的断言可能是空转。"""
        context, page = self._open("/words/index.html", "() => typeof WordsLanding !== 'undefined'")
        try:
            local_day = page.evaluate(
                "() => new Date().toLocaleDateString('sv')"  # sv = YYYY-MM-DD
            )
        finally:
            context.close()
        self.assertEqual(
            local_day, LOCAL_TODAY,
            "这台机器的时区变了（本地日期是 %s，不再与服务端差一天）⇒ "
            "这条测试已经测不出东西了" % (local_day,),
        )

    def test_the_words_page_calls_the_cst_day_today(self):
        """★ 背单词页的「今天」＝ 服务端的今天（UTC+8），不是浏览器本地日期。"""
        context, page = self._open("/words/index.html", "() => typeof WordsLanding !== 'undefined'")
        try:
            today_key = page.evaluate("() => WordsLanding.todayKey()")
            last_days = page.evaluate("() => WordsLanding.lastDays(3)")
        finally:
            context.close()
        self.assertEqual(
            today_key, CST_TODAY,
            "背单词页把 %s 当成今天（服务端认的是 %s）" % (today_key, CST_TODAY),
        )
        self.assertEqual(
            last_days, ["2026-11-23", "2026-11-24", "2026-11-25"],
            "背单词页的「最近几天」不是按北京时间算的：%r" % (last_days,),
        )

    def test_the_pomodoro_page_records_the_cst_day(self):
        """★ 番茄钟页记的那天＝ 服务端的今天（否则计时器会记到昨天）。"""
        context, page = self._open("/pomodoro/index.html", "() => typeof todayStr === 'function'")
        try:
            today = page.evaluate("() => todayStr()")
            picks = page.evaluate("() => pickDays(3)")
        finally:
            context.close()
        self.assertEqual(
            today, CST_TODAY,
            "番茄钟页把 %s 当成今天（服务端认的是 %s）" % (today, CST_TODAY),
        )
        self.assertEqual(
            picks, ["2026-11-25", "2026-11-24", "2026-11-23"],
            "番茄钟页补录时能选到的日子不是按北京时间算的：%r" % (picks,),
        )

    def test_the_dex_page_heatmap_ends_on_the_cst_day(self):
        """★ 图鉴页热力图的最后一格＝ 服务端的今天（否则她看着像没记录）。"""
        context, page = self._open("/index.html")
        try:
            page.wait_for_selector("#heatMap .hcell", timeout=30000)
            titles = page.evaluate(
                "() => [...document.querySelectorAll('#heatMap .hcell')].map(c => c.getAttribute('title') || '')"
            )
        finally:
            context.close()
        self.assertTrue(titles, "热力图一格都没画出来")
        self.assertTrue(
            titles[-1].startswith(CST_TODAY),
            "热力图最后一格是 %r（应该是 %s 那天）" % (titles[-1], CST_TODAY),
        )


if __name__ == "__main__":
    unittest.main()
