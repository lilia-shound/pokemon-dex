"""备考（背单词）记数页（`site/words/`）的测试。

两件事：

1. **口径的端到端一致性** —— 页面算出来的额度要跟服务端（`scripts/sources.py`）
   算的**一样**（按天 floor 再相加，不跨天凑零头）；
   页面生成的 issue body 要能被服务端的 `scripts/collect_reports.py` 的 `parse_report` 认。
2. **真实浏览器里的观感断言**（Playwright）—— 输入、快捷按钮、按天 floor、
   补录历史、localStorage 记住数字、注册表拉不到时照样渲染、零外链、手机宽度下不横向溢出。

跑法：

    python3 -B run_tests.py                          # 整套（浏览器那部分自己起本地静态服务器）
    python3 -B -m tests.test_words_landing           # 只跑这个文件（要在仓库根目录下跑）
"""

from __future__ import annotations

import datetime
import http.server
import json
import pathlib
import re
import shutil
import socketserver
import threading
import unittest
import urllib.parse

from scripts import collect_reports as cr
from scripts import sources as src
from tests import browser_path

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES_JSON = ROOT / "data" / "sources.json"
SITE_REGISTRY = ROOT / "site" / "sources.json"
WORDS_REGISTRY = ROOT / "site" / "words" / "sources.json"
APP_JS = ROOT / "site" / "words" / "app.js"
INDEX_HTML = ROOT / "site" / "words" / "index.html"
STYLE_CSS = ROOT / "site" / "words" / "style.css"
WORDS_DIR = ROOT / "site" / "words"
MANIFEST = WORDS_DIR / "manifest.webmanifest"
ICON = WORDS_DIR / "icon.svg"
ICON_MASKABLE = WORDS_DIR / "icon-maskable.svg"
CHROME = browser_path.chromium_path()   # 别写死版本号 —— 沙箱刷新会换 Playwright 版本

# 假装「已经是主屏图标打开的」：把 matchMedia 改成 display-mode: standalone 时命中。
_STANDALONE_INIT_SCRIPT = """
  (function () {
    const real = window.matchMedia;
    window.matchMedia = function (query) {
      if (String(query).indexOf('display-mode: standalone') >= 0) {
        return {
          matches: true, media: String(query), onchange: null,
          addListener: function () {}, removeListener: function () {},
          addEventListener: function () {}, removeEventListener: function () {},
          dispatchEvent: function () { return false; },
        };
      }
      return real.call(window, query);
    };
  })();
"""

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False


