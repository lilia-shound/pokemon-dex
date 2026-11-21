"""KOReader 手机落地页（`site/koreader/`）的测试。

两件事：

1. **线格式的跨语言对拍** —— JS 侧（落地页真正用的那份代码：`site/koreader/app.js`）
   与 Python 侧（`tests/test_koreader_lua.py` 的 `parse_days`）拿**同一份**用例文件
   （`tests/fixtures/koreader_wire_cases.txt`）逐例对拍。
   用例只有一份 ⇒ Lua / Python / JS 三端不可能各自漂移。
2. **真实浏览器里的观感断言**（Playwright）—— 数字对不对、额度算的是 4 不是 5、
   坏数据按规则整段丢掉、没有有效日期时给友好提示、按钮的 href 是合法的
   GitHub 新建 issue 链接（title/body 能解回正确的值）、手机宽度下不横向溢出。

跑法：

    python3 -B run_tests.py                          # 整套（浏览器那部分自己起本地静态服务器）
    python3 -B -m tests.test_koreader_landing        # 只跑这个文件（要在仓库根目录下跑）
"""

from __future__ import annotations

import http.server
import json
import pathlib
import re
import shutil
import socketserver
import subprocess
import threading
import unittest
import urllib.parse

from scripts import collect_reports as cr
from scripts import sources as src

# 落地页那侧的正则/实现就在 site/koreader/app.js 里 —— 这里不另抄一份，
# 只把它当**被测对象**（下面的 node 脚本会去 require 它）。
from tests import koreader_lua_bridge as bridge
from tests.test_koreader_lua import encode_days, parse_days  # noqa: F401  （对拍用的 Python 侧参考实现）

ROOT = bridge.ROOT
SOURCES_JSON = ROOT / "data" / "sources.json"
SITE_SOURCES_JSON = ROOT / "site" / "sources.json"
APP_JS = ROOT / "site" / "koreader" / "app.js"
INDEX_HTML = ROOT / "site" / "koreader" / "index.html"
STYLE_CSS = ROOT / "site" / "koreader" / "style.css"
SPEC_JS = ROOT / "tests" / "koreader_landing_spec.js"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

# 干净的那条线（fixture 的第一行）—— 页面上就是它
CLEAN_WIRE = "2026-11-21:25,2026-11-20:50"
CLEAN_DAYS = {"2026-11-21": 25, "2026-11-20": 50}

# 断言集至少要跑这么多条（防止用例文件被清空 ⇒ 一片绿但什么都没测）
MIN_WIRE_CASES = 21

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False


def quote_wire(wire: str) -> str:
    """把天数串安全地塞进 query string（`:` `,` 空格、Unicode 数字都要能原样还原）。"""
    return urllib.parse.quote(wire, safe="")


# JS 侧对拍脚本的输出（**整个进程只跑一次** —— 同一进程里跑两遍 node 纯属浪费）
_JS_SPEC_RESULT: dict | None = None


