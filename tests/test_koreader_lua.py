"""KOReader 插件纯逻辑（report.lua）的单元测试。

沙箱里没有 Lua 解释器，用 **fengari**（Lua 5.3 的 JS 实现）跑：
    node tests/koreader_lua_runner.js

断言写在 tests/koreader_lua_spec.lua（纯 Lua），这个文件只负责：
    1. 把 node 的输出/退出码变成 Python 断言
    2. node 或 fengari 缺失时**优雅跳过**（不能让整套测试挂掉）
    3. 跨语言一致性：同一个天数串，Lua 侧与 Python 侧必须解出一样的结果

天数串的格式（`日期:分钟`，逗号分隔，日期倒序）是 Lua 的 `encode_days` 定下的，
**唯一的真值来源** —— 手机落地页（site/koreader/app.js）必须解出一样的结果。

线格式的**唯一真值来源**是 `tests/fixtures/koreader_wire_cases.txt`：
好坏线都在里面，Lua 侧与 Python 侧读的是同一份 ⇒ 两边不可能各自漂移。
"""

import re
import unittest

from scripts import sources as src

from tests import koreader_lua_bridge as bridge

ROOT = bridge.ROOT
FIXTURE = bridge.FIXTURE
REPORT_LUA = ROOT / "plugin" / "koreader-dex.koplugin" / "report.lua"
SOURCES_JSON = ROOT / "data" / "sources.json"
SPEC = bridge.SPEC

# 天数串里每一段的样子：YYYY-MM-DD:分钟
#
# **数字只认 ASCII 0-9** —— 用 [0-9] 而不是 \d：
# Python 的 \d 也认 Unicode 数字（"٢��٦" 那种），Lua 的 %d 不认 ⇒ 两边会分歧。
WIRE_CHUNK = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}):([0-9]+)$")

# 断言集至少要跑这么多条（防止 spec 被清空 ⇒ 一片绿但什么都没测）
MIN_LUA_CASES = 20


def parse_days(wire: str) -> dict[str, int]:
    """落地页那个 `days=` 参数怎么解。

    与 Lua 的 `Report.decode_days` **一一对应**（同样的正则、同样的忽略规则）：
    - 一段必须**完全**匹配 `YYYY-MM-DD:分钟` ⇒ **不 trim**、不做别的容错；
    - 数字只认 ASCII 0-9；
    - 解不出来的段直接丢掉，不当成 0 分钟的一天。
    """
    out: dict[str, int] = {}
    for chunk in (wire or "").split(","):
        m = WIRE_CHUNK.match(chunk)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def encode_days(days: dict) -> str:
    """与 Lua 的 `Report.encode_days` 对应：日期倒序，逗号分隔。"""
    return ",".join(f"{d}:{int(days[d])}" for d in sorted(days, reverse=True))