class WordsRegistryTest(unittest.TestCase):
    """记数页读的那份注册表不许和服务端算账用的那份漂移。

    页面读的是**站根**那份（`site/sources.json`）—— 它是 `scripts/deploy.sh`
    从 `data/sources.json` 自动同步出来的**部署产物** ⇒ 改 `data/sources.json`
    的 `per`，重新部署就生效，**永远不会漂移**。

    反过来：`site/words/` 下再放一份手工副本的话，部署脚本根本不管它 ⇒
    副本一漂，页面显示的额度就和服务端给的对不上（这正是上一轮的 Critical）。
    """

    def test_the_site_registry_matches_the_server_side_registry(self):
        """`site/sources.json` 与 `data/sources.json` **整份**相等。"""
        self.assertTrue(SITE_REGISTRY.exists(), "site/sources.json 不存在")
        server = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
        site = json.loads(SITE_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(site, server, "site/sources.json 与 data/sources.json 不一致")

    def test_the_site_registry_knows_the_words_source(self):
        """站根那份里也要有 words 源（页面就是从它读 per 的）。"""
        site = json.loads(SITE_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(site["sources"]["words"]["per"], 100)
        self.assertEqual(site["sources"]["words"]["enabled"], True)

    def test_there_is_no_second_hand_copied_registry(self):
        """`site/words/` 下不许再留一份手工副本（部署脚本永远不会更新它）。"""
        self.assertFalse(
            WORDS_REGISTRY.exists(),
            "site/words/sources.json 还在 —— 那是一份永远同步不上的手工副本，"
            "页面会读到它而不是 deploy.sh 同步的那份",
        )

    def test_the_page_reads_the_site_root_registry(self):
        """页面读的是站根那份（`../sources.json`），不是同目录的副本。"""
        app = APP_JS.read_text(encoding="utf-8")
        self.assertIn("const REGISTRY_PATH = '../sources.json';", app)
        self.assertNotIn(
            "const REGISTRY_PATH = 'sources.json';", app,
            "页面读的是同目录那份手工副本 —— 它永远不会被 deploy.sh 更新",
        )


class WordsPwaAssetsTest(unittest.TestCase):
    """记数页的「加到主屏」资产（manifest / 图标 / apple-touch-icon）。

    为什么要有这些：她在**手机上**背词 ⇒ 记数页得能像 App 一样一点就开
    ⇒ 需要 manifest（安卓 Chrome）+ apple-touch-icon（iOS Safari）。
    """

    def test_the_manifest_exists_and_is_valid_json(self):
        """manifest 存在，而且是一份合法的 JSON。"""
        self.assertTrue(MANIFEST.exists(), "site/words/manifest.webmanifest 不存在")
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_the_manifest_describes_an_app(self):
        """manifest 里那几项「像个 App」的值都得对。"""
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertTrue(data.get("name"), "manifest 没有 name")
        self.assertTrue(data.get("short_name"), "manifest 没有 short_name")
        self.assertEqual(data.get("display"), "standalone",
                         "display 不是 standalone ⇒ 加到主屏后还是浏览器的样子")
        self.assertTrue(data.get("start_url"), "manifest 没有 start_url")
        self.assertTrue(data.get("theme_color"), "manifest 没有 theme_color")
        self.assertTrue(data.get("background_color"), "manifest 没有 background_color")

    def test_the_manifest_start_url_is_local(self):
        """start_url / scope 必须是**相对路径**（不许外链，她那边外链会挂）。"""
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for key in ("start_url", "scope"):
            url = data.get(key, "")
            self.assertFalse(url.startswith("http://") or url.startswith("https://"),
                             "%s 指到了外链：%s" % (key, url))
            self.assertFalse(url.startswith("//"), "%s 是协议相对的外链：%s" % (key, url))

    def test_the_manifest_lists_the_local_icons(self):
        """icons 至少两个：普通版 + maskable 版，且都是**本地文件**。"""
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        icons = data.get("icons") or []
        self.assertGreaterEqual(len(icons), 2, "manifest 里的 icons 太少：%r" % (icons,))
        purposes = set()
        for icon in icons:
            src = icon.get("src", "")
            self.assertFalse(src.startswith("http://") or src.startswith("https://"),
                             "图标指到了外链：%s" % src)
            self.assertFalse(src.startswith("data:"),
                             "图标不许用 data-URI（要本地文件）：%s" % src)
            self.assertTrue((WORDS_DIR / src).exists(), "图标文件不存在：%s" % src)
            purposes.add(icon.get("purpose", "any"))
        self.assertIn("maskable", purposes, "icons 里少了 maskable 版（安卓会裁形状）")

    def test_the_icon_files_exist(self):
        """两个图标文件都在（普通版 + maskable 版）。"""
        self.assertTrue(ICON.exists(), "site/words/icon.svg 不存在")
        self.assertTrue(ICON_MASKABLE.exists(), "site/words/icon-maskable.svg 不存在")

    def test_the_icon_is_drawn_with_shapes_not_fonts(self):
        """图标里不许依赖**字体**渲染文字。

        她手机上（尤其电子墨水屏）不一定有那个字形 ⇒ 用 `<text>` 画的图标
        在她那边可能变成一个方框。字母 A 用路径画 ⇒ 哪儿都长一个样。
        """
        for path in (ICON, ICON_MASKABLE):
            svg = path.read_text(encoding="utf-8")
            self.assertNotIn("<text", svg, "%s 用了 <text>（字体不一定有那个字形）" % path.name)
            self.assertIn("<path", svg, "%s 里没有路径（画不出东西）" % path.name)

    def test_the_page_links_the_manifest_and_the_apple_touch_icon(self):
        """HTML 里要有 manifest 链接 + apple-touch-icon（iOS「添加到主屏幕」靠它）。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r"""<link[^>]+rel=["']manifest["'][^>]*>""",
                         "HTML 里没有 manifest 链接")
        self.assertRegex(html, r"""<link[^>]+rel=["']apple-touch-icon["'][^>]*>""",
                         "HTML 里没有 apple-touch-icon")
        # 两个 href 都得是**本地文件**
        for m in re.finditer(r"""<link[^>]+rel=["'](?:manifest|apple-touch-icon)["'][^>]*>""", html):
            tag = m.group(0)
            href = re.search(r"""href\s*=\s*["']([^"']+)["']""", tag)
            self.assertIsNotNone(href, "link 标签里没有 href：%s" % tag)
            url = href.group(1)
            self.assertFalse(url.startswith("http://") or url.startswith("https://"),
                             "图标/manifest 指到了外链：%s" % url)
            self.assertFalse(url.startswith("data:"),
                             "图标不许用 data-URI（要本地文件）：%s" % url)
            self.assertTrue((WORDS_DIR / url).exists(), "引用的文件不存在：%s" % url)

    def test_the_viewport_is_untouched(self):
        """viewport 那一行一个字都不许动（改了会在她手机上跑版）。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">',
                      html, "viewport 被改了")

    def test_there_is_no_service_worker(self):
        """记数页**不引 Service Worker**。

        它不要求离线可用 ⇒ 加了 SW 只会带来「缓存陈旧」的问题
        （她改了口径却还看老页面）。番茄钟要 SW 是因为它要计时，这里不要。
        """
        self.assertFalse((WORDS_DIR / "sw.js").exists(),
                         "site/words/sw.js 不该存在（记数页不要 SW）")
        app = APP_JS.read_text(encoding="utf-8")
        self.assertNotIn("serviceWorker", app, "app.js 里不许注册 Service Worker")
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("serviceWorker", html, "HTML 里不许注册 Service Worker")


class WordsLandingBrowserTest(unittest.TestCase):
    """真实浏览器（Playwright）里的记数页观感断言。

    浏览器自己起一个本地静态服务器（site/ 目录）—— 不依赖外网，
    也不占用固定端口（免得跟别的测试撞车）。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过记数页的浏览器测试")
        if not (shutil.which("chromium") or pathlib.Path(CHROME).exists()):
            raise unittest.SkipTest("找不到 chromium，跳过记数页的浏览器测试")
        cls.httpd = _make_server(ROOT / "site")
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.base = cls.origin + "/words/index.html"
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

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 390, "height": 844})
        self.page_errors = []
        self.console_errors = []
        self.console_warnings = []
        self.registry_requests = []
        self.all_requests = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        self.page.on("console", lambda m: self.console_warnings.append(m.text)
                     if m.type == "warning" else None)
        self.page.on("request", lambda r: self.all_requests.append(r.url))
        # 注册表（sources.json）是页面唯一的网络请求 —— 这里把它拦下来，
        # 让每个用例自己决定「服务端给什么」「拉不到」。
        self.registry_response = None

        def handle(route):
            self.registry_requests.append(route.request.url)
            if self.registry_response is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.registry_response))

        # **相对路径**取的那份（页面就在 site/words/ 下，同源）
        self.page.route("**/sources.json", handle)

    def tearDown(self):
        self.page.close()

    # -- 辅助 -----------------------------------------------------------

    def open_page(self, query: str = "", width: int = 390):
        """打开记数页并等它真的渲染完（github.io 慢，不固定 sleep）。"""
        self.page.set_viewport_size({"width": width, "height": 844})
        url = self.base + (("?" + query) if query else "")
        self.page.goto(url, wait_until="commit")
        return url

    def wait_rendered(self):
        """等页面画出额度行（**不等于**注册表生效了）。"""
        self.page.wait_for_function(
            """() => {
              const el = document.querySelector('#creditLine');
              return !!el && (el.textContent || '').trim().length > 0;
            }""",
            timeout=30000,
        )

    def wait_app_ready(self):
        """等**页面脚本真的跑起来了**（`WordsLanding` 挂好了）。

        为什么不能只等 `wait_rendered()`：额度行在静态 HTML 里就有字
        （「今天还没记」）⇒ 它有字不代表 app.js 已经执行过
        ⇒ 这时候去问 `WordsLanding.xxx` 会撞上「is not defined」。
        """
        self.page.wait_for_function(
            "() => typeof window.WordsLanding !== 'undefined'",
            timeout=30000,
        )

    def wait_registry_applied(self):
        """等**注册表真的生效**（读到真注册表了，或者兜底口径生效了）。

        页面第一次渲染用的是默认口径（per=100），注册表是之后异步换上去的 ⇒
        「画完了」**不等于**「注册表生效了」。要断言注册表口径，必须等这个标志。
        """
        self.page.wait_for_function(
            "() => !!(window.WordsLanding && WordsLanding.state.registryLoaded)",
            timeout=30000,
        )

    def today_key(self) -> str:
        """页面自己认为的「今天」（用页面的算式，别在 Python 那边另算一遍）。"""
        return self.page.evaluate("() => WordsLanding.todayKey()")

    def shift_key(self, key: str, days: int) -> str:
        """把日期键挪几天（-1 = 昨天）。"""
        y, m, d = (int(x) for x in key.split("-"))
        return (datetime.date(y, m, d) + datetime.timedelta(days=days)).isoformat()

    def fill(self, value: str):
        """往大数字输入框里填个数。"""
        self.page.fill("#countInput", value)
        self.page.wait_for_function(
            "(v) => document.querySelector('#countInput').value === v",
            arg=value, timeout=10000,
        )

    def pick_day(self, key: str):
        """点某一天的小卡片（补录历史）。"""
        self.page.click('.dcell[data-day="%s"]' % key)
        # 等那张卡片变成「选中」的样子（class=on）
        self.page.wait_for_function(
            """(k) => {
              const el = document.querySelector('.dcell[data-day="' + k + '"]');
              return !!el && el.classList.contains('on');
            }""",
            arg=key, timeout=10000,
        )

    def credit_text(self) -> str:
        return self.page.inner_text("#creditLine")

    def week_credit_text(self) -> str:
        return self.page.inner_text("#weekCredit")

    def issue_query(self) -> dict:
        """**老办法**那个链接里的 title / body。

        ★ 主按钮 `#submitBtn` 现在是 `<button>`（点一下直接交，不再跳 GitHub）⇒
          预填好的 issue 链接搬到了老办法那个链接 `#oldWayBtn` 上。
          这里跟着换过来 —— 断言的还是「页面生成的那份 issue URL」。
        """
        url = self.page.get_attribute("#oldWayBtn", "href")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return {"url": url, "title": q.get("title", [""])[0], "body": q.get("body", [""])[0]}

    def record_credit_history(self):
        """在页面里挂一个观察者，记下额度行的**每一次**变化。

        为什么要在页面里记：Python 线程不一定能在两次渲染之间插进去
        ⇒「兜底口径那一版」与「注册表口径那一版」两态都只能在页面里抓。
        """
        self.page.add_init_script("""
          window.__creditHistory = [];
          window.__pushCredit = function () {
            const el = document.querySelector('#creditLine');
            if (!el) return;
            const v = (el.textContent || '').trim();
            const h = window.__creditHistory;
            if (h[h.length - 1] !== v) h.push(v);
          };
          document.addEventListener('DOMContentLoaded', function () {
            new MutationObserver(window.__pushCredit).observe(document.body, {
              subtree: true, childList: true, characterData: true });
            window.__pushCredit();
          });
          (function poll() {
            window.__pushCredit();
            requestAnimationFrame(poll);
          })();
        """)

    def credit_history(self) -> list:
        """页面记下来的额度行序列（按出现的先后）。"""
        return self.page.evaluate("() => window.__creditHistory || []")

    def delay_the_registry_in_the_browser(self, seconds: float):
        """在**浏览器里**把注册表那次 fetch 延后 —— 模拟「CDN 慢」。

        为什么在浏览器里延后，而不是在 route handler 里 sleep：
        handler 里 sleep 会把 Playwright 的事件循环整个占住
        （页面照跑，但测试这边读什么都要等到 sleep 结束）⇒ 那样就**测不出中间态**了。
        """
        self.page.add_init_script("""
          (function () {
            const realFetch = window.fetch;
            window.fetch = function (input, init) {
              const url = (typeof input === 'string') ? input : ((input && input.url) || '');
              if (url.indexOf('sources.json') >= 0) {
                return new Promise(function (resolve) {
                  setTimeout(function () { resolve(realFetch(input, init)); }, %d);
                });
              }
              return realFetch(input, init);
            };
          })();
        """ % int(seconds * 1000))

    # -- 用例 -----------------------------------------------------------

    def test_a_hundred_and_fifty_words_is_one_credit(self):
        """输入 150 ⇒ 显示「1 个额度」；输入 250 ⇒ 「2 个额度」。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.assertIn("150 个", self.credit_text())
        self.assertIn("1 个额度", self.credit_text())
        self.fill("250")
        self.assertIn("250 个", self.credit_text())
        self.assertIn("2 个额度", self.credit_text())

    def test_ninety_nine_words_is_still_zero_credit(self):
        """零头不计：99 个词 = 0 个额度（但页面要说清楚「今天记了 99 个」）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("99")
        self.assertIn("99 个", self.credit_text())
        self.assertIn("0 个额度", self.credit_text())

    def test_credits_are_floored_per_day_not_over_the_total(self):
        """**按天 floor 再相加**：第一天 250、第二天 50 ⇒ 合计 **2 个额度**（不是 3）。

        floor(250/100) + floor(50/100) = 2 + 0 = 2；
        要是按总数算就是 floor(300/100) = 3 ⇒ **多算一个额度** ⇒ 这条必须钉死。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.fill("250")
        self.pick_day(yesterday)
        self.fill("50")
        self.assertEqual(self.week_credit_text(), "2 个额度")
        # 而且要和服务端算的**一样**（同一个口径）
        cfg = src.load_config(SOURCES_JSON)
        ledger = {today: {"words": 250}, yesterday: {"words": 50}}
        server = src.credits_for_days(cfg, ledger)
        self.assertEqual(sum(server.values()), 2)
        self.assertEqual(sum(server.values()), 3 - 1)  # 不是 3
        # 页面上那一天的额度也是按天算的：50 个 = 0 个额度
        self.assertIn("0 个额度", self.credit_text())

    def test_the_quick_buttons_work(self):
        """快捷按钮 +20 生效（点一下就 +20）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.page.click('#quick button[data-add="20"]')
        self.page.wait_for_function(
            "() => document.querySelector('#countInput').value === '170'",
            timeout=10000,
        )
        self.assertIn("170 个", self.credit_text())
        # 清零
        self.page.click('#quick button[data-add="0"]')
        self.page.wait_for_function(
            "() => document.querySelector('#countInput').value === ''",
            timeout=10000,
        )
        self.assertIn("还没记", self.credit_text())

    def test_the_number_survives_a_reload(self):
        """localStorage 记住：刷新后数字还在。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.page.reload(wait_until="commit")
        self.wait_registry_applied()
        self.assertEqual(self.page.input_value("#countInput"), "150")
        self.assertIn("1 个额度", self.credit_text())

    def test_a_day_can_be_backfilled(self):
        """点某一天可以补录历史（改的是那一天，不是今天）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.pick_day(yesterday)
        self.fill("80")
        self.assertIn("80 个", self.credit_text())
        # 今天还是空的
        self.pick_day(today)
        self.assertIn("今天还没记", self.credit_text())

    def test_only_days_with_counts_go_into_the_body(self):
        """body 里**只放有数的天**（零不报），标题是最新的一天。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.fill("150")
        self.pick_day(yesterday)
        self.fill("80")
        q = self.issue_query()
        body = json.loads(q["body"])
        self.assertEqual(body.get("v"), 1)
        self.assertEqual(body.get("source"), "words")
        self.assertEqual(body.get("days"), {today: 150, yesterday: 80})
        self.assertEqual(q["title"], "[report] words " + today)
        self.assertTrue(q["url"].startswith(
            "https://github.com/lilia-shound/pokemon-dex/issues/new?"))

    def test_the_issue_body_is_accepted_by_the_server_parser(self):
        """**端到端**：页面生成的 body，服务端的 parse_report 要认。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.fill("150")
        self.pick_day(yesterday)
        self.fill("80")
        body = self.issue_query()["body"]
        cfg = src.load_config(SOURCES_JSON)
        days, err = cr.parse_report(body, cfg, today)
        self.assertIsNone(err, "服务端不认这个 body：%s" % err)
        self.assertEqual(days, {today: 150, yesterday: 80})
        # 服务端按这个 body 折算出来的额度 = 1 + 0 = 1（按天 floor）
        ledger = {d: {"words": v} for d, v in days.items()}
        self.assertEqual(sum(src.credits_for_days(cfg, ledger).values()), 1)

    def test_the_page_falls_back_to_a_hundred_when_the_registry_is_unreachable(self):
        """拉不到 sources.json ⇒ 退回兜底的 100（**不能卡在「载入中…」**）。"""
        self.registry_response = None      # 拦下来直接 abort
        self.open_page()
        self.wait_registry_applied()        # 等兜底口径生效（注册表试过了）
        self.fill("150")
        self.assertIn("1 个额度", self.credit_text())
        self.assertIn("100 个 = 1 个额度", self.page.inner_text("#perNote"))
        # 失败原因不许被整个吞掉 —— 控制台里要看得见「为什么退回默认口径」
        self.assertTrue(
            any("注册表" in w for w in self.console_warnings),
            "注册表拉不到时应该有一条 warning：%r" % (self.console_warnings,),
        )

    def test_the_credit_comes_from_the_registry(self):
        """per 真的来自注册表（**两态都要看到**）。

        页面**第一次**渲染用的是兜底口径（per=100）⇒ 150 个 = 1 个额度；
        注册表（per=50）生效后要重算成 150 // 50 = 3 个额度。

        为了不靠时序侥幸，这里把注册表那次 fetch **人为延后**：
        先用兜底口径把 150 填进去（看到「1 个额度」），
        等注册表生效后再看到「3 个额度」—— 两态都在，才证明 per 是从注册表来的。
        """
        self.registry_response = _registry(per=50)
        self.delay_the_registry_in_the_browser(1.5)
        self.record_credit_history()
        self.open_page()
        self.wait_rendered()
        self.fill("150")
        # 兜底口径那一版：150 // 100 = 1 个额度
        self.assertIn("1 个额度", self.credit_text())
        self.wait_registry_applied()
        # 注册表口径那一版：150 // 50 = 3 个额度
        self.assertIn("3 个额度", self.credit_text())
        self.assertIn("50 个 = 1 个额度", self.page.inner_text("#perNote"))
        # 两态都要在页面上出现过，而且**先后顺序**要对
        history = self.credit_history()
        first = next((i for i, h in enumerate(history) if "1 个额度" in h), -1)
        second = next((i for i, h in enumerate(history) if "3 个额度" in h), -1)
        self.assertGreaterEqual(first, 0, "没看到兜底口径（per=100）那一版：%r" % (history,))
        self.assertGreaterEqual(second, 0, "没看到注册表口径（per=50）那一版：%r" % (history,))
        self.assertLess(first, second, "两次渲染的先后顺序不对：%r" % (history,))

    def test_the_count_is_clamped_to_the_daily_cap_before_submitting(self):
        """单日超上限的词数要**夹到 maxPerDay** 再提交。

        服务端 `scripts/collect_reports.py` 的规则是「任一天超过 maxPerDay
        ⇒ **整份报告丢弃**」⇒ 页面要是原样报上去，她会以为交上去了，其实被丢了。
        """
        self.registry_response = _registry(per=100, max_per_day=500)
        self.open_page()
        self.wait_registry_applied()
        self.fill("9999")
        # 页面上有提示（原始值与上限都在）
        note = self.page.inner_text("#clampNote")
        self.assertIn("9999", note)
        self.assertIn("500", note)
        # 提交给服务端的是夹过的值
        body = json.loads(self.issue_query()["body"])
        today = self.today_key()
        self.assertEqual(body.get("days"), {today: 500})
        # 而且服务端认这份报告（没被丢掉）
        cfg = src.load_config(SOURCES_JSON)
        days, err = cr.parse_report(self.issue_query()["body"], cfg, today)
        self.assertIsNone(err, "服务端不认这个 body：%s" % err)
        self.assertEqual(days, {today: 500})

    def test_zero_days_shows_a_friendly_hint(self):
        """一天都没记 ⇒ 显示友好提示（不是空白页、也不是一个孤零零的 0）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.assertIn("今天还没记", self.credit_text())
        self.assertNotIn("0 个额度", self.credit_text())
        # 提交按钮没有量时是禁用的
        self.assertEqual(self.page.get_attribute("#submitBtn", "aria-disabled"), "true")

    # -- 上一轮评审提的小问题 ---------------------------------------------

    def test_the_credit_line_shows_the_original_count_when_clamped(self):
        """夹到单日上限时，额度行要写出**原值**。

        输入框里是原值（9999），额度行是夹后的（500）—— 只看额度行会以为系统记错了。
        """
        self.registry_response = _registry(per=100, max_per_day=500)
        self.open_page()
        self.wait_registry_applied()
        self.fill("9999")
        line = self.credit_text()
        self.assertIn("500 个", line)
        self.assertIn("原始 9999", line)

    def test_a_words_link_does_not_overwrite_what_she_already_recorded(self):
        """带 `?words=150` 的链接不许**悄悄改掉**当天手填的数。

        这个口子本来是留给以后「自动导入」那一层的 ⇒ 预填可以，
        覆盖别人已经记好的数不行（她以为记的是 80，结果被改成 150）。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("80")
        # 同一个链接再打开一次（模拟「点了个带 ?words= 的链接」）
        self.open_page(query="words=150")
        self.wait_registry_applied()
        self.assertEqual(self.page.input_value("#countInput"), "80")
        self.assertIn("没有覆盖", self.page.inner_text("#linkNote"))
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {self.today_key(): 80})

    def test_a_words_link_prefills_when_the_day_is_still_empty(self):
        """当天还没记时，`?words=150` 把数预填进去，并说明「可以改」。"""
        self.registry_response = _registry(per=100)
        self.open_page(query="words=150")
        self.wait_registry_applied()
        self.assertEqual(self.page.input_value("#countInput"), "150")
        self.assertIn("预填", self.page.inner_text("#linkNote"))
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {self.today_key(): 150})

    # -- 预填上次的数字（减摩擦） -----------------------------------------

    def seed_days(self, days: dict):
        """往本机存储里塞几条记录（相当于「她前几天记过了」），然后刷新。"""
        self.page.evaluate(
            "(v) => window.localStorage.setItem('pokemon-dex-words-v1', v)",
            json.dumps({"v": 1, "days": days}))
        self.page.reload(wait_until="commit")
        self.wait_registry_applied()

    def test_the_previous_count_is_prefilled_when_today_is_still_empty(self):
        """今天还没记、昨天记过 ⇒ 上次的数字填进输入框，并有一行提示。

        提示必须说清楚「这是上次的数，不是今天的」——
        她要是以为那是今天已经背完的数，就会白交一次。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({yesterday: 150})
        self.assertEqual(self.page.input_value("#countInput"), "150")
        note = self.page.inner_text("#prefillNote")
        self.assertIn("上次记的是", note)
        self.assertIn("150", note)
        self.assertIn("今天还没记", note)

    def test_todays_own_count_is_not_overwritten_by_the_previous_one(self):
        """今天已经有记录 ⇒ 不许用上次的数覆盖它。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({today: 120, yesterday: 150})
        self.assertEqual(self.page.input_value("#countInput"), "120")
        # 也没有预填提示了（没什么好提示的）
        self.assertTrue(self.page.locator("#prefillNote").is_hidden())

    def test_the_prefilled_number_does_not_count_as_a_credit(self):
        """**预填的数不进额度** —— 额度仍按实际已记的天算。

        昨天 150 个（1 个额度）⇒ 今天预填 150 个 **不算**今天的额度
        （不然「还没提交」的日子提前达标了）。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({yesterday: 150})
        # 输入框里是 150 …
        self.assertEqual(self.page.input_value("#countInput"), "150")
        # … 但今天还是「还没记」，额度只有昨天那 1 个
        self.assertIn("今天还没记", self.credit_text())
        self.assertEqual(self.week_credit_text(), "1 个额度")
        # 近 7 天的合计也不含今天（那个 150 还没算进去）
        total = self.page.inner_text("#weekTotal")
        self.assertIn("150 个", total)

    def test_the_prefilled_number_can_be_submitted_directly(self):
        """预填的数能**直接提交** —— 链接里就带着它。

        「改一下或直接提交」这句话要成立 ⇒ 提交按钮不能是灰的，
        报告里也得带上今天这个数（否则点开是空的，等于没得交）。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({yesterday: 150})
        self.assertNotEqual(self.page.get_attribute("#submitBtn", "aria-disabled"), "true")
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {today: 150, yesterday: 150})
        # 服务端认这份报告（没被丢掉）
        cfg = src.load_config(SOURCES_JSON)
        days, err = cr.parse_report(self.issue_query()["body"], cfg, today)
        self.assertIsNone(err, "服务端不认这个 body：%s" % err)
        self.assertEqual(days, {today: 150, yesterday: 150})

    def test_editing_the_prefilled_number_records_it(self):
        """她把预填的数改一下 ⇒ 变成今天真正的记录（额度从这时才算）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({yesterday: 150})
        self.fill("160")
        self.assertIn("160 个", self.credit_text())
        self.assertEqual(self.week_credit_text(), "2 个额度")
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {today: 160, yesterday: 150})

    def test_a_quick_button_works_on_top_of_the_prefilled_number(self):
        """快捷按钮在预填之上加（预填 150 ⇒ +20 ⇒ 170，不是 20）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        yesterday = self.shift_key(today, -1)
        self.seed_days({yesterday: 150})
        self.page.click('#quick button[data-add="20"]')
        self.page.wait_for_function(
            "() => document.querySelector('#countInput').value === '170'",
            timeout=10000,
        )
        self.assertIn("170 个", self.credit_text())
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {today: 170, yesterday: 150})

    def test_there_is_no_prefill_when_she_has_never_recorded_anything(self):
        """从来没记过 ⇒ 没有预填（输入框是空的，也没有提示）。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.assertEqual(self.page.input_value("#countInput"), "")
        self.assertTrue(self.page.locator("#prefillNote").is_hidden())

    def test_days_outside_the_week_window_are_called_out(self):
        """存 30 天、只显示 7 天 ⇒ 报告里可能有**她看不见**的日子 ⇒ 必须提示。

        存储窗口（30 天，服务端一次最多收 30 天）比显示窗口（7 天）大 ⇒
        几天没提交就攒下了窗口外的量 ⇒ 那几天在页面上一个格子都没有，
        却会被一起交上去 ⇒ 页面上得说清楚。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        today = self.today_key()
        old = self.shift_key(today, -10)          # 10 天前 ⇒ 在 30 天里，不在 7 天里
        # 塞进本机存储，再刷新一次（相当于「几天没提交，攒下了窗口外的量」）
        self.page.evaluate(
            "(v) => window.localStorage.setItem('pokemon-dex-words-v1', v)",
            json.dumps({"v": 1, "days": {old: 60}}))
        self.page.reload(wait_until="commit")
        self.wait_registry_applied()
        note = self.page.inner_text("#extraDaysNote")
        self.assertIn("不在「最近 7 天」里", note)
        self.assertIn(old[5:], note)               # 短标签（11-12）
        # 而它确实也在报告里（看不见 ≠ 不交上去 ⇒ 所以必须提示）
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {old: 60})

    def test_a_disabled_source_is_not_offered_for_submission(self):
        """注册表里 words 不是启用状态 ⇒ 服务端**整份拒收** ⇒ 页面不许装作能交。

        服务端 `scripts/collect_reports.py` 的 `parse_report`：
        源不是启用状态 ⇒ 报「源 words 当前是停用的」⇒ 交了也白交。
        """
        self.registry_response = _registry(per=100, enabled=False)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.assertIn("不是启用状态", self.page.inner_text("#disabledNote"))
        self.assertIn("停用", self.page.inner_text("#submitHint"))
        self.assertEqual(self.page.get_attribute("#submitBtn", "aria-disabled"), "true")
        # 额度还是算得出来（口径没变），但提交按钮不让点
        self.assertIn("1 个额度", self.credit_text())

    # -- 加到主屏（PWA） -------------------------------------------------

    def test_the_manifest_can_be_read_over_http(self):
        """那份 manifest 能被读到，而且内容是对的（线上也一样：静态文件原样发）。"""
        resp = self.page.request.get(self.origin + "/words/manifest.webmanifest")
        self.assertEqual(resp.status, 200, "manifest 取不到")
        data = resp.json()
        self.assertEqual(data.get("display"), "standalone")
        self.assertTrue(data.get("name"), "manifest 没有 name")
        self.assertTrue(data.get("short_name"), "manifest 没有 short_name")
        self.assertTrue(data.get("start_url"), "manifest 没有 start_url")
        self.assertTrue(data.get("icons"), "manifest 没有 icons")
        # 图标本身也要拿得到（图标 404 了，主屏上就是一块白）
        for icon in data.get("icons", []):
            r = self.page.request.get(self.origin + "/words/" + icon.get("src", ""))
            self.assertEqual(r.status, 200, "图标取不到：%s" % icon.get("src"))

    def test_the_install_hint_shows_when_the_page_is_not_standalone(self):
        """还在浏览器里用（不是主屏图标）⇒ 显示一行「可以加到主屏」的小字。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_app_ready()
        self.assertFalse(self.page.locator("#installNote").is_hidden(),
                         "不在 standalone 时应该有加主屏的提示")
        self.assertIn("主屏", self.page.inner_text("#installNote"))
        self.assertEqual(self.page.evaluate("() => WordsLanding.isStandalone()"), False)

    def test_the_install_hint_is_hidden_in_standalone_mode(self):
        """已经是主屏图标（standalone）⇒ 不许再提示（不烦人）。"""
        self.registry_response = _registry(per=100)
        self.page.add_init_script(_STANDALONE_INIT_SCRIPT)
        self.open_page()
        self.wait_app_ready()
        self.assertEqual(self.page.evaluate("() => WordsLanding.isStandalone()"), True)
        self.assertTrue(self.page.locator("#installNote").is_hidden(),
                        "已经是主屏图标了，不该再提示加主屏")

    def test_a_broken_display_mode_detection_does_not_break_the_page(self):
        """检测不到（matchMedia 抛异常）⇒ 当「不在 standalone」+ 整页不崩。

        「检测不到就当不在 standalone，不要报错」—— 这条得钉死。
        """
        self.registry_response = _registry(per=100)
        self.page.add_init_script("""
          window.matchMedia = function () { throw new Error('不支持'); };
        """)
        self.open_page()
        self.wait_app_ready()
        self.assertEqual(self.page.evaluate("() => WordsLanding.isStandalone()"), False)
        self.assertFalse(self.page.locator("#installNote").is_hidden(),
                         "检测不到时应该按「不在 standalone」处理（显示提示）")
        self.assertEqual(self.page_errors, [])

    def test_the_page_sends_exactly_one_network_request(self):
        """只许发一个网络请求：读 sources.json 拿 per。

        把**全部**请求都记下来：除了本地静态资源（html/css/js），
        只应该剩下那一条 sources.json。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.assertEqual(len(self.registry_requests), 1, str(self.registry_requests))
        others = [u for u in self.all_requests if not _is_local_static(u)]
        self.assertEqual(others, list(self.registry_requests),
                         "除了 sources.json 之外还有别的请求：%r" % (self.all_requests,))

    def test_the_registry_is_fetched_from_a_relative_path(self):
        """注册表走**相对路径**（同源），不是绝对 CDN 地址 —— 外链在她那边会挂。

        而且必须是**站根**那份（`/sources.json`）—— 那份由 `scripts/deploy.sh`
        从 `data/sources.json` 自动同步 ⇒ 口径永远不会漂移。
        """
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.assertEqual(len(self.registry_requests), 1)
        url = self.registry_requests[0]
        self.assertTrue(url.startswith(self.origin + "/"),
                        "注册表不是从同源的相对路径取的：%s" % url)
        self.assertTrue(url.endswith("/sources.json"), url)
        self.assertEqual(url, self.origin + "/sources.json",
                         "页面读的不是站根那份（deploy.sh 同步的那份）")

    def test_the_page_works_without_any_query_parameter(self):
        """被直接打开（没有 ? 参数）也能用。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.assertIn("1 个额度", self.credit_text())

    def test_the_page_works_with_a_query_parameter(self):
        """带参数打开（?day=）也能用 —— 落在那一天上。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        yesterday = self.shift_key(self.today_key(), -1)
        self.open_page(query="day=" + yesterday)
        self.wait_registry_applied()
        self.fill("80")
        self.assertIn("80 个", self.credit_text())
        body = json.loads(self.issue_query()["body"])
        self.assertEqual(body.get("days"), {yesterday: 80})

    def test_the_page_still_works_when_local_storage_is_unavailable(self):
        """localStorage 不可用（隐私模式 / 沙箱 iframe）⇒ **整页不能崩**。"""
        self.registry_response = _registry(per=100)
        self.page.add_init_script("""
          Object.defineProperty(window, 'localStorage', {
            configurable: true,
            get() { throw new Error('SecurityError: storage is disabled'); }
          });
        """)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.assertIn("1 个额度", self.credit_text())
        self.assertEqual(self.page_errors, [])
        # 并且要告诉她「刷新就没了」
        self.assertIn("不让存东西", self.page.inner_text("#storeNote"))

    def test_the_page_does_not_overflow_horizontally_on_a_phone(self):
        """手机宽度下不许横向溢出。"""
        self.registry_response = _registry(per=100)
        for width in (320, 390, 430):
            with self.subTest(width=width):
                self.open_page(width=width)
                self.wait_rendered()
                overflow = self.page.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 1, "宽 %d 像素时横向溢出 %s 像素"
                                     % (width, overflow))

    def test_no_page_errors(self):
        """页面不许有未捕获的异常。"""
        self.registry_response = _registry(per=100)
        self.open_page()
        self.wait_registry_applied()
        self.fill("150")
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_the_page_has_no_external_resource(self):
        """不许引外部 CDN 的框架/字体/脚本 —— 外链在她那边（大陆直连）会挂。

        页面只准跟两处说话：自己站上的 sources.json，和 GitHub 的新建 issue 页。

        **看得要全**：
          - HTML 的 `src` / `href` 属性（**双引号与单引号都要看**），
            以及 HTML 里内联的 `url(...)`；
          - CSS 的 `url(...)` 与 `@import`；
          - JS 里的 URL 字面量（包括协议相对的 `//…`）。
        漏了任何一处 ⇒ 这条守卫就形同虚设。
        """
        allowed_prefix = "https://github.com/"

        def check_url(url: str, where: str):
            url = url.strip()
            self.assertFalse(
                url.startswith("http://") or url.startswith("https://"),
                "%s 引了外链：%s" % (where, url),
            )
            self.assertFalse(
                url.startswith("//"),
                "%s 引了协议相对的外链：%s" % (where, url),
            )

        # -- HTML：src / href 属性（双引号、单引号都算）--------------------
        html = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r"""(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)')""", html):
            url = m.group(1) if m.group(1) is not None else m.group(2)
            check_url(url, "记数页的 HTML")
        # -- HTML：内联的 url(...) -----------------------------------------
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]*)", html):
            check_url(m.group(1), "记数页的 HTML")

        # -- CSS：url(...) 与 @import --------------------------------------
        css = STYLE_CSS.read_text(encoding="utf-8")
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]*)", css):
            check_url(m.group(1), "style.css")
        self.assertNotIn("@import", css, "style.css 里不许 @import 外链")

        # -- JS：URL 字面量 -------------------------------------------------
        app = APP_JS.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s'\"]+", app):
            self.assertTrue(
                url.startswith(allowed_prefix),
                "app.js 里出现了意料之外的外链：%s" % url,
            )
        # 协议相对的外链（'//cdn.…'）
        for url in re.findall(r"(?<=['\"])//[A-Za-z0-9][^\s'\"]*", app):
            self.assertFalse(
                url.startswith("//"),
                "app.js 里出现了协议相对的外链：%s" % url,
            )