def run_js_spec() -> dict:
    """跑 JS 侧的对拍脚本（`tests/koreader_landing_spec.js`），返回它吐的 JSON。

    **只跑一次**：结果缓存在模块级变量里 ⇒ 同一个进程里不管谁要，都是同一份结果。
    （之前两个地方各跑了一遍 node ⇒ 同一个进程里 node 脚本被跑了两次。）
    """
    global _JS_SPEC_RESULT
    if _JS_SPEC_RESULT is not None:
        return _JS_SPEC_RESULT
    node = bridge.find_node()
    if not node:
        raise unittest.SkipTest("找不到 node，跳过落地页的 JS 对拍")
    proc = subprocess.run(
        [node, str(SPEC_JS)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "JS 对拍脚本没跑通（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr)
        )
    payload = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            payload = json.loads(line)
    if payload is None:
        raise AssertionError("JS 对拍脚本没有输出 JSON：\n" + proc.stdout)
    _JS_SPEC_RESULT = payload
    return payload


class KoreaderLandingWireTest(unittest.TestCase):
    """JS 侧的线格式解析 vs Python 侧的参考实现 —— 逐例对拍。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = run_js_spec()
        cls.cases = cls.payload["cases"]

    # -- 用例 -----------------------------------------------------------

    def test_wire_case_file_is_not_empty(self):
        """用例文件不能是空的 —— 空跑一遍也是「全过」，那是假绿。"""
        cases = bridge.read_wire_cases()
        self.assertTrue(cases, "线格式的用例文件不能是空的")
        self.assertGreaterEqual(len(cases), MIN_WIRE_CASES)

    def test_js_ran_every_wire_case(self):
        """JS 侧要真的把每一条线跑过 —— 用例与探针一一对应。"""
        cases = bridge.read_wire_cases()
        self.assertEqual(len(self.cases), len(cases))
        self.assertEqual([c["wire"] for c in self.cases], cases)

    def test_js_and_python_agree_on_every_wire_case(self):
        """逐例对拍：好坏线都要两边解出一样的结果。"""
        for row in self.cases:
            with self.subTest(wire=row["wire"]):
                self.assertEqual(row["decoded"], parse_days(row["wire"]))

    def test_js_and_python_agree_on_the_encoded_form(self):
        """编回去也要一样 —— 落地页会把解出来的天数再编回去（进 issue 的 body）。"""
        for row in self.cases:
            with self.subTest(wire=row["wire"]):
                self.assertEqual(row["encoded"], encode_days(parse_days(row["wire"])))

    def test_the_clean_wire_parses_to_the_two_days(self):
        """干净的那条线解出两天、两个数。"""
        row = next(c for c in self.cases if c["wire"] == CLEAN_WIRE)
        self.assertEqual(row["decoded"], CLEAN_DAYS)

    def test_credits_are_floored_per_day_not_over_the_total(self):
        """**按天 floor 再相加**：25 分钟 + 50 分钟、15 分钟 1 个额度 ⇒ 1 + 3 = 4。"""
        row = next(c for c in self.cases if c["wire"] == CLEAN_WIRE)
        self.assertEqual(row["credits"], 4, "不是 5 —— 不能跨天凑零头")

    def test_js_credits_match_the_server_side_ledger(self):
        """页面算出来的额度要跟服务端（scripts/sources.py）算的一样。"""
        cfg = src.load_config(SOURCES_JSON)
        ledger = {d: {"koreader": m} for d, m in CLEAN_DAYS.items()}
        server = sum(src.credits_for_days(cfg, ledger).values())
        row = next(c for c in self.cases if c["wire"] == CLEAN_WIRE)
        self.assertEqual(row["credits"], server)
        self.assertEqual(server, 4)

    def test_js_credits_clamp_to_the_daily_cap_like_the_server(self):
        """单日上限的口径也要一样（服务端先把原始量夹到 maxPerDay 再折算）。"""
        cfg = src.load_config(SOURCES_JSON)
        ledger = {"2026-11-21": {"koreader": 5000}}
        server = sum(src.credits_for_days(cfg, ledger).values())
        self.assertEqual(self.payload["cap"], cfg["sources"]["koreader"]["maxPerDay"])
        self.assertEqual(server, 1440 // 15)
        # JS 侧也夹（node 脚本里的自检：5000 分钟 ⇒ floor(cap / per)）
        self.assertEqual(self.payload["per"], cfg["sources"]["koreader"]["per"])

    def test_bad_chunks_are_dropped_not_zeroed(self):
        """坏段整段丢掉（**不 trim**）—— 不能变成 0 分钟的一天。"""
        by_wire = {c["wire"]: c["decoded"] for c in self.cases}
        self.assertEqual(by_wire["2026-11-21:25, 2026-11-20:50"], {"2026-11-21": 25})
        self.assertEqual(by_wire[" 2026-11-21:25"], {})
        self.assertEqual(by_wire["2026-11-21:25 "], {})
        self.assertEqual(by_wire["2026-11-21:2 5"], {})
        self.assertEqual(by_wire["2026-11-21:25,,2026-11-20:50"], CLEAN_DAYS)
        self.assertEqual(by_wire["2026-11-21:25,abc,2026-11-20:50"], CLEAN_DAYS)

    def test_unicode_digits_are_rejected(self):
        """数字只认 ASCII 0-9 —— Unicode 数字整段丢掉。"""
        by_wire = {c["wire"]: c["decoded"] for c in self.cases}
        self.assertEqual(by_wire["٢٠٢٦-11-21:5"], {})
        self.assertEqual(by_wire["2026-11-21:٢٥"], {})

    def test_a_chunk_with_a_trailing_newline_is_dropped(self):
        """段尾带**换行**的段整段丢掉 —— JS 侧与 Python / Lua 一致。

        JS 的 `$`（无 /m）与 Lua 的 `$` 只认字符串末尾 ⇒ 这种段不匹配；
        Python 以前用 `$` 会放过它（偏松）⇒ 现在 Python 侧也改成 fullmatch 了，
        三端在这一点上闭合。
        """
        by_wire = {c["wire"]: c["decoded"] for c in self.cases}
        self.assertIn("2026-11-21:25\n", by_wire, "用例文件里没有「段尾带换行」那条")
        self.assertEqual(by_wire["2026-11-21:25\n"], {})

    def test_the_site_copy_of_the_registry_knows_the_koreader_source(self):
        """线上那份 sources.json（site/sources.json）也得有 koreader 源。

        页面是从 CDN 上读它拿 `per` 的 —— 注册表里没有这个源就只能退回默认的 15。
        """
        self.assertTrue(SITE_SOURCES_JSON.exists(), "site/sources.json 不存在")
        site_cfg = json.loads(SITE_SOURCES_JSON.read_text(encoding="utf-8"))
        self.assertIn("koreader", site_cfg.get("sources", {}))
        self.assertEqual(site_cfg["sources"]["koreader"]["per"], 15)

    def test_the_landing_page_has_no_external_resource(self):
        """不许引外部 CDN 的框架/字体/脚本 —— 外链在她那边（大陆直连）会挂。

        页面只准跟两处说话：自己站上的 sources.json，和 GitHub 的新建 issue 页。

        **看得要全**：
          - HTML 的 `src` / `href` 属性（**双引号与单引号都要看**），
            以及 HTML 里内联的 `url(...)`；
          - CSS 的 `url(...)` 与 `@import`；
          - JS 里的 URL 字面量（包括协议相对的 `//…`）。
        漏了任何一处 ⇒ 这条守卫就形同虚设。
        """
        # 允许的外链前缀（自家 CDN 与 GitHub）
        allowed_prefix = (
            "https://lilia-shound.github.io/pokemon-dex/",
            "https://github.com/",
        )

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
            check_url(url, "落地页的 HTML")
        # -- HTML：内联的 url(...)（比如 style="background:url(...)"）-------
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]*)", html):
            check_url(m.group(1), "落地页的 HTML")

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
        # 协议相对的外链（'//cdn.…'）—— 引号后面直接跟 // 且后面是域名
        for url in re.findall(r"(?<=['\"])//[A-Za-z0-9][^\s'\"]*", app):
            self.assertFalse(
                url.startswith("//"),
                "app.js 里出现了协议相对的外链：%s" % url,
            )


class KoreaderLandingBrowserTest(unittest.TestCase):
    """真实浏览器（Playwright）里的落地页观感断言。

    浏览器自己起一个本地静态服务器（site/ 目录）—— 不依赖外网，
    也不占用固定端口（免得跟别的测试撞车）。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过落地页的浏览器测试")
        if not (shutil.which("chromium") or pathlib.Path(CHROME).exists()):
            raise unittest.SkipTest("找不到 chromium，跳过落地页的浏览器测试")
        cls.httpd = _make_server(ROOT / "site")
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.base = cls.origin + "/koreader/index.html"
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
        # **全部**请求都要记下来 —— 只数被 route 拦的那一条是不够的：
        # 页面要是另发一个指向别处的 fetch，那条请求根本不会进 route。
        self.all_requests = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        self.page.on("console", lambda m: self.console_warnings.append(m.text)
                     if m.type == "warning" else None)
        self.page.on("request", lambda r: self.all_requests.append(r.url))
        # 注册表（sources.json）是页面唯一的网络请求 —— 这里把它拦下来，
        # 让每个用例自己决定「服务端给什么」「拉不到」还是「慢一点回来」。
        self.registry_response = None
        self.registry_delay = 0.0     # >0 ⇒ 人为把注册表响应延后多少秒

        def handle(route):
            self.registry_requests.append(route.request.url)
            if self.registry_response is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.registry_response))

        self.page.route("**/pokemon-dex/sources.json", handle)

    def tearDown(self):
        self.page.close()

    # -- 辅助 -----------------------------------------------------------

    def open_page(self, wire: str | None = CLEAN_WIRE, width: int = 390):
        """打开落地页并等它真的渲染完（github.io 慢，不固定 sleep）。"""
        self.page.set_viewport_size({"width": width, "height": 844})
        url = self.base + ("?days=" + quote_wire(wire) if wire is not None else "")
        self.page.goto(url, wait_until="commit")
        return url

    def wait_rendered(self):
        """等页面要么给出数字、要么给出「没读出有效时长」的提示。

        ⚠️ 这只说明「页面画完了」—— **不等于**「注册表生效了」：
        第一次渲染用的是默认口径（per=15），注册表是之后异步换上去的。
        要断言注册表口径，必须等 `wait_registry_applied()`。
        """
        self.page.wait_for_function(
            """() => {
              const bad = document.querySelector('#badBox');
              if (bad && !bad.hidden) return true;
              const n = document.querySelector('#creditNum');
              return !!n && n.textContent !== '—';
            }""",
            timeout=30000,
        )

    def wait_registry_applied(self):
        """等**注册表真的生效**（读到真注册表了，或者兜底口径生效了）。

        页面里那个 `state.registryLoaded` 标志就是为这件事准备的 ——
        它在注册表（或兜底）生效、并且**重新渲染完**之后才置 true。
        等它 ⇒ 页面上的数字一定是当前口径下的数字。
        """
        self.page.wait_for_function(
            "() => !!(window.KoreaderLanding && KoreaderLanding.state.registryLoaded)",
            timeout=30000,
        )

    def record_credit_history(self):
        """在页面里挂一个观察者，记下 #creditNum 的**每一次**变化。

        为什么要在页面里记：Python 线程不一定能在两次渲染之间插进去
        ⇒「第一次渲染（默认口径）」与「注册表生效后」两态都只能在页面里抓。
        """
        self.page.add_init_script("""
          window.__creditHistory = [];
          window.__pushCredit = function () {
            const el = document.querySelector('#creditNum');
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

    def credit_history(self) -> list:
        """页面记下来的额度数字序列（按出现的先后）。"""
        return self.page.evaluate("() => window.__creditHistory || []")

    def state_days(self) -> dict:
        """页面解出来的天数（直接问页面里那份代码）。"""
        return self.page.evaluate("() => KoreaderLanding.state.days")

    def credit_text(self) -> str:
        return self.page.inner_text("#creditNum")

    def day_rows(self) -> list[tuple[str, str, str]]:
        """页面上显示的每一行：(日期, 分钟, 额度)。"""
        return self.page.evaluate("""() => [...document.querySelectorAll('#dayList li')]
            .map(li => [li.querySelector('.d').textContent,
                         li.querySelector('.m').textContent,
                         li.querySelector('.c').textContent])""")

    # -- 用例 -----------------------------------------------------------

    def test_valid_days_show_the_dates_minutes_and_credits(self):
        """链接带合法 days ⇒ 页面显示正确的日期、分钟、合计与额度。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_rendered()
        rows = self.day_rows()
        self.assertEqual([r[0] for r in rows], ["2026-11-21", "2026-11-20"])
        self.assertEqual([r[1] for r in rows], ["25 分钟", "50 分钟"])
        self.assertEqual([r[2] for r in rows], ["1 个额度", "3 个额度"])
        self.assertEqual(self.page.inner_text("#totalMin"), "75 分钟")
        # 没超上限 ⇒ 不该有夹取的提示
        self.assertFalse(self.page.is_visible("#clampNote"))

    def test_the_credit_is_four_not_five(self):
        """**额度是 4 不是 5** —— 按天 floor 再相加，不跨天凑零头。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()
        self.assertEqual(self.credit_text(), "4")
        self.assertNotEqual(self.credit_text(), "5")

    def test_the_credit_comes_from_the_registry(self):
        """per 真的来自注册表 —— **两态都要看到**。

        页面**第一次**渲染用的是默认口径（per=15）⇒ 25//15 + 50//15 = 1 + 3 = 4；
        注册表（per=30）生效后要重算成 25//30 + 50//30 = 0 + 1 = 1。

        ⚠️ 这条用例以前是**靠时序侥幸通过的**：它等的是「页面画完了」，
        而第一次渲染就已经把 `#creditNum` 填成 "4" 了 ⇒ 注册表响应一慢，
        读到的就是默认口径的 "4"，测试变红；注册表响应快 ⇒ 读到 "1"，测试变绿。
        现在改成：**等 `state.registryLoaded`（注册表真的生效了）**，
        并且把「默认口径那一版」与「注册表口径那一版」都断言到 ——
        这样它证明的才是「per 是从注册表来的」，而不是「per 是默认值」。

        为了不靠运气，这里还**人为把注册表响应延后 400ms**：
        两次渲染之间隔着 400ms ⇒ 中间态一定抓得到（页面里的观察者记着每一次变化）。
        """
        self.registry_response = _registry(per=30)
        self.registry_delay = 0.4        # 人为把注册表响应延后
        self.delay_the_registry_in_the_browser(self.registry_delay)
        self.record_credit_history()      # 在页面里记下每一次额度变化
        self.open_page()
        self.wait_registry_applied()      # 等注册表**真的生效**（不是等页面画完）
        # 注册表口径：25 // 30 = 0，50 // 30 = 1 ⇒ 合计 1
        self.assertEqual(self.credit_text(), "1")
        self.assertEqual([r[2] for r in self.day_rows()], ["0 个额度", "1 个额度"])
        self.assertIn("30 分钟 = 1 个额度", self.page.inner_text("#perNote"))
        # 默认口径那一版（第一次渲染，per=15 ⇒ 1 + 3 = 4）也要在页面上出现过，
        # 而且**在**注册表口径那一版**之前** —— 这才证明「per 来自注册表」。
        history = self.credit_history()
        self.assertIn("4", history, "没看到默认口径（per=15）那一版：%r" % history)
        self.assertIn("1", history, "没看到注册表口径（per=30）那一版：%r" % history)
        self.assertLess(
            history.index("4"), history.index("1"),
            "两次渲染的先后顺序不对：%r" % history,
        )

    def test_the_page_falls_back_to_fifteen_when_the_registry_is_unreachable(self):
        """拉不到 sources.json ⇒ 退回默认的 15（**不能卡在「载入中…」**）。"""
        self.registry_response = None      # 拦下来直接 abort
        self.open_page()
        self.wait_registry_applied()        # 等兜底口径生效（注册表试过了）
        self.assertEqual(self.credit_text(), "4")
        self.assertIn("15 分钟 = 1 个额度", self.page.inner_text("#perNote"))
        # 失败原因不许被整个吞掉 —— 控制台里要看得见「为什么退回默认口径」
        self.assertTrue(
            any("注册表" in w for w in self.console_warnings),
            "注册表拉不到时应该有一条 warning：%r" % (self.console_warnings,),
        )

    def test_the_page_sends_exactly_one_network_request(self):
        """只许发一个网络请求：读 sources.json 拿 per。

        ⚠️ 以前只数了被 route 拦的那一条 ⇒ 页面要是另发一个指向别处的 fetch，
        那条请求根本不会进 route，测试照样绿。
        现在把**全部**请求都记下来：除了本地静态资源（html/css/js），
        只应该剩下那一条 sources.json。
        """
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()        # 等请求真的走完
        self.assertEqual(len(self.registry_requests), 1, str(self.registry_requests))
        others = [u for u in self.all_requests if not _is_local_static(u)]
        self.assertEqual(others, list(self.registry_requests),
                         "除了 sources.json 之外还有别的请求：%r" % (self.all_requests,))

    def test_bad_chunks_are_dropped_one_by_one(self):
        """坏数据（空、格式错、含空白、含 Unicode 数字、段尾带换行）⇒ 按规则整段丢掉。

        断言两样：**页面内部变量**与**页面上渲染出来的行** ——
        只看内部变量的话，renderDays 漏渲染/渲染错位这条用例不会红。
        """
        by_wire = {c["wire"]: c["decoded"] for c in _wire_cases()}
        for wire, expected in by_wire.items():
            with self.subTest(wire=wire):
                self.registry_response = _registry(per=15)
                self.open_page(wire)
                self.wait_rendered()
                self.assertEqual(self.state_days(), expected)
                if not expected:
                    # 一条都没解出来 ⇒ 友好提示，不是空白页
                    self.assertTrue(self.page.is_visible("#badBox"))
                    self.assertFalse(self.page.is_visible("#mainBox"))
                else:
                    # **渲染出来的行**也要对（日期倒序）
                    self.assertEqual(self.day_rows(), _expected_rows(expected))

    def test_no_valid_days_shows_a_friendly_hint(self):
        """没有有效日期 ⇒ 显示友好提示（不是空白页、也不是一个空的 0）。"""
        self.registry_response = _registry(per=15)
        self.open_page("abc")
        self.wait_rendered()
        bad = self.page.inner_text("#badBox")
        self.assertIn("没读出有效的时长", bad)
        self.assertIn("链接不完整", bad)
        self.assertFalse(self.page.is_visible("#mainBox"))
        # 不是空白页
        self.assertGreater(len(self.page.inner_text("body").strip()), 20)

    def test_a_missing_days_parameter_also_shows_the_hint(self):
        """链接里压根没有 days ⇒ 同样给提示。"""
        self.registry_response = _registry(per=15)
        self.open_page(wire=None)
        self.wait_rendered()
        self.assertTrue(self.page.is_visible("#badBox"))

    def test_the_button_points_at_a_valid_new_issue_url(self):
        """按钮的 href 必须是合法的 GitHub 新建 issue 链接（title/body 能解回正确的值）。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()
        url = self.page.get_attribute("#submitBtn", "href")
        self.assertTrue(
            url.startswith("https://github.com/lilia-shound/pokemon-dex/issues/new?"),
            url[:80],
        )
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["title"][0], "[report] koreader 2026-11-21")
        body = json.loads(q["body"][0])
        self.assertEqual(body.get("v"), 1)
        self.assertEqual(body.get("source"), "koreader")
        self.assertEqual(body.get("days"), CLEAN_DAYS)

    def test_the_minutes_are_clamped_to_the_daily_cap_before_submitting(self):
        """单日超上限的分钟数要**夹到 maxPerDay** 再提交。

        服务端 `scripts/collect_reports.py` 的规则是「任一天超过 maxPerDay
        ⇒ **整份报告丢弃**」⇒ 页面要是原样报上去，用户会以为交上去了，其实被丢了。
        所以落地页在编 body 时夹住，并且要在页面上说清楚夹了多少。
        """
        self.registry_response = _registry(per=15, max_per_day=1440)
        self.open_page("2026-11-21:2000")
        self.wait_registry_applied()
        # 页面上有提示（原始值与上限都在）
        note = self.page.inner_text("#clampNote")
        self.assertIn("2026-11-21", note)
        self.assertIn("2000", note)
        self.assertIn("1440", note)
        # 提交给服务端的是夹过的值
        url = self.page.get_attribute("#submitBtn", "href")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        body = json.loads(q["body"][0])
        self.assertEqual(body.get("days"), {"2026-11-21": 1440})
        # 而且服务端认这份报告（没被丢掉）
        cfg = src.load_config(SOURCES_JSON)
        days, err = cr.parse_report(q["body"][0], cfg, "2026-11-21")
        self.assertIsNone(err, "服务端不认这个 body：%s" % err)
        self.assertEqual(days, {"2026-11-21": 1440})
        # 页面显示的分钟数与提交的一致（1440），额度也按 1440 算
        self.assertEqual([r[1] for r in self.day_rows()], ["1440 分钟"])
        self.assertEqual([r[2] for r in self.day_rows()], ["96 个额度"])

    def test_the_issue_title_shown_on_the_page_matches_the_link(self):
        """页面上写着的标题要跟链接里那个一模一样（别写死一个日期）。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()
        url = self.page.get_attribute("#submitBtn", "href")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(self.page.inner_text("#issueTitle"), q["title"][0])

    def test_the_issue_body_is_accepted_by_the_server_parser(self):
        """**端到端**：页面生成的 body，服务端的 parse_report 要认。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()
        url = self.page.get_attribute("#submitBtn", "href")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        cfg = src.load_config(SOURCES_JSON)
        days, err = cr.parse_report(q["body"][0], cfg, "2026-11-21")
        self.assertIsNone(err, "服务端不认这个 body：%s" % err)
        self.assertEqual(days, CLEAN_DAYS)

    def test_the_page_does_not_overflow_horizontally_on_a_phone(self):
        """手机宽度下不许横向溢出。"""
        self.registry_response = _registry(per=15)
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
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_registry_applied()
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])


