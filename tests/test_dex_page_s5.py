"""图鉴页（S5：多源额度 + 形态 + 链级养成）的测试。

三件事：

1. **额度口径的跨语言对拍** —— 页面算出来的额度要跟服务端
   （`scripts/sources.py` 的 `credit_for_day` / `credits_for_days`）**一模一样**：
   按天 floor 再相加，零头不跨天凑；多算一个额度就是「她明明凑够了，
   页面却说没够」。
2. **图鉴墙按形态显示** —— `collected[].form` 决定显示哪张图、哪个名字；
   老数据没有 `form` 时回退到 `id`。
3. **真实浏览器里的观感断言**（Playwright）—— 四个源各一行、没量的源不隐藏、
   阈值文案是额度语义、链级养成的天数钉死、注册表拉不到时不崩、零外链、
   手机宽度下不横向溢出。

跑法：

    python3 -B run_tests.py                          # 整套
    python3 -B -m tests.test_dex_page_s5             # 只跑这个文件（仓库根目录下跑）
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

from scripts import sources as src

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES_JSON = ROOT / "data" / "sources.json"
SITE_REGISTRY = ROOT / "site" / "sources.json"
STATE_JSON = ROOT / "data" / "dex.json"
APP_JS = ROOT / "site" / "app.js"
INDEX_HTML = ROOT / "site" / "index.html"
STYLE_CSS = ROOT / "site" / "style.css"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False


# ── 测试用的假图鉴（只放几只，够测形态回退就行） ─────────────────────────
FAKE_POKEDEX = {
    "count": 4,
    "pokemon": [
        {
            "id": "0001", "nameZh": "妙蛙种子", "nameJa": "フシギダネ",
            "types": ["草", "毒"], "category": "种子宝可梦", "abilities": ["茂盛"],
            "height": "0.7m", "weight": "6.9kg",
            "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"],
            "flavor": "从出生的时候开始。", "art": "0001.webp", "artShiny": "0001-shiny.webp",
        },
        {
            "id": "0003", "nameZh": "妙蛙花", "nameJa": "フシギダネ",
            "types": ["草", "毒"], "category": "种子宝可梦", "abilities": ["叶绿素"],
            "height": "2.0m", "weight": "100.0kg",
            "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"],
            "flavor": "花朵散发着迷人的香味。", "art": "0003.webp", "artShiny": "0003-shiny.webp",
        },
        {
            "id": "0004", "nameZh": "小火龙", "nameJa": "ヒトカゲ",
            "types": ["火"], "category": "蜥蜴宝可梦", "abilities": [" blaze"],
            "height": "0.6m", "weight": "8.5kg",
            "evolution": ["小火龙", "火恐龙", "喷火龙"],
            "flavor": "生下来的时候尾巴上就有火。", "art": "0004.webp", "artShiny": "0004-shiny.webp",
        },
        {
            "id": "0471", "nameZh": "冰伊布", "nameJa": " glaciers ",
            "types": ["冰"], "category": "柔软宝可梦", "abilities": ["雪隐"],
            "height": "0.3m", "weight": "6.6kg",
            "evolution": ["伊布", "冰伊布"],
            "flavor": "冰伊布是伊布的进化形态。", "art": "0471.webp", "artShiny": "0471-shiny.webp",
        },
    ],
}


def _registry(per_award: int = 2, enabled_words: bool = True) -> dict:
    """给页面看的一份 sources.json（四个源；口径与服务端那份一致）。"""
    return {
        "version": 1,
        "perAward": per_award,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {
                "name": "微信读书", "icon": "📖", "unit": "秒", "per": 900,
                "mode": "pull", "enabled": True, "maxPerDay": 86400,
            },
            "pom": {
                "name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                "mode": "push", "enabled": True, "maxPerDay": 1440,
            },
            "words": {
                "name": "背单词", "icon": "🔤", "unit": "个", "per": 100,
                "mode": "push", "enabled": enabled_words, "maxPerDay": 500,
            },
            "koreader": {
                "name": "KOReader", "icon": "📚", "unit": "分钟", "per": 15,
                "mode": "push", "enabled": True, "maxPerDay": 1440,
            },
        },
    }


def _state(today_sources=None, date="2026-11-22", collected=None, chains=None,
           credit=2, seconds=1753, threshold=1800, streak=3) -> dict:
    """一份状态文件（v2 的形状，字段一个都不少）。"""
    return {
        "version": 2,
        "collected": collected if collected is not None else [],
        "streak": streak,
        "lastAwardDate": date,
        "today": {
            "date": date,
            "seconds": seconds,
            "threshold": threshold,
            "sources": today_sources if today_sources is not None else {},
            "credit": credit,
            "perAward": 2,
        },
        "ledger": {},
        "chains": chains if chains is not None else {},
    }


class DexRegistryTest(unittest.TestCase):
    """口径的根：页面读的那份注册表不许和服务端算账用的那份漂移。

    页面读的是**站根**那份（`site/sources.json`）—— 它是 `scripts/deploy.sh`
    从 `data/sources.json` 自动同步出来的**部署产物**。
    漂了 ⇒ 页面算的额度和服务端给的对不上（「明明凑够了却说她没够」）。
    """

    def test_the_site_registry_matches_the_server_side_registry(self):
        """`site/sources.json` 与 `data/sources.json` **整份**相等。"""
        self.assertTrue(SITE_REGISTRY.exists(), "site/sources.json 不存在")
        server = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
        site = json.loads(SITE_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(site, server, "site/sources.json 与 data/sources.json 不一致")

    def test_the_registry_knows_all_four_sources(self):
        """四个源都在，且都是启用状态（页面要列出四个源各一行）。"""
        cfg = src.load_config(SOURCES_JSON)
        for sid in ("weread", "pom", "words", "koreader"):
            self.assertIn(sid, cfg["sources"], "注册表里少了 %s" % sid)
            self.assertTrue(cfg["sources"][sid]["enabled"], "%s 不是启用状态" % sid)


class DexStateFieldsTest(unittest.TestCase):
    """状态文件的字段一个都不许删 —— 微信读书插件还在读它们。"""

    def test_the_state_file_still_has_every_field_the_readers_need(self):
        """`dex.json`（v2）的顶层字段与 today / collected 里的字段都在。"""
        state = json.loads(STATE_JSON.read_text(encoding="utf-8"))
        for key in ("version", "collected", "streak", "lastAwardDate",
                    "today", "ledger", "chains"):
            self.assertIn(key, state, "dex.json 少了顶层字段 %s" % key)
        for key in ("date", "seconds", "threshold", "sources", "credit", "perAward"):
            self.assertIn(key, state["today"], "dex.json 的 today 少了 %s" % key)
        for c in state["collected"]:
            for key in ("id", "nameZh", "shiny", "at", "date", "sources", "form"):
                self.assertIn(key, c, "collected 里的一条少了 %s" % key)


class DexPageCreditTest(unittest.TestCase):
    """真实浏览器（Playwright）里的图鉴页断言。

    浏览器自己起一个本地静态服务器（site/ 目录）—— 不依赖外网，
    也不占用固定端口（免得跟别的测试撞车）。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过图鉴页的浏览器测试")
        if not (shutil.which("chromium") or pathlib.Path(CHROME).exists()):
            raise unittest.SkipTest("找不到 chromium，跳过图鉴页的浏览器测试")
        cls.httpd = _make_server(ROOT / "site")
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.base = cls.origin + "/index.html"
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
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        # 这三份数据由每个用例自己决定（注册表拉不到 / 状态是旧的 / 形态不一样）
        self.registry_response = _registry()
        self.state_response = _state(today_sources={"weread": 1753, "pom": 25})
        self.pokedex_response = FAKE_POKEDEX

        def registry_handler(route):
            if self.registry_response is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.registry_response))

        def state_handler(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(self.state_response))

        def pokedex_handler(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(self.pokedex_response))

        self.page.route("**/sources.json", registry_handler)
        self.page.route("**/dex.json", state_handler)
        self.page.route("**/pokedex.json", pokedex_handler)

    def tearDown(self):
        self.page.close()

    # -- 辅助 -----------------------------------------------------------

    def open_page(self, query: str = "", width: int = 390):
        """打开图鉴页并等它真的渲染完（github.io 慢，不固定 sleep）。"""
        self.page.set_viewport_size({"width": width, "height": 844})
        self.page.goto(self.base + (("?" + query) if query else ""),
                       wait_until="commit")
        return self.page.url

    def wait_rendered(self):
        """等额度面板与图鉴墙都画出来（**不等于**注册表生效了）。"""
        self.page.wait_for_function(
            """() => {
              const panel = document.querySelector('#creditPanel');
              const cells = document.querySelectorAll('#grid .cell');
              return !!panel && !panel.hidden && cells.length > 0;
            }""",
            timeout=30000,
        )

    def rows(self) -> dict:
        """面板里每一行：源 id → 那一行的文字。"""
        out = {}
        for el in self.page.query_selector_all("#cpRows .cpRow"):
            out[el.get_attribute("data-src")] = " ".join(el.inner_text().split())
        return out

    def chain_rows(self) -> dict:
        """链级养成每一行：链名 → 那一行的文字。"""
        out = {}
        for el in self.page.query_selector_all("#chainList .chainRow"):
            out[el.get_attribute("data-chain")] = " ".join(el.inner_text().split())
        return out

    def bar_width(self, selector: str) -> str:
        return self.page.eval_on_selector(selector, "el => el.style.width")

    def open_sheet(self, cell_id: str):
        """点开某只宝可梦的详情浮层，等它真的弹出来。"""
        self.page.click('.cell[data-id="%s"]' % cell_id)
        self.page.wait_for_function(
            "() => { const el = document.querySelector('#sheet'); return !!el && !el.hidden; }",
            timeout=10000,
        )

    # -- 用例 -----------------------------------------------------------

    def test_the_panel_lists_every_enabled_source_with_its_raw_amount(self):
        """四个源各一行；每行写着今日原始量与折算出来的额度。"""
        self.open_page()
        self.wait_rendered()
        rows = self.rows()
        self.assertEqual(sorted(rows), ["koreader", "pom", "weread", "words"],
                         "面板没有列出四个源：%r" % (rows,))
        # 微信读书 1753 秒 ⇒ floor(1753/900) = 1 个额度
        self.assertIn("微信读书", rows["weread"])
        self.assertIn("1753 秒", rows["weread"])
        self.assertIn("1 个额度", rows["weread"])
        # 番茄钟 25 分钟 ⇒ floor(25/25) = 1 个额度
        self.assertIn("番茄钟", rows["pom"])
        self.assertIn("25 分钟", rows["pom"])
        self.assertIn("1 个额度", rows["pom"])
        # 今天没量的两个源也在（不隐藏）
        self.assertIn("0 个额度", rows["words"])
        self.assertIn("0 分钟", rows["koreader"])
        # 合计 2 / 2
        self.assertEqual(" ".join(self.page.inner_text("#cpTotal").split()), "2 / 2 个额度")

    def test_the_credit_on_the_page_is_the_same_as_the_server_calculates(self):
        """**对拍**：页面算的额度 == 服务端 scripts/sources.py 算的额度。"""
        cfg = self.registry_response
        day = {"weread": 1753, "pom": 25}
        server = src.credit_for_day(cfg, day)
        self.assertEqual(server, {"weread": 1, "pom": 1, "words": 0, "koreader": 0})
        self.assertEqual(src.day_credit(cfg, day), 2)

        self.state_response = _state(today_sources=day)
        self.open_page()
        self.wait_rendered()
        # 页面自己的那一份
        page_credits = self.page.evaluate(
            "() => { const o = {}; DexPage.sourceCredits().forEach(r => o[r.id] = r.credit); return o; }")
        self.assertEqual(page_credits, server, "页面和服务端算的额度不一样")
        # 页面上画出来的那一份
        rows = self.rows()
        for sid, n in server.items():
            self.assertIn("%d 个额度" % n, rows[sid], "%s 那行不对：%s" % (sid, rows[sid]))
        # 合计也一样
        self.assertEqual(
            self.page.evaluate("() => DexPage.sourceCredits().reduce((s, r) => s + r.credit, 0)"),
            src.day_credit(cfg, day))

    def test_the_folding_matches_the_server_on_every_edge_case(self):
        """逐个边界值对拍：零、负数、超单日上限、刚好一个额度、差一个。"""
        cfg = self.registry_response
        self.open_page()
        self.wait_rendered()
        raw_values = (0, 1, 14, 15, 16, 24, 25, 26, 99, 100, 101, 149, 150,
                      899, 900, 901, 1753, 5000, 99999, -5)
        for sid, s in cfg["sources"].items():
            for raw in raw_values:
                with self.subTest(source=sid, raw=raw):
                    server = src.credit_for_day(cfg, {sid: raw})[sid]
                    page = self.page.evaluate(
                        "(a) => DexPage.creditForSource(a[0], a[1])", [raw, s])
                    self.assertEqual(
                        page, server,
                        "源 %s 原始量 %s：页面算 %s，服务端算 %s" % (sid, raw, page, server))

    def test_the_credit_is_floored_per_day_and_does_not_carry_over(self):
        """**按天 floor**：今天 150 个词 + 昨天 50 个词 ⇒ 合计 **1** 个额度。

        floor(150/100) + floor(50/100) = 1 + 0 = 1；
        按总数算就是 floor(200/100) = 2 ⇒ **多算一个额度** ⇒ 这条必须钉死。
        """
        ledger = {
            "2026-11-21": {"words": 50},
            "2026-11-22": {"words": 150},
        }
        self.state_response = _state(today_sources={"words": 150})
        self.state_response["ledger"] = ledger
        self.open_page()
        self.wait_rendered()
        rows = self.rows()
        self.assertIn("1 个额度", rows["words"])
        self.assertEqual(" ".join(self.page.inner_text("#cpTotal").split()), "1 / 2 个额度")
        # 和服务端逐日算的**一样**（不是 2）
        cfg = self.registry_response
        server = src.credits_for_days(cfg, ledger)
        self.assertEqual(server, {"2026-11-21": 0, "2026-11-22": 1})
        self.assertEqual(sum(server.values()), 1)
        self.assertNotEqual(sum(server.values()), 2)

    def test_a_source_with_no_amount_today_is_still_listed(self):
        """今天没量的源**不隐藏**（那一行要写着 0，让人知道它存在）。"""
        self.state_response = _state(today_sources={"weread": 1753})
        self.open_page()
        self.wait_rendered()
        rows = self.rows()
        self.assertIn("words", rows)
        self.assertIn("0 个额度", rows["words"])
        self.assertIn("0 个", rows["words"])
        self.assertIn("0 个额度", rows["koreader"])
        # 那一行是**看得见的**（不是 display:none）
        self.assertTrue(self.page.locator('#cpRows .cpRow[data-src="words"]').is_visible())

    def test_a_disabled_source_is_neither_listed_nor_counted(self):
        """注册表里 enabled: false 的源：不列、不结算。"""
        self.registry_response = _registry(enabled_words=False)
        self.state_response = _state(today_sources={"weread": 1753, "words": 500})
        self.open_page()
        self.wait_rendered()
        rows = self.rows()
        self.assertNotIn("words", rows)
        self.assertEqual(" ".join(self.page.inner_text("#cpTotal").split()), "1 / 2 个额度")
        # 服务端也是这个口径
        cfg = self.registry_response
        self.assertEqual(src.day_credit(cfg, {"weread": 1753, "words": 500}), 1)

    def test_the_panel_does_not_use_the_weread_only_threshold(self):
        """`today.seconds` / `today.threshold` 是**微信读书专用**的旧口径。

        这里把它们故意写错（seconds 大得离谱、threshold 小得离谱）⇒
        面板要是拿它们当多源口径，数字就会露馅。
        """
        self.state_response = _state(
            today_sources={"weread": 1753, "pom": 25},
            seconds=99999, threshold=1)
        self.open_page()
        self.wait_rendered()
        self.assertEqual(" ".join(self.page.inner_text("#cpTotal").split()), "2 / 2 个额度")
        rows = self.rows()
        self.assertIn("1753 秒", rows["weread"])
        self.assertIn("1 个额度", rows["weread"])

    def test_the_threshold_text_speaks_in_credits_not_in_minutes(self):
        """阈值文案不再说「读满 30 分钟」，改成额度语义。"""
        self.open_page()
        self.wait_rendered()
        self.open_sheet("0471")          # 没收服的那只 ⇒ 浮层里是锁定文案
        msg = self.page.inner_text(".lockedMsg")
        self.assertIn("额度", msg)
        self.assertNotIn("读满", msg)
        self.assertIn("2 个额度", msg)

    def test_the_threshold_text_lists_what_two_credits_are_worth(self):
        """「2 个额度」要说清楚相当于多少：微信读书 30 分钟、番茄钟 50 分钟…"""
        self.open_page()
        self.wait_rendered()
        sentence = self.page.evaluate("() => DexPage.thresholdSentence(false)")
        self.assertIn("2 个额度", sentence)
        self.assertIn("微信读书 30 分钟", sentence)   # 900 秒 × 2 = 1800 秒 = 30 分钟
        self.assertIn("番茄钟 50 分钟", sentence)     # 25 分钟 × 2 = 50 分钟
        self.assertIn("背单词 200 个", sentence)      # 100 个 × 2 = 200 个

    def test_the_page_survives_a_missing_registry(self):
        """注册表拉不到 ⇒ 页面**不崩**，并且说含糊但不说错的话。"""
        self.registry_response = None
        self.open_page()
        self.wait_rendered()
        self.assertEqual(self.page_errors, [])
        # 面板还在，额度总数用状态文件里那个（服务端自己算的）
        self.assertEqual(" ".join(self.page.inner_text("#cpTotal").split()), "2 / 2 个额度")
        self.assertIn("读不到", self.page.inner_text("#cpNote"))
        # 文案退回含糊的说法：有「额度」，没有「读满」，也没有具体分钟数
        self.open_sheet("0471")
        msg = self.page.inner_text(".lockedMsg")
        self.assertIn("额度", msg)
        self.assertNotIn("读满", msg)
        self.assertNotIn("30 分钟", msg)

    def test_the_page_survives_a_missing_state_file(self):
        """状态文件拉不到 ⇒ 页面不崩，顶栏空着就行（别显示一个假的 0）。"""
        self.page.route("**/dex.json", lambda r: r.abort())
        self.open_page()
        self.page.wait_for_function(
            "() => { const s = document.querySelector('#summary');"
            "return !!s && s.textContent.indexOf('载入中') < 0; }",
            timeout=30000,
        )
        self.assertEqual(self.page_errors, [])
        self.assertIn("状态没加载上", self.page.inner_text("#summary"))

    def test_the_collected_form_decides_which_picture_and_name_are_shown(self):
        """`collected[].form` ≠ id ⇒ 显示的是**形态**的图与名字。"""
        self.state_response = _state(
            today_sources={"weread": 1753, "pom": 25},
            collected=[{
                "id": "0001", "nameZh": "妙蛙种子", "shiny": False,
                "at": "2026-11-20T10:00:00+08:00", "date": "2026-11-20",
                "sources": ["weread"], "form": "0003",
            }])
        self.open_page()
        self.wait_rendered()
        cell = self.page.query_selector('.cell[data-id="0001"]')
        self.assertEqual(cell.query_selector("img").get_attribute("src"), "img/0003.webp")
        text = " ".join(cell.inner_text().split())
        self.assertIn("妙蛙花", text)
        self.assertIn("妙蛙种子 → 妙蛙花", text)   # 基础名作为副标题

    def test_a_collected_entry_without_a_form_falls_back_to_its_id(self):
        """老数据没有 `form` ⇒ 回退到 id（图与名字都用 id 那条）。"""
        self.state_response = _state(
            today_sources={"weread": 1753},
            collected=[{
                "id": "0004", "nameZh": "小火龙", "shiny": False,
                "at": "2026-11-21T10:00:00+08:00", "date": "2026-11-21",
                "sources": ["weread"],
            }])
        self.open_page()
        self.wait_rendered()
        cell = self.page.query_selector('.cell[data-id="0004"]')
        self.assertEqual(cell.query_selector("img").get_attribute("src"), "img/0004.webp")
        text = " ".join(cell.inner_text().split())
        self.assertIn("小火龙", text)
        self.assertNotIn("→", text)     # 没有别的形态 ⇒ 不写形态行

    def test_the_chain_progress_pins_the_pity_count_and_the_days_left(self):
        """链级养成：保底可用数与「还差几天」钉死具体数字。"""
        self.state_response = _state(
            today_sources={"weread": 1753},
            chains={
                # 两天前收服的 ⇒ 养了 2 天，还差 28 天
                "宝贝龙": {"firstDate": "2026-11-20", "lastShinyDate": None},
                # 兑现过一次保底 ⇒ 从 lastShinyDate 重新计时（11-01 → 11-22 = 21 天）
                "伊布": {"firstDate": "2026-10-01", "lastShinyDate": "2026-11-01"},
                # 刚好养满 30 天 ⇒ 保底可用 1 次、**已就绪**、条满格
                "快龙": {"firstDate": "2026-10-23", "lastShinyDate": None},
            })
        self.open_page()
        self.wait_rendered()
        rows = self.chain_rows()
        self.assertIn("保底可用 0 次 · 还差 28 天", rows["宝贝龙"])
        self.assertIn("保底可用 0 次 · 还差 9 天", rows["伊布"])   # 21 天 ⇒ 30 - 21
        self.assertIn("保底可用 1 次 · 已就绪（下一轮还差 30 天）", rows["快龙"])
        # 进度条：2/30、21/30、满格
        widths = self.page.eval_on_selector_all(
            ".chainBar i", "els => els.map(e => e.style.width)")
        self.assertAlmostEqual(float(widths[0].rstrip("%")), 100 * 2 / 30, places=1)
        self.assertAlmostEqual(float(widths[1].rstrip("%")), 100 * 21 / 30, places=1)
        self.assertAlmostEqual(float(widths[2].rstrip("%")), 100.0, places=1)

    def test_the_chain_progress_is_consistent_on_every_boundary_day(self):
        """边界那六天：保底可用数、还差几天、进度条三者**不许互相打架**。

        评审提的问题：旧写法在「刚好养满 30 天」那天会说
        「保底可用 1 次 · 还要再等 30 天 · 条已经满了」—— 三个信号互相否定；
        而且第 31 天的进度条会从 100% 掉回 3.3%（倒退）。
        现在拆成两个量：攒到了就写「已就绪」，条满格之后不倒退。
        """
        today = datetime.date(2026, 11, 22)
        # (链名, 养了几天, 保底可用数, 还差几天, 下一轮还差几天, 文案, 进度条)
        cases = [
            ("刚收养", 0, 0, 30, 30, "保底可用 0 次 · 还差 30 天", 0.0),
            ("养了29天", 29, 0, 1, 1, "保底可用 0 次 · 还差 1 天", 100 * 29 / 30),
            ("养了30天", 30, 1, 0, 30, "保底可用 1 次 · 已就绪（下一轮还差 30 天）", 100.0),
            ("养了31天", 31, 1, 29, 29, "保底可用 1 次 · 已就绪（下一轮还差 29 天）", 100.0),
            ("养了59天", 59, 1, 1, 1, "保底可用 1 次 · 已就绪（下一轮还差 1 天）", 100.0),
            ("养了60天", 60, 2, 0, 30, "保底可用 2 次 · 已就绪（下一轮还差 30 天）", 100.0),
        ]
        chains = {}
        for name, d, _cycles, _remain, _next, _text, _pct in cases:
            base = (today - datetime.timedelta(days=d)).isoformat()
            chains[name] = {"firstDate": base, "lastShinyDate": None}
        self.state_response = _state(today_sources={"weread": 1753}, chains=chains)
        self.open_page()
        self.wait_rendered()
        rows = self.chain_rows()
        widths = self.page.eval_on_selector_all(
            ".chainBar i", "els => els.map(e => e.style.width)")
        for i, (name, d, cycles, remain, next_wait, text, pct) in enumerate(cases):
            with self.subTest(days=d):
                self.assertIn(text, rows[name],
                              "养了 %d 天那行不对：%s" % (d, rows.get(name)))
                self.assertAlmostEqual(float(widths[i].rstrip("%")), pct, places=1,
                                       msg="养了 %d 天的进度条不对：%s" % (d, widths[i]))
                # 页面自己算出来的量也对得上
                pr = self.page.evaluate("(c) => DexPage.chainProgress(c)", chains[name])
                self.assertEqual(pr["cycles"], cycles, "养了 %d 天的保底可用数不对" % d)
                self.assertEqual(pr["remain"], remain, "养了 %d 天的还差天数不对" % d)
                self.assertEqual(pr["nextWait"], next_wait, "养了 %d 天的下一轮还差天数不对" % d)
                self.assertEqual(pr["ready"], cycles > 0)
                self.assertAlmostEqual(pr["pct"], pct, places=1)

    def test_the_chain_bar_never_goes_backwards_after_it_is_full(self):
        """条满格之后**不许倒退**：养了 30 天是 100%，养了 31 天还是 100%。"""
        today = datetime.date(2026, 11, 22)
        chains = {}
        for d in (29, 30, 31, 32, 59, 60, 61):
            base = (today - datetime.timedelta(days=d)).isoformat()
            chains["链%d" % d] = {"firstDate": base, "lastShinyDate": None}
        self.state_response = _state(today_sources={"weread": 1753}, chains=chains)
        self.open_page()
        self.wait_rendered()
        widths = [float(w.rstrip("%")) for w in self.page.eval_on_selector_all(
            ".chainBar i", "els => els.map(e => e.style.width)")]
        by_days = {d: w for d, w in zip((29, 30, 31, 32, 59, 60, 61), widths)}
        self.assertAlmostEqual(by_days[29], 100 * 29 / 30, places=1)
        for d in (30, 31, 32, 59, 60, 61):
            self.assertAlmostEqual(by_days[d], 100.0, places=1,
                                   msg="养了 %d 天进度条不该是 %s%%" % (d, by_days[d]))

    def test_the_chain_section_is_hidden_when_there_are_no_chains(self):
        """chains 为空 ⇒ 整块不显示（别留一个空标题）。"""
        self.state_response = _state(today_sources={"weread": 1753}, chains={})
        self.open_page()
        self.wait_rendered()
        self.assertTrue(self.page.locator("#chains").is_hidden())
        self.assertEqual(self.page.inner_text("#chainList").strip(), "")

    def test_the_streak_is_shown_on_the_top(self):
        """顶部要看得见连读天数。"""
        self.state_response = _state(today_sources={"weread": 1753}, streak=3)
        self.open_page()
        self.wait_rendered()
        self.assertIn("连读 3 天", self.page.inner_text("#streak"))

    def test_the_page_does_not_overflow_horizontally_on_a_phone(self):
        """手机宽度下不许横向溢出。"""
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
        self.open_page()
        self.wait_rendered()
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.console_errors, [])

    def test_the_page_has_no_external_resource(self):
        """不许引外部 CDN 的框架/字体/脚本 —— 外链在她那边（大陆直连）会挂。"""
        def check_url(url: str, where: str):
            url = url.strip()
            self.assertFalse(
                url.startswith("http://") or url.startswith("https://"),
                "%s 引了外链：%s" % (where, url))
            self.assertFalse(url.startswith("//"),
                             "%s 引了协议相对的外链：%s" % (where, url))

        html = INDEX_HTML.read_text(encoding="utf-8")
        for m in re.finditer(r"""(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)')""", html):
            url = m.group(1) if m.group(1) is not None else m.group(2)
            check_url(url, "图鉴页的 HTML")
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]*)", html):
            check_url(m.group(1), "图鉴页的 HTML")

        css = STYLE_CSS.read_text(encoding="utf-8")
        for m in re.finditer(r"url\(\s*['\"]?([^'\")]*)", css):
            check_url(m.group(1), "style.css")
        self.assertNotIn("@import", css, "style.css 里不许 @import 外链")

        app = APP_JS.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s'\"]+", app):
            check_url(url, "app.js")


# -- 小工具 -------------------------------------------------------------


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

        def handle_error(self, request, client_address):
            """浏览器掐掉请求（route.abort）时的 BrokenPipe 不该刷屏。"""

    httpd = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


if __name__ == "__main__":
    unittest.main()
