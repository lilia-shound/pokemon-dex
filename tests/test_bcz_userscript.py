"""百词斩油猴脚本（`plugin/bcz-dex.user.js`）的测试。

两件事：

1. **纯逻辑**（「认数」的那部分）—— 沙箱里拿不到真实百词斩页面
   （学习界面在登录后面，域名之外的路径都是 404 ⇒ 拿不到它的 DOM/文案），
   所以用**构造的页面文字**喂给脚本里拆出来的纯函数。
   纯函数跑在 node 里（脚本是**一个文件**，装起来方便 ⇒ 不能拆成多个文件 import，
   于是这里直接 require 整个脚本，让它在 node 下只跑纯逻辑那一段）。
2. **真实浏览器里的观感断言**（Playwright）—— 在构造的页面上注入脚本，
   断言浮层出现、认出来的数对、「打开记数页」的链接对、没认出来时是空的。

跑法：

    python3 -B run_tests.py                          # 整套
    python3 -B -m tests.test_bcz_userscript          # 只跑这个文件（仓库根目录下跑）
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugin" / "bcz-dex.user.js"
RUNNER = ROOT / "tests" / "bcz_userscript_runner.js"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

LANDING = "https://lilia-shound.github.io/pokemon-dex/words/"

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False


def find_node() -> str | None:
    """找 node。PATH 里没有就看看常见安装位置。"""
    exe = shutil.which("node")
    if exe:
        return exe
    for cand in ("/usr/local/bin/node", "/usr/bin/node", "/opt/homebrew/bin/node"):
        if pathlib.Path(cand).exists():
            return cand
    return None


class BczParseTest(unittest.TestCase):
    """脚本里「认数」的纯逻辑（用构造的页面文字喂它）。

    **最要紧的一条**：百词斩 App 左上角的「需新学 15 / 需复习 1」是**剩余量**，
    不是今天的成果 ⇒ 脚本必须不产出数字（宁可让她手填）。
    """

    @classmethod
    def setUpClass(cls):
        cls.node = find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过油猴脚本的纯逻辑测试")
        if not SCRIPT.exists():
            raise unittest.SkipTest("plugin/bcz-dex.user.js 不存在")
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def run_cases(self, cases: list) -> list:
        """把一批用例丢给 node 跑测器，返回结果列表。"""
        proc = subprocess.run(
            [self.node, str(RUNNER)],
            input=json.dumps({"cases": cases}, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, "跑测器挂了：%s" % proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertNotIn("error", payload, str(payload))
        return payload["results"]

    def parse_text(self, text: str):
        """只跑一条「页面文字」用例。"""
        return self.run_cases([{"op": "text", "text": text}])[0]

    # -- 任务书点名要的几个用例 -------------------------------------------

    def test_today_learning_label_is_read(self):
        """「今日学习 137 词」⇒ 137。"""
        r = self.parse_text("今日学习 137 词")
        self.assertIsNotNone(r, "「今日学习 137 词」没认出来")
        self.assertEqual(r["value"], 137)

    def test_the_remaining_counts_are_never_counted_as_a_result(self):
        """「需新学 15 需复习 1」⇒ **不产出数字**。

        这是本任务最容易犯的错：那是**还要背多少**，不是今天背了多少。
        """
        r = self.parse_text("需新学 15 需复习 1")
        self.assertIsNone(r, "把剩余量当成了今天的成果：%r" % (r,))

    def test_new_words_plus_review_words_are_added_up(self):
        """「新学 30 复习 107」⇒ 137（口径 = 新学 + 复习）。"""
        r = self.parse_text("新学 30 复习 107")
        self.assertIsNotNone(r, "「新学 30 复习 107」没认出来")
        self.assertEqual(r["value"], 137)
        self.assertEqual(r["kind"], "sum")

    def test_nothing_recognizable_gives_nothing(self):
        """什么都没有 ⇒ 空（**不许瞎猜一个数**）。"""
        self.assertIsNone(self.parse_text("今天天气不错，适合背单词"))
        self.assertIsNone(self.parse_text(""))
        self.assertIsNone(self.parse_text("   "))

    def test_the_explicit_label_wins_over_the_remaining_one(self):
        """多个候选同时出现 ⇒ 取「今日 / 已学 / 完成」那类语义明确的。"""
        r = self.parse_text("今日学习 137 词 需新学 15")
        self.assertIsNotNone(r)
        self.assertEqual(r["value"], 137)

    # -- 其余该钉住的 ---------------------------------------------------

    def test_the_other_keyword_shapes(self):
        """其余几种写法也要认得出来。"""
        cases = {
            "今日新学 30": 30,
            "已学 88": 88,
            "学习量 137": 137,
            "今日单词 42": 42,
            "完成 56": 56,
            "今天已背 64": 64,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                r = self.parse_text(text)
                self.assertIsNotNone(r, "「%s」没认出来" % text)
                self.assertEqual(r["value"], want)

    def test_the_remaining_words_are_excluded_wherever_they_appear(self):
        """排除清单里的标签，出现在任何位置都不算数。"""
        for text in ("还剩 20 个", "剩余 20", "待复习 30", "未学习 40", "需新学 15",
                     "今日学习 137 词 需新学 15 还剩 3"):
            with self.subTest(text=text):
                r = self.parse_text(text)
                if text.startswith("今日学习"):
                    self.assertEqual(r["value"], 137)
                else:
                    self.assertIsNone(r, "把「%s」里的剩余量当成了成果：%r" % (text, r))

    def test_a_lifetime_total_is_not_mistaken_for_a_daily_count(self):
        """累计总量（几千词）不许被当成今天背的 ⇒ 当没认出来。"""
        r = self.parse_text("学习量 3200")
        self.assertIsNone(r, "累计总量被当成了今天的量：%r" % (r,))

    def test_only_half_of_the_caliber_is_marked_as_partial(self):
        """只读到「新学 30」（没找到复习数）⇒ 给出数，但标明是半个口径。"""
        r = self.parse_text("今日新学 30")
        self.assertIsNotNone(r)
        self.assertEqual(r["value"], 30)
        self.assertEqual(r["kind"], "partial")

    # -- 接口兜底 ---------------------------------------------------------

    def parse_payload(self, payload, today="2026-11-22"):
        return self.run_cases([{"op": "payload", "payload": payload, "today": today}])[0]

    def test_a_today_scoped_payload_is_read(self):
        """接口里明确是「今天」的节点 ⇒ 认得出来。"""
        r = self.parse_payload({"data": {"today": {"wordCount": 137}}})
        self.assertIsNotNone(r, "接口兜底没认出来")
        self.assertEqual(r["value"], 137)

    def test_new_and_review_counts_in_a_payload_are_added_up(self):
        """接口里的 新学 + 复习 ⇒ 相加。"""
        r = self.parse_payload({"data": {"today": {"newWordCount": 30, "reviewCount": 107}}})
        self.assertIsNotNone(r)
        self.assertEqual(r["value"], 137)

    def test_the_ambiguous_keys_are_not_used_outside_a_today_scope(self):
        """语义不明确的键（newWordCount 之类）不在「今天」的节点里 ⇒ **不用**。

        百词斩 App 左上角那两个数很可能就叫 newWordCount / reviewCount
        ⇒ 不加限制就会把「还需背多少」当成今天的成果。
        """
        r = self.parse_payload({"data": {"todo": {"newWordCount": 15, "reviewCount": 1}}})
        self.assertIsNone(r, "把剩余量当成了今天的成果：%r" % (r,))

    def test_a_payload_with_nothing_usable_gives_nothing(self):
        """接口里没有可信的键 ⇒ 空（不猜）。"""
        self.assertIsNone(self.parse_payload({"code": 0, "data": {"list": [1, 2, 3]}}))

    def test_a_dated_node_counts_as_today(self):
        """节点里带今天的日期 ⇒ 也算「今天」。"""
        r = self.parse_payload({"data": {"stat": {"date": "2026-11-22", "wordCount": 137}}})
        self.assertIsNotNone(r)
        self.assertEqual(r["value"], 137)

    # -- 记数页链接 -------------------------------------------------------

    def test_the_landing_url_carries_the_number(self):
        """「打开记数页」的链接要带上 ?words=N。"""
        url = self.run_cases([{"op": "url", "n": 137}])[0]
        self.assertEqual(url, LANDING + "?words=137")

    # -- 脚本本身的硬要求 -------------------------------------------------

    def test_the_metadata_block_has_the_usual_fields(self):
        """头部元信息要齐（版本号 / @match / @updateURL / @downloadURL）。"""
        for needle in ("// ==UserScript==", "// @version      1.0.0",
                       "// @match        https://www.baicizhan.com/*",
                       "// @updateURL", "// @downloadURL", "// @grant        none"):
            self.assertIn(needle, self.source, "头部少了：%s" % needle)
        self.assertIn("https://lilia-shound.github.io/pokemon-dex/bcz-dex.user.js", self.source)

    def test_the_script_touches_no_credentials(self):
        """不许读也不许发任何 Cookie / 凭证 —— 源码里连**用**都不许用。

        注意：查的是**代码里的用法**（``document.cookie`` 这种），
        不是查有没有出现「cookie」这个词（注释里说明「不读 Cookie」是允许的）。
        """
        low = self.source.lower()
        for banned in ("document.cookie", "cookiestore", "setcookie(", "localstorage",
                       "sessionstorage", "sendbeacon", "gm_xmlhttprequest",
                       "navigator.credentials", "authorization:"):
            self.assertNotIn(banned, low, "脚本里用了「%s」" % banned)

    def test_the_script_pulls_in_nothing_from_the_internet(self):
        """零外部依赖：不引 CDN、不引第三方库、不引任何外部脚本。"""
        low = self.source.lower()
        self.assertNotIn("@require", low, "不许 @require 任何外部脚本")
        self.assertNotIn("<script src", low)
        self.assertNotIn("import(", low)
        # 除了自己站上的地址，不许有别的外链
        # （头部元信息里的 @match 是「脚本在哪个站上跑」，不算外链 ⇒ 先摘掉头部）
        body = self.source.split("// ==/UserScript==", 1)[-1]
        urls = [u for u in re.findall(r"https?://[^\s'\"]+", body)
                if not u.startswith("https://lilia-shound.github.io/pokemon-dex/")]
        self.assertEqual(urls, [], "脚本里出现了意料之外的外链：%r" % (urls,))

    def test_the_script_never_submits_by_itself(self):
        """**绝不自动提交** —— 脚本里不许有提交/发请求的动作。"""
        for banned in ("method=\"post\"", "method='post'", ".submit(", "form.submit"):
            self.assertNotIn(banned, self.source.lower(), "脚本里有自动提交的动作：%s" % banned)


class BczUserscriptBrowserTest(unittest.TestCase):
    """真实浏览器（Playwright）里注入脚本后的观感断言。

    沙箱里拿不到真实百词斩页面 ⇒ 这里用**构造的页面**（假 URL + 假 HTML）。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过油猴脚本的浏览器测试")
        if not (shutil.which("chromium") or pathlib.Path(CHROME).exists()):
            raise unittest.SkipTest("找不到 chromium，跳过油猴脚本的浏览器测试")
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        self.page_errors = []
        self.requests = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.add_init_script(self.source)

    def tearDown(self):
        self.page.close()

    # -- 辅助 -------------------------------------------------------------

    def open_fake_page(self, body_html: str, api_body: str | None = None):
        """打开一个构造出来的「百词斩页面」。

        - ``body_html``  —— 页面正文（放中文标签与数字）；
        - ``api_body``   —— 页面自己会 fetch 的接口（JSON 字符串）。
        """
        script = ""
        if api_body is not None:
            script = ("<script>fetch('/api/study').then(function(r){return r.text()});"
                      "var x=new XMLHttpRequest();x.open('GET','/api/study2');x.send();</script>")
        html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<title>假的百词斩</title></head><body><div id='fake'>" + body_html +
                "</div>" + script + "</body></html>")

        def handler(route):
            url = route.request.url
            if "/api/study" in url:
                route.fulfill(status=200, content_type="application/json",
                              body=api_body if api_body is not None else "{}")
            else:
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        self.page.route("**/*", handler)
        self.page.goto("https://www.baicizhan.com/fake-page", wait_until="commit")

    def wait_panel(self):
        """等浮层真的画出来。"""
        self.page.wait_for_selector("#bcz-panel", timeout=15000)

    def count_value(self) -> str:
        return self.page.input_value("#bcz-count")

    def open_href(self) -> str:
        return self.page.get_attribute("#bcz-open", "href")

    def source_text(self) -> str:
        return self.page.inner_text("#bcz-src")

    # -- 用例 -------------------------------------------------------------

    def test_the_panel_shows_the_number_it_read_from_the_page(self):
        """页面写着「今日学习 137 词」⇒ 浮层里填 137，链接也带 137。"""
        self.open_fake_page("<div>今日学习 137 词</div>")
        self.wait_panel()
        self.assertEqual(self.count_value(), "137")
        self.assertEqual(self.open_href(), LANDING + "?words=137")
        self.assertIn("页面文字", self.source_text())

    def test_the_panel_refuses_to_count_the_remaining_words(self):
        """「需新学 15 需复习 1」⇒ 输入框是空的，并说明没认出来。"""
        self.open_fake_page("<div>需新学 15 需复习 1</div>")
        self.wait_panel()
        self.assertEqual(self.count_value(), "")
        self.assertIn("没认出来", self.source_text())
        # 没有数 ⇒ 不给一个能点的链接
        self.assertEqual(self.open_href(), "#")

    def test_new_and_review_are_added_up_in_the_browser_too(self):
        """「新学 30 复习 107」⇒ 137。"""
        self.open_fake_page("<div>新学 30 复习 107</div>")
        self.wait_panel()
        self.assertEqual(self.count_value(), "137")
        self.assertEqual(self.open_href(), LANDING + "?words=137")

    def test_nothing_is_guessed_when_the_page_says_nothing(self):
        """页面没有可认的数 ⇒ 空输入框 + 一句提示（**不瞎猜**）。"""
        self.open_fake_page("<div>今天天气不错</div>")
        self.wait_panel()
        self.assertEqual(self.count_value(), "")
        self.assertIn("手动填", self.source_text())

    def test_the_number_can_be_typed_by_hand(self):
        """手动填也能走通：改了输入框 ⇒ 来源标成「手填」，链接跟着变。"""
        self.open_fake_page("<div>今天天气不错</div>")
        self.wait_panel()
        self.page.fill("#bcz-count", "88")
        self.page.wait_for_function(
            "() => document.querySelector('#bcz-src').textContent.indexOf('手填') >= 0",
            timeout=10000,
        )
        self.assertEqual(self.open_href(), LANDING + "?words=88")
        self.assertIn("手填", self.source_text())

    def test_the_number_is_read_from_the_api_when_the_page_has_none(self):
        """页面文字里没有 ⇒ 走接口兜底（来源写明「从接口读到」）。"""
        api = json.dumps({"data": {"today": {"wordCount": 137}}})
        self.open_fake_page("<div>背单词</div>", api_body=api)
        self.wait_panel()
        self.page.wait_for_function(
            "() => document.querySelector('#bcz-count').value === '137'", timeout=15000)
        self.assertEqual(self.count_value(), "137")
        self.assertIn("接口", self.source_text())
        self.assertEqual(self.open_href(), LANDING + "?words=137")

    def test_the_page_text_wins_over_the_api(self):
        """两边都有 ⇒ 页面文字赢（它更稳）。"""
        api = json.dumps({"data": {"today": {"wordCount": 999}}})
        self.open_fake_page("<div>今日学习 137 词</div>", api_body=api)
        self.wait_panel()
        self.page.wait_for_timeout(600)
        self.assertEqual(self.count_value(), "137")
        self.assertIn("页面文字", self.source_text())

    def test_a_number_she_typed_is_not_overwritten_by_a_later_page_update(self):
        """她自己填的数，页面后来变了也不许被盖掉。"""
        self.open_fake_page("今日学习 137 词")
        self.wait_panel()
        self.page.fill("#bcz-count", "88")
        self.page.wait_for_function(
            "() => document.querySelector('#bcz-src').textContent.indexOf('手填') >= 0",
            timeout=10000,
        )
        # 页面上的数字变了（比如她背完词，页面刷新了统计）
        self.page.evaluate(
            "() => { document.querySelector('#fake').textContent = '今日学习 200 词'; }")
        self.page.wait_for_timeout(900)
        self.assertEqual(self.count_value(), "88")
        self.assertEqual(self.open_href(), LANDING + "?words=88")

    def test_the_script_does_not_submit_anything_by_itself(self):
        """脚本**只带数** ⇒ 不会自己发任何写请求。"""
        self.open_fake_page("<div>今日学习 137 词</div>")
        self.wait_panel()
        self.page.wait_for_timeout(500)
        # 只允许页面自己请求的假 URL；不许有 POST、也不许有 github 的提交链接请求
        for url in self.requests:
            self.assertNotIn("github.com", url)
            self.assertNotIn("issues/new", url)
        self.assertEqual(self.page_errors, [])

    def test_the_panel_is_small_and_can_be_closed(self):
        """浮层要小、能关。"""
        self.open_fake_page("<div>今日学习 137 词</div>")
        self.wait_panel()
        box = self.page.locator("#bcz-panel").bounding_box()
        self.assertIsNotNone(box)
        self.assertLess(box["width"], 340, "浮层太宽了：%r" % (box,))
        # 收起来 ⇒ 只剩一个小胶囊
        self.page.click('#bcz-panel [data-act="collapse"]')
        self.page.wait_for_selector("#bcz-fab:not([hidden])", timeout=5000)
        fab = self.page.locator("#bcz-fab").bounding_box()
        self.assertLess(fab["width"], 200)
        # 藏起来 ⇒ 什么都不剩
        self.page.click("#bcz-fab")
        self.page.click('#bcz-panel [data-act="hide"]')
        self.page.wait_for_function(
            "() => document.querySelector('#bcz-panel').hidden === true", timeout=5000)
        self.assertTrue(self.page.locator("#bcz-fab").is_hidden())


if __name__ == "__main__":
    unittest.main()
