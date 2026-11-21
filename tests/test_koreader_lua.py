"""KOReader 插件纯逻辑（report.lua）的单元测试。

沙箱里没有 Lua 解释器，用 **fengari**（Lua 5.3 的 JS 实现）跑：
    node tests/koreader_lua_runner.js

断言写在 tests/koreader_lua_spec.lua（纯 Lua），这个文件只负责：
    1. 把 node 的输出/退出码变成 Python 断言
    2. node 或 fengari 缺失时**优雅跳过**（不能让整套测试挂掉）
    3. 跨语言一致性：同一个天数串，Lua 侧与 Python 侧必须解出一样的结果

天数串的格式（`日期:分钟`，逗号分隔，日期倒序）是 Lua 的 `encode_days` 定下的，
**唯一的真值来源** —— 手机落地页（site/koreader/app.js）必须解出一样的结果。
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import unittest

from scripts import sources as src

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
RUNNER = TESTS / "koreader_lua_runner.js"
SPEC = TESTS / "koreader_lua_spec.lua"
FIXTURE = TESTS / "fixtures" / "koreader_days.txt"
REPORT_LUA = ROOT / "plugin" / "koreader-dex.koplugin" / "report.lua"
SOURCES_JSON = ROOT / "data" / "sources.json"

# 天数串里每一段的样子：YYYY-MM-DD:分钟
WIRE_CHUNK = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+)$")

# 断言集至少要跑这么多条（防止 spec 被清空 ⇒ 一片绿但什么都没测）
MIN_LUA_CASES = 20


def parse_days(wire: str) -> dict[str, int]:
    """落地页那个 `days=` 参数怎么解。

    与 Lua 的 `Report.decode_days` 一一对应（同样的正则、同样的忽略规则）：
    解不出来的段直接丢掉，不当成 0 分钟的一天。
    """
    out: dict[str, int] = {}
    for chunk in (wire or "").split(","):
        m = WIRE_CHUNK.match(chunk.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def encode_days(days: dict) -> str:
    """与 Lua 的 `Report.encode_days` 对应：日期倒序，逗号分隔。"""
    return ",".join(f"{d}:{int(days[d])}" for d in sorted(days, reverse=True))


def find_node() -> str | None:
    """找 node。PATH 里没有就看看常见安装位置。"""
    exe = shutil.which("node")
    if exe:
        return exe
    for cand in ("/usr/local/bin/node", "/usr/bin/node", "/opt/homebrew/bin/node"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def fengari_available(node: str) -> bool:
    """fengari 装没装（npm install 跑过没有）。"""
    try:
        proc = subprocess.run(
            [node, "-e", "require('fengari')"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


class KoreaderLuaTest(unittest.TestCase):
    """report.lua 的纯逻辑：用 fengari 跑 Lua 断言集。"""

    @classmethod
    def setUpClass(cls):
        cls.node = find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过 KOReader Lua 单测")
        if not fengari_available(cls.node):
            raise unittest.SkipTest("找不到 fengari（npm install 没跑），跳过 KOReader Lua 单测")
        cls.fixture = FIXTURE.read_text(encoding="utf-8").strip()
        cls.probe = cls.run_probe()

    # -- runner 包装 ---------------------------------------------------

    @classmethod
    def run_runner(cls, *args) -> subprocess.CompletedProcess:
        """跑 fengari runner。**不用 shell=True**（参数直接进 argv）。"""
        return subprocess.run(
            [cls.node, str(RUNNER), *args],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        )

    @classmethod
    def run_probe(cls) -> dict:
        """跑探针：让 Lua 侧把一个天数串过一遍，返回结构化结果。"""
        proc = cls.run_runner("--probe")
        if proc.returncode != 0:
            raise AssertionError(
                "探针没跑起来（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
                % (proc.returncode, proc.stdout, proc.stderr)
            )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        raise AssertionError("探针没有输出 JSON：\n" + proc.stdout)

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
                   "pick_days", "credits", "DAILY_SQL"):
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


class KoreaderReportSourceTest(unittest.TestCase):
    """report.lua 这个文件本身的形状（不需要 Lua 解释器）。"""

    @classmethod
    def setUpClass(cls):
        cls.text = REPORT_LUA.read_text(encoding="utf-8")

    def test_report_lua_does_not_require_koreader_modules(self):
        """report.lua 不许 require 任何 KOReader 模块 —— 否则沙箱里根本跑不起来。"""
        self.assertTrue("require(" not in self.text, "report.lua 里出现了 require")

    def test_report_lua_exports_the_report_table(self):
        self.assertIn("return Report", self.text)

    def test_report_lua_defines_every_pure_function(self):
        for fn in ("encode_days", "decode_days", "short_url", "to_minutes",
                   "pick_days", "credits"):
            self.assertIn(f"function Report.{fn}(", self.text, "少了 Report.%s" % fn)

    def test_report_lua_has_the_daily_sql_constant(self):
        self.assertIn("Report.DAILY_SQL", self.text)

    def test_spec_file_is_pure_lua(self):
        """断言文件不许依赖 fengari 的专属 API（那不是「纯 Lua」了）。"""
        spec = SPEC.read_text(encoding="utf-8")
        self.assertTrue("require(" not in spec, "断言文件不该 require 任何东西")
        self.assertTrue("fengari." not in spec, "断言文件不该用 fengari 的专属 API")
        self.assertIsNone(re.search(r"io\.open\s*\(", spec), "fengari 的 io 库是残的，断言文件别调 io.open")


if __name__ == "__main__":
    unittest.main()