# -- 小工具 -------------------------------------------------------------


def _registry(per: int, max_per_day: int = 1440) -> dict:
    """给页面看的一份 sources.json（只放页面要用的那几个字段）。"""
    return {
        "version": 1,
        "perAward": 2,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "koreader": {
                "name": "KOReader",
                "icon": "📚",
                "unit": "分钟",
                "per": per,
                "mode": "push",
                "enabled": True,
                "maxPerDay": max_per_day,
            }
        },
    }


def _expected_rows(days: dict, per: int = 15) -> list[list[str]]:
    """页面上**应该**显示出来的行（日期倒序）：[日期, 分钟, 额度]。

    额度按天 floor（不跨天凑零头）—— 与服务端 scripts/sources.py 的口径一致。
    """
    return [[d, "%d 分钟" % m, "%d 个额度" % (m // per)]
            for d, m in sorted(days.items(), reverse=True)]


def _is_local_static(url: str) -> bool:
    """这个请求是不是本地静态服务器上的静态资源（html/css/js）。

    落地页除了这三个文件，不许再要别的东西 —— 别的都算「多余的网络请求」。
    """
    return bool(re.match(
        r"^http://127\.0\.0\.1:\d+/koreader/(index\.html|style\.css|app\.js)(\?.*)?$",
        url,
    ))


def _wire_cases() -> list[dict]:
    """线格式的用例（解码结果）—— 从 JS 对拍脚本那里拿（同一份代码、同一份用例）。"""
    return run_js_spec()["cases"]


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