# -- 小工具 -------------------------------------------------------------


def _registry(per: int, max_per_day: int = 500, per_award: int = 2,
              enabled: bool = True) -> dict:
    """给页面看的一份 sources.json（只放页面要用的那几个字段）。"""
    return {
        "version": 1,
        "perAward": per_award,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "words": {
                "name": "背单词",
                "icon": "🔤",
                "unit": "个",
                "per": per,
                "mode": "push",
                "enabled": enabled,
                "maxPerDay": max_per_day,
            }
        },
    }


def _is_local_static(url: str) -> bool:
    """这个请求是不是本地静态服务器上的静态资源（html/css/js/manifest/图标）。

    记数页除了这些文件，不许再要别的东西 —— 别的都算「多余的网络请求」。
    （manifest 与图标是本地文件，不是外链。）
    """
    return bool(re.match(
        r"^http://127\.0\.0\.1:\d+/words/(index\.html|style\.css|app\.js|"
        r"manifest\.webmanifest|icon\.svg|icon-maskable\.svg)(\?.*)?$",
        url,
    ))


def _make_server(directory: pathlib.Path):
    """在 site/ 目录上起一个静态服务器（端口 0 = 随机端口，免得跟别的测试撞车）。"""

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


if __name__ == "__main__":
    unittest.main()
