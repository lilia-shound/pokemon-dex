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
SPEC_JS = ROOT / "tests" / "koreader_landing_spec.js"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

# 干净的那条线（fixture 的第一行）—— 页面上就是它
CLEAN_WIRE = "2026-11-21:25,2026-11-20:50"
CLEAN_DAYS = {"2026-11-21": 25, "2026-11-20": 50}

# 断言集至少要跑这么多条（防止用例文件被清空 ⇒ 一片绿但什么都没测）
MIN_WIRE_CASES = 20

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False


def quote_wire(wire: str) -> str:
    """把天数串安全地塞进 query string（`:` `,` 空格、Unicode 数字都要能原样还原）。"""
    return urllib.parse.quote(wire, safe="")


class KoreaderLandingWireTest(unittest.TestCase):
    """JS 侧的线格式解析 vs Python 侧的参考实现 —— 逐例对拍。"""

    @classmethod
    def setUpClass(cls):
        cls.node = bridge.find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过落地页的 JS 对拍")
        cls.proc = subprocess.run(
            [cls.node, str(SPEC_JS)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        if cls.proc.returncode != 0:
            raise AssertionError(
                "JS 对拍脚本没跑通（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
                % (cls.proc.returncode, cls.proc.stdout, cls.proc.stderr)
            )
        payload = None
        for line in cls.proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                payload = json.loads(line)
        if payload is None:
            raise AssertionError("JS 对拍脚本没有输出 JSON：\n" + cls.proc.stdout)
        cls.payload = payload
        cls.cases = payload["cases"]

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
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:src|href)\s*=\s*"([^"]+)"', html):
            url = m.group(1)
            self.assertFalse(
                url.startswith("http://") or url.startswith("https://"),
                "落地页引了外链：%s" % url,
            )
        app = APP_JS.read_text(encoding="utf-8")
        allowed_prefix = (
            "https://lilia-shound.github.io/pokemon-dex/",
            "https://github.com/",
        )
        for url in re.findall(r"https?://[^\s'\"]+", app):
            self.assertTrue(
                url.startswith(allowed_prefix),
                "app.js 里出现了意料之外的外链：%s" % url,
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
        cls.base = "http://127.0.0.1:%d/koreader/index.html" % cls.httpd.server_address[1]
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
        self.registry_requests = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        # 注册表（sources.json）是页面唯一的网络请求 —— 这里把它拦下来，
        # 让每个用例自己决定「服务端给什么」还是「拉不到」。
        self.registry_response = None

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
        """等页面要么给出数字、要么给出「没读出有效时长」的提示。"""
        self.page.wait_for_function(
            """() => {
              const bad = document.querySelector('#badBox');
              if (bad && !bad.hidden) return true;
              const n = document.querySelector('#creditNum');
              return !!n && n.textContent !== '—';
            }""",
            timeout=30000,
        )

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

    def test_the_credit_is_four_not_five(self):
        """**额度是 4 不是 5** —— 按天 floor 再相加，不跨天凑零头。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_rendered()
        self.assertEqual(self.credit_text(), "4")
        self.assertNotEqual(self.credit_text(), "5")

    def test_the_credit_comes_from_the_registry(self):
        """per 从 sources.json 读 —— 换成 30 分钟 1 个额度，数字要跟着变。"""
        self.registry_response = _registry(per=30)
        self.open_page()
        self.wait_rendered()
        # 25 // 30 = 0，50 // 30 = 1 ⇒ 合计 1
        self.assertEqual(self.credit_text(), "1")
        self.assertEqual([r[2] for r in self.day_rows()], ["0 个额度", "1 个额度"])

    def test_the_page_falls_back_to_fifteen_when_the_registry_is_unreachable(self):
        """拉不到 sources.json ⇒ 退回默认的 15（**不能卡在「载入中…」**）。"""
        self.registry_response = None      # 拦下来直接 abort
        self.open_page()
        self.wait_rendered()
        self.assertEqual(self.credit_text(), "4")
        self.assertIn("15 分钟 = 1 个额度", self.page.inner_text("#perNote"))

    def test_the_page_sends_exactly_one_network_request(self):
        """只许发一个网络请求：读 sources.json 拿 per。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_rendered()
        self.assertEqual(len(self.registry_requests), 1, str(self.registry_requests))

    def test_bad_chunks_are_dropped_one_by_one(self):
        """坏数据（空、格式错、含空格、含 Unicode 数字）⇒ 按规则整段丢掉。"""
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
        self.wait_rendered()
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

    def test_the_issue_title_shown_on_the_page_matches_the_link(self):
        """页面上写着的标题要跟链接里那个一模一样（别写死一个日期）。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_rendered()
        url = self.page.get_attribute("#submitBtn", "href")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(self.page.inner_text("#issueTitle"), q["title"][0])

    def test_the_issue_body_is_accepted_by_the_server_parser(self):
        """**端到端**：页面生成的 body，服务端的 parse_report 要认。"""
        self.registry_response = _registry(per=15)
        self.open_page()
        self.wait_rendered()
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
        self.wait_rendered()
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])


# -- 小工具 -------------------------------------------------------------


def _registry(per: int) -> dict:
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
                "maxPerDay": 1440,
            }
        },
    }


def _wire_cases() -> list[dict]:
    """线格式的用例（解码结果）—— 从 JS 对拍脚本那里拿（同一份代码、同一份用例）。"""
    node = bridge.find_node()
    if not node:
        raise unittest.SkipTest("找不到 node")
    proc = subprocess.run(
        [node, str(SPEC_JS)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError("JS 对拍脚本没跑通：\n" + proc.stdout + "\n" + proc.stderr)
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)["cases"]
    raise AssertionError("JS 对拍脚本没有输出 JSON")


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