class KoreaderLuaTest(unittest.TestCase):
    """report.lua 的纯逻辑：用 fengari 跑 Lua 断言集。"""

    @classmethod
    def setUpClass(cls):
        cls.node = bridge.find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过 KOReader Lua 单测")
        if not bridge.fengari_available(cls.node):
            raise unittest.SkipTest("找不到 fengari（npm install 没跑），跳过 KOReader Lua 单测")
        cls.fixture = FIXTURE.read_text(encoding="utf-8").strip()
        cls.probe = cls.run_probe()

    # -- runner 包装 ---------------------------------------------------

    @classmethod
    def run_runner(cls, *args):
        """跑 fengari runner。**不用 shell=True**（参数直接进 argv）。"""
        return bridge.run_runner(cls.node, *args)

    @classmethod
    def run_probe(cls) -> dict:
        """跑探针：让 Lua 侧把天数串过一遍，返回结构化结果。"""
        return bridge.run_probe(cls.node)

    # -- 用例 -----------------------------------------------------------

    def test_lua_assertions_all_pass(self):
        """Lua 断言集全绿。"""
        proc = self.run_runner()
        self.assertEqual(
            proc.returncode, 0,
            "runner 退出码 %d\nstdout:\n%s\nstderr:\n%s" % (proc.returncode, proc.stdout, proc.stderr),
        )
        self.assertIn("0 失败", proc.stdout)

    def test_lua_spec_actually_ran_cases(self):
        """断言集不能是空的 —— 空跑一遍也是「0 失败」，那是假绿。"""
        proc = self.run_runner()
        m = re.search(r"合计：(\d+) 通过 / (\d+) 失败", proc.stdout)
        self.assertIsNotNone(m, "runner 没输出汇总行：\n" + proc.stdout)
        self.assertGreaterEqual(int(m.group(1)), MIN_LUA_CASES)
        self.assertEqual(int(m.group(2)), 0)

    def test_lua_spec_covers_every_pure_function(self):
        """brief 里点名的纯逻辑，每一条都要有用例。"""
        proc = self.run_runner()
        for fn in ("encode_days", "decode_days", "short_url", "to_minutes",
                   "pick_days", "credits", "DAILY_SQL", "daily_sql",
                   "MAX_MINUTES_PER_DAY", "collect_days", "collect_pages",
                   "filter_dates"):
            self.assertIn(fn, proc.stdout, "断言集里没有 %s 的用例" % fn)

    def test_fixture_roundtrips_in_lua(self):
        """fixture 在 Lua 侧解得开、也编得回去。"""
        self.assertEqual(self.probe["encoded"], self.fixture)
        self.assertEqual(self.probe["decoded"], parse_days(self.fixture))

    def test_python_and_lua_agree_on_the_wire_format(self):
        """跨语言一致性：同一个天数串，两边解出来的结果必须一样。"""
        self.assertEqual(parse_days(self.fixture), {"2026-11-21": 25, "2026-11-20": 50})
        self.assertEqual(encode_days(parse_days(self.fixture)), self.fixture)
        self.assertEqual(self.probe["decoded"], parse_days(self.fixture))
        self.assertEqual(self.probe["encoded"], encode_days(parse_days(self.fixture)))

    def test_python_parser_ignores_broken_chunks(self):
        """坏段直接丢掉（跟 Lua 的 decode_days 一样），不能变成 0 分钟的一天。"""
        wire = "abc,2026-1-1:5,2026-11-21:,2026-11-21:xx,2026-11-22:7"
        self.assertEqual(parse_days(wire), {"2026-11-22": 7})

    def test_python_parser_drops_chunks_with_whitespace(self):
        """带空白的段整段丢掉 —— **不 trim**（Lua 的 decode_days 也不 trim）。"""
        self.assertEqual(parse_days("2026-11-21:25, 2026-11-20:50"), {"2026-11-21": 25})
        self.assertEqual(parse_days(" 2026-11-21:25"), {})
        self.assertEqual(parse_days("2026-11-21:25 "), {})
        self.assertEqual(parse_days("2026-11-21:2 5"), {})

    def test_python_parser_drops_unicode_digits(self):
        """数字只认 ASCII 0-9 —— Python 的 \\d 会认 Unicode 数字，所以不能用它。"""
        self.assertEqual(parse_days("٢٠٢٦-11-21:5"), {})
        self.assertEqual(parse_days("2026-11-21:٢٥"), {})

    def test_lua_and_python_agree_on_every_wire_case(self):
        """逐例对拍：好坏线都要两边解出一样的结果。

        用例来自 `tests/fixtures/koreader_wire_cases.txt`（**唯一真值来源**），
        喂进 fengari 探针再和 Python 对拍 ——
        只在两个文件里各写一份字面量的话，两边一起漂移都发现不了。
        """
        cases = bridge.read_wire_cases()
        self.assertTrue(cases, "线格式的用例文件不能是空的")
        lua_cases = {c["wire"]: c for c in self.probe["cases"]}
        self.assertEqual(set(lua_cases), set(cases), "探针跑到的用例要和用例文件一一对应")
        for wire in cases:
            with self.subTest(wire=wire):
                self.assertEqual(lua_cases[wire]["decoded"], parse_days(wire))
                # 编回去也要一样（落地页会把解出来的天数再编回去）
                self.assertEqual(
                    lua_cases[wire]["encoded"], encode_days(parse_days(wire))
                )

    def test_lua_short_url_points_at_the_landing_page(self):
        """二维码里那条短链必须指向落地页（不是 GitHub 的长链）。"""
        url = self.probe["short_url"]
        self.assertTrue(url.startswith("https://lilia-shound.github.io/pokemon-dex/koreader/?days="))
        self.assertTrue(url.endswith(self.fixture))
        self.assertNotIn(" ", url)
        self.assertTrue(all(33 <= ord(c) <= 126 for c in url), "短链里出现了非 ASCII 字符")

    def test_lua_credits_match_the_server_ledger(self):
        """插件折算的额度要跟服务端（scripts/sources.py）折算的一样。

        两边的口径都是：**按天**向下取整，不跨天凑零头。
        """
        cfg = src.load_config(SOURCES_JSON)
        days = parse_days(self.fixture)
        ledger = {d: {"koreader": m} for d, m in days.items()}
        server = sum(src.credits_for_days(cfg, ledger).values())
        self.assertEqual(self.probe["credits"], server)
        # 25 分钟 + 50 分钟，按 15 分钟 1 个额度 ⇒ 1 + 3 = 4（不是 floor(75/15)=5）
        self.assertEqual(server, 4)

    def test_lua_credits_apply_the_daily_cap_like_the_server(self):
        """超过单日上限的分钟要夹住 —— 与服务端 credit_for_day 的口径一致。"""
        cfg = src.load_config(SOURCES_JSON)
        ledger = {"2026-11-21": {"koreader": 5000}}
        server = sum(src.credits_for_days(cfg, ledger).values())
        self.assertEqual(server, 1440 // 15, "服务端：夹到 1440 分钟再折算")
        self.assertEqual(self.probe["credits_over_cap"], server, "插件侧要跟服务端一样")

    def test_lua_daily_cap_matches_the_registry(self):
        """report.lua 里的单日上限要跟服务端注册表一致（不许各写一份）。"""
        cfg = src.load_config(SOURCES_JSON)
        self.assertEqual(self.probe["max_per_day"], cfg["sources"]["koreader"]["maxPerDay"])


class KoreaderCollectTest(unittest.TestCase):
    """`Report.collect_days` / `Report.collect_pages` 的跨语言对拍。

    Lua 侧跑的是 report.lua 里的真函数；Python 侧是**独立实现**的同一套规则
    （秒 → 分钟，向下取整）—— 两边对不上就说明有一边错了。

    还要**端到端**对一次：拿真的 SQL（插件那条 DAILY_SQL）在假库上查出来的行，
    折成 ljsqlite3 那种「每一列一个数组」的形状喂给 Lua ⇒
    这样连**列号**（第 1 列日期 / 第 2 列页数 / 第 3 列秒数）都对上了才算数。
    """

    @classmethod
    def setUpClass(cls):
        cls.node = bridge.find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过 KOReader Lua 单测")
        if not bridge.fengari_available(cls.node):
            raise unittest.SkipTest("找不到 fengari（npm install 没跑），跳过 KOReader Lua 单测")

    # -- 跑 Lua 侧 ---------------------------------------------------------

    def lua_collect(self, fn: str, cols: list[list]) -> dict:
        """让 Lua 侧处理一份查询结果。"""
        return bridge.lua_collect(self.node, fn, cols)

    @staticmethod
    def to_columns(rows: list[tuple]) -> list[list]:
        """把行数组折成「每一列一个数组」（ljsqlite3 的 conn:exec 返回的形状）。"""
        cols: list[list] = [[] for _ in range(4)]
        for row in rows:
            for i, value in enumerate(row):
                cols[i].append(value)
        return cols

    # -- 用例 ---------------------------------------------------------------

    def test_collect_days_matches_the_python_reference(self):
        """同一份查询结果，两边解出来的分钟数必须一样。"""
        cols = [
            ["2026-11-21", "2026-11-20"],
            [3, 8],
            [1500, 720],
            [1766275200, 1766188800],
        ]
        # Python 侧的独立实现：秒 → 分钟，向下取整
        expected = {d: s // 60 for d, s in zip(cols[0], cols[2])}
        self.assertEqual(expected, {"2026-11-21": 25, "2026-11-20": 12})
        self.assertEqual(self.lua_collect("collect_days", cols), expected)

    def test_collect_days_floors_seconds_to_minutes(self):
        """秒数向下取整（119 秒 → 1 分钟，59 秒 → 0 分钟）。"""
        cols = [["2026-11-21"], [1], [119], [0]]
        self.assertEqual(self.lua_collect("collect_days", cols), {"2026-11-21": 1})
        cols = [["2026-11-21"], [1], [59], [0]]
        self.assertEqual(self.lua_collect("collect_days", cols), {"2026-11-21": 0})

    def test_collect_days_on_an_empty_result(self):
        """空结果 ⇒ 空表（不是 nil，也不是炸）。"""
        self.assertEqual(self.lua_collect("collect_days", []), {})
        self.assertEqual(self.lua_collect("collect_pages", []), {})

    def test_collect_pages_matches_the_python_reference(self):
        """页数取的是第 2 列。"""
        cols = [
            ["2026-11-21", "2026-11-20"],
            [3, 8],
            [1500, 720],
            [1766275200, 1766188800],
        ]
        expected = {d: p for d, p in zip(cols[0], cols[1])}
        self.assertEqual(self.lua_collect("collect_pages", cols), expected)

    def test_collect_agrees_with_the_rows_the_real_sql_returns(self):
        """**端到端**：真 SQL 查出来的行 ⇒ Lua 整理出来的分钟数/页数。

        这一条把「列号」也盯住了：日期取第 1 列、页数第 2 列、秒数第 3 列 ——
        写错一个下标的话，这里立刻红（而不是在真机上悄悄报出错的数字）。
        """
        from tests.test_koreader_sql import (
            DAY_START,
            add_book,
            add_reading,
            device_timezone,
            make_db,
        )

        db = make_db()
        add_book(db, 1, pages=100)
        add_reading(db, 1, page=5, start_time=DAY_START, duration=300, total_pages=100)
        add_reading(db, 1, page=6, start_time=DAY_START + 60, duration=120, total_pages=100)
        add_reading(db, 1, page=7, start_time=DAY_START + 86400, duration=1500, total_pages=100)

        sql = bridge.lua_daily_sql(self.node, 0)
        with device_timezone("UTC-8"):     # 设备在台州 = UTC+8
            rows = db.execute(sql).fetchall()

        cols = self.to_columns(rows)
        self.assertEqual(
            self.lua_collect("collect_days", cols),
            {"2026-11-22": 25, "2026-11-21": 7},
            "Lua 侧整理出来的分钟数与真 SQL 的行对不上",
        )
        self.assertEqual(
            self.lua_collect("collect_pages", cols),
            {"2026-11-22": 1, "2026-11-21": 2},
            "Lua 侧整理出来的页数与真 SQL 的行对不上",
        )


class KoreaderReportSourceTest(unittest.TestCase):
    """report.lua 这个文件本身的形状（不需要 Lua 解释器）。"""

    @classmethod
    def setUpClass(cls):
        cls.text = REPORT_LUA.read_text(encoding="utf-8")

    def test_report_lua_does_not_require_koreader_modules(self):
        """report.lua 不许 require 任何 KOReader 模块 —— 否则沙箱里根本跑不起来。"""
        self.assertTrue("require(" not in self.text, "report.lua 里出现了 require")

    def test_report_lua_exports_the_report_table(self):
        """report.lua 的最后一行得 return Report —— 插件才能 require 到它。"""
        self.assertIn("return Report", self.text)

    def test_report_lua_defines_every_pure_function(self):
        """brief 里点名的纯逻辑，report.lua 里都要有对应的函数。"""
        for fn in ("encode_days", "decode_days", "short_url", "to_minutes",
                   "pick_days", "credits", "daily_sql", "collect_days",
                   "collect_pages", "filter_dates"):
            self.assertIn(f"function Report.{fn}(", self.text, "少了 Report.%s" % fn)

    def test_report_lua_has_the_daily_sql_constant(self):
        """按天汇总的 SQL 常量要在（与内置 statistics 的 sqlDaily() 逐字一致）。"""
        self.assertIn("Report.DAILY_SQL", self.text)

    def test_report_lua_has_only_one_sql_formatting_entry_point(self):
        """SQL 的格式化只能有一个入口：Report.daily_sql。

        模板里的 `%%` 只有经过 string.format 才会解开；
        插件里要是另有一处拼 SQL（或改用绑定参数），
        日期就会变成字面的 "%Y-%m-%d"，**静默产出错数据**。
        """
        self.assertEqual(
            self.text.count("string.format("), 1,
            "report.lua 里只准有 Report.daily_sql 一处 string.format",
        )

    def test_spec_file_is_pure_lua(self):
        """断言文件不许依赖 fengari 的专属 API（那不是「纯 Lua」了）。"""
        spec = SPEC.read_text(encoding="utf-8")
        self.assertTrue("require(" not in spec, "断言文件不该 require 任何东西")
        self.assertTrue("fengari." not in spec, "断言文件不该用 fengari 的专属 API")
        self.assertIsNone(re.search(r"io\.open\s*\(", spec), "fengari 的 io 库是残的，断言文件别调 io.open")


if __name__ == "__main__":
    unittest.main()
