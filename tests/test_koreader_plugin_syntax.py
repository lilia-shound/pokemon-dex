"""插件主体（`main.lua` / `_meta.lua`）的静态检查。

**刻意不模拟 KOReader** —— 沙箱里 stub 出来的 API 只会给人虚假信心：
真机上才能验的东西（菜单出不出来、二维码扫不扫得出来、exec 返回值的形状）
由插件里的「自检」入口兜底（见 `main.lua` 的 `onSelfTest`）。

这里做两件事：

1. **只编译不执行**：把 Lua 源码用 Lua 的 `load()` 编一遍（fengari 代替真解释器）
   ⇒ 抓住拼错的关键字、括号不配、`end` 少一个这类低级错误。
   （真机上一堆插件就是被这种错误顶回来的。）
2. **静态断言**：把任务书里点名的那几件事钉在文件里 ——
   三个菜单项、四个「说人话」的提示、剪贴板兜底要 `pcall` 包住、
   不许自己拼 SQL、额度的口径要跟服务端注册表一致……

静态断言不是「形式主义」：这些正是评审要点名的东西，
用正则钉住 ⇒ 以后谁改了，一跑测试就红。
"""

from __future__ import annotations

import json
import pathlib
import re
import tempfile
import unittest

from tests import koreader_lua_bridge as bridge

ROOT = bridge.ROOT
PLUGIN_DIR = ROOT / "plugin" / "koreader-dex.koplugin"
MAIN_LUA = PLUGIN_DIR / "main.lua"
META_LUA = PLUGIN_DIR / "_meta.lua"
REPORT_LUA = PLUGIN_DIR / "koreader_dex_report.lua"
SOURCES_JSON = ROOT / "data" / "sources.json"

# main.lua 里允许出现的 require —— 全是 KOReader 自带的模块（外加同目录的纯逻辑模块）
# **不许有第三方依赖**（Lua 侧零第三方依赖是全局约束）
#
# 同目录那个模块的名字必须**全局唯一**：KOReader 把所有插件目录都加进 package.path，
# `require("report")` 这种通用名会被别的插件的同名文件抢走。
ALLOWED_REQUIRES = {
    "gettext",
    "datastorage",
    "device",
    "lua-ljsqlite3/init",
    "ui/uimanager",
    "ui/widget/container/widgetcontainer",
    "ui/widget/infomessage",
    "ui/widget/qrmessage",
    "koreader_dex_report",
}

# 任务书点名的四个「说人话」的提示（**原文**）
MSG_NO_DB = "KOReader 的统计数据库还没建 —— 先读一会儿书，或打开一次 Reading statistics"
MSG_OLD_DB = "先在 KOReader 里打开一次 Reading statistics，让它把数据库升级"
MSG_NO_DATA = "还没有阅读记录"
# 「内置 statistics 没开」那条没有给原文，只要求「提示她先打开」
MSG_DISABLED_HINTS = ("Reading statistics", "打开")


class PluginSyntaxTest(unittest.TestCase):
    """main.lua / _meta.lua 能不能编过（只编译，不执行）。"""

    @classmethod
    def setUpClass(cls):
        cls.node = bridge.find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过 KOReader Lua 编译检查")
        if not bridge.fengari_available(cls.node):
            raise unittest.SkipTest("找不到 fengari（npm install 没跑），跳过 KOReader Lua 编译检查")

    def compile_lua(self, path: pathlib.Path):
        """只编译不执行。返回 runner 的输出。"""
        proc = bridge.lua_compile(self.node, path)
        self.assertEqual(
            proc.returncode, 0,
            "%s 编译不过：\nstdout:\n%s\nstderr:\n%s" % (path.name, proc.stdout, proc.stderr),
        )
        return proc

    def test_main_lua_compiles(self):
        """main.lua 要能编过 —— 少一个 end 的话真机上是直接顶不起来的。"""
        proc = self.compile_lua(MAIN_LUA)
        self.assertIn("编译通过", proc.stdout)

    def test_meta_lua_compiles(self):
        """_meta.lua 要能编过（插件管理器会 dofile 它）。"""
        proc = self.compile_lua(META_LUA)
        self.assertIn("编译通过", proc.stdout)

    def test_report_lua_compiles(self):
        """koreader_dex_report.lua 也要能编过（main.lua 要它）。"""
        proc = self.compile_lua(REPORT_LUA)
        self.assertIn("编译通过", proc.stdout)

    def test_the_pure_module_has_a_unique_name(self):
        """纯逻辑模块的文件名要**全局唯一**（避免被别的插件抢走 require 的名字）。

        KOReader 把所有插件目录都加进 package.path
        （上游 frontend/pluginloader.lua）⇒ `require("report")` 这种通用名
        会被别的插件的同名文件抢走 ⇒ 我们会静默用到别人的模块。
        """
        self.assertTrue(REPORT_LUA.exists(), "缺少 koreader_dex_report.lua")
        self.assertFalse(
            (PLUGIN_DIR / "report.lua").exists(),
            "report.lua 这个名字太通用了，要改成 koreader_dex_report.lua",
        )
        self.assertEqual(REPORT_LUA.name, "koreader_dex_report.lua")

    def test_compile_check_actually_catches_broken_lua(self):
        """这个检查本身要有效：编不过的文件必须被抓住（否则是假绿）。"""
        with tempfile.TemporaryDirectory() as tmp:
            broken = pathlib.Path(tmp) / "broken_main.lua"
            broken.write_text("local x =\nif x then\n", encoding="utf-8")
            proc = bridge.lua_compile(self.node, broken)
            self.assertNotEqual(proc.returncode, 0, "编不过的文件居然编过了")
            self.assertIn("编译失败", proc.stderr)


class PluginStaticTest(unittest.TestCase):
    """main.lua 的静态断言：任务书点名的东西要钉在文件里。"""

    @classmethod
    def setUpClass(cls):
        cls.text = MAIN_LUA.read_text(encoding="utf-8")
        cls.meta_text = META_LUA.read_text(encoding="utf-8")
        # **只看代码**：注释里提到某个 API 不算数（注释是给人看的）
        cls.code = bridge.strip_lua_comments(cls.text)
        cls.self_test = cls._function_body(cls.code, "function DexPlugin:onSelfTest()")
        cls.read_stats = cls._function_body(cls.code, "function DexPlugin:readStats()")
        cls.describe = cls._function_body(cls.code, "local function describe_error(")

    @staticmethod
    def _function_body(code: str, header: str) -> str:
        """截出某个**顶层**函数的代码（从函数头到它的 end）。

        只取**代码**（注释已去掉）⇒ 里面出现什么就是真的用了什么。
        顶层函数的 end 独占一行（缩进的 end 是别的）⇒ 按行首的 end 找终点。
        """
        idx = code.find(header)
        if idx == -1:
            return ""
        end = code.find("\nend\n", idx)
        return code[idx:] if end == -1 else code[idx:end + 5]

    # -- 菜单 -------------------------------------------------------------

    def test_main_lua_returns_the_plugin_table(self):
        """main.lua 的最后一行得 return 插件表 —— KOReader 才认得出来。"""
        self.assertRegex(self.text, r"return\s+\w+\s*$")

    def test_main_menu_has_the_three_items(self):
        """三个入口：汇报最近 7 天 / 只汇报今天 / 自检。"""
        for label in ("汇报阅读时长（最近 7 天）", "只汇报今天", "自检"):
            self.assertIn(label, self.text, "菜单里少了「%s」" % label)

    def test_main_menu_is_under_more_tools(self):
        """挂在主菜单的「工具」下（sorting_hint = "more_tools"）。"""
        self.assertIn('sorting_hint = "more_tools"', self.text)

    def test_main_menu_uses_add_to_main_menu(self):
        """要用 addToMainMenu 挂到主菜单。"""
        self.assertIn("function DexPlugin:addToMainMenu(menu_items)", self.text)

    # -- 四个「说人话」的提示 ----------------------------------------------

    def test_the_four_plain_language_messages_are_present(self):
        """任务书点名的提示要**原文**在（她是照着这些字去操作的）。"""
        self.assertIn(MSG_NO_DB, self.text, "「数据库还没建」那条提示不对")
        self.assertIn(MSG_OLD_DB, self.text, "「老库没升级」那条提示不对")
        self.assertIn(MSG_NO_DATA, self.text, "「还没有阅读记录」那条提示不对")
        # 「内置 statistics 没开」那条没给原文 ⇒ 只要求：说清楚要先打开它
        m = re.search(r'MSG_DISABLED\s*=\s*"([^"]*)"', self.code)
        self.assertIsNotNone(m, "main.lua 里没有 MSG_DISABLED")
        disabled = m.group(1)
        for hint in MSG_DISABLED_HINTS:
            self.assertIn(hint, disabled, "「内置 statistics 没开」那条提示缺了 %s" % hint)

    def test_messages_are_shown_with_info_message(self):
        """提示要用 InfoMessage 显示。"""
        self.assertIn('require("ui/widget/infomessage")', self.text)
        self.assertIn("InfoMessage:new", self.text)

    def test_the_lag_hint_is_present(self):
        """数据滞后的提示必须有（内置 statistics 是攒着写库的）。"""
        self.assertIn("还没落库", self.text)
        self.assertIn("息屏", self.text)

    # -- 读数据库 ---------------------------------------------------------

    def test_reads_the_official_statistics_database(self):
        """读的是内置 statistics 的库（settings/statistics.sqlite3）。"""
        self.assertIn('DataStorage:getSettingsDir() .. "/statistics.sqlite3"', self.text)

    def test_uses_the_single_sql_entry_point(self):
        """SQL 的格式化只有纯逻辑模块一个入口 —— main.lua 不准自己拼 SQL。"""
        self.assertIn("Report.daily_sql(", self.text)
        self.assertNotRegex(self.code, r"(?i)\bselect\b", "main.lua 里出现了自己拼的 SQL")

    def test_uses_the_official_sqlite_binding(self):
        """用 KOReader 自带的 ljsqlite3（不是第三方库）。"""
        self.assertIn('require("lua-ljsqlite3/init")', self.text)

    def test_checks_the_builtin_statistics_switch(self):
        """内置 statistics 没开要提示（is_enabled，默认 true）。"""
        self.assertIn('G_reader_settings:readSetting("statistics"', self.code)
        self.assertIn("is_enabled", self.code)

    def test_read_setting_has_no_default_value(self):
        """readSetting **不许传默认值**。

        上游 readSetting(key, default) 在 key 不存在时会把 default **写进设置里**
        （frontend/luasettings.lua：`if not self:has(key) then self.data[key] = default end`）
        ⇒ 我们要是传 {is_enabled = true}，就会抢先造出一张只有 is_enabled 的残表，
        内置 statistics 之后读到的 min_sec / max_sec / convert_to_db 全变 nil。

        「表压根不存在」= 内置 statistics 从没被打开过 ⇒ 按「默认开着」处理。
        """
        m = re.search(r'G_reader_settings:readSetting\(\s*"statistics"[^)]*\)', self.code)
        self.assertIsNotNone(m, "main.lua 里没有 readSetting(\"statistics\", ...)")
        self.assertEqual(
            m.group(0), 'G_reader_settings:readSetting("statistics")',
            "readSetting 不许带默认值（会往设置里写一张残表）",
        )
        # 「表不存在 ⇒ 当作开着」：只有明确写成 false 才算没开
        self.assertIn("settings.is_enabled == false", self.code)

    # -- 日历窗口 ---------------------------------------------------------

    def test_calendar_window_lives_in_the_pure_module(self):
        """日历窗口的纯逻辑必须住在纯逻辑模块里（沙箱里才测得到）。

        main.lua 里不许自己再实现一遍窗口计算（那就又变成零覆盖的纯逻辑了）。
        """
        self.assertIn("Report.window_lower(", self.code)
        self.assertIn("Report.window_upper(", self.code)
        self.assertNotIn("local function window_lower", self.code)
        self.assertNotIn("local function window_upper", self.code)

    def test_window_clock_is_passed_in_by_the_caller(self):
        """时钟要由调用方传进去（Report.window_lower(now, count)）。"""
        m = re.search(r"Report\.window_lower\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,", self.code)
        self.assertIsNotNone(m, "没找到 Report.window_lower(now, count) 的调用")
        self.assertNotEqual(m.group(1), "os.time()", "时钟要作为参数传进去，不能在模块里取")

    # -- 自检表（对账用） --------------------------------------------------

    def test_self_test_lists_every_day_that_has_pages(self):
        """自检表要列出窗口内**所有有页数的天**（含不足 1 分钟的天）。

        内置 statistics 的表是按 page_stat 的行分组的：一天读了 45 秒也会显示成一行。
        我们要是把 0 分钟的天丢掉 ⇒ 她在内置那边看到一天、插件这边没有 ⇒
        **误判插件坏了**。而这张表就是唯一的对账手段。
        """
        self.assertTrue(self.self_test, "找不到 onSelfTest 的函数体")
        self.assertNotIn(
            "Report.pick_days", self.self_test,
            "自检表不能用 pick_days（它会把 0 分钟的天丢掉）",
        )
        self.assertIn("Report.collect_pages(", self.self_test)
        self.assertIn("Report.collect_days(", self.self_test)

    def test_self_test_uses_a_monospace_font(self):
        """自检表用等宽字体 —— 默认的比例字体下两列对不齐（对账要看的正是这两列）。"""
        self.assertIn("monospace_font", self.code)

    def test_self_test_gives_the_self_test_a_scrollable_height(self):
        """自检表很长 ⇒ 要给一个显式高度（上游 InfoMessage：给了 height 才可滚动）。"""
        self.assertIn("Screen:scaleBySize(400)", self.code)

    def test_self_test_explains_that_minutes_drop_the_seconds(self):
        """要说明「分钟 = 丢掉秒数」（内置显示 时:分:秒，插件显示整分钟）。"""
        self.assertIn("丢掉秒数", self.text)
        self.assertIn("时:分:秒", self.text)

    def test_self_test_points_at_the_upstream_menu_names(self):
        """对账指引要照抄上游原文的菜单名。

        上游 plugins/statistics.koplugin/main.lua：
          菜单项「Time range」→ 页面标题「Time range statistics」
          → 条目「Last week」（7 天，逐天）与「Last month by day」（30 天）。
        **没有**「Last week by day」这种说法。
        """
        self.assertIn("Time range", self.text)
        self.assertIn("Time range statistics", self.text)
        self.assertIn("Last week", self.text)
        self.assertIn("Last month by day", self.text)
        self.assertNotIn("Last week by day", self.text)

    # -- 错误提示的判别 ----------------------------------------------------

    def test_error_is_judged_by_the_exact_sqlite_shape(self):
        """「老库没升级」只认 SQLite 缺表的那句原文（"no such table"）。

        早先用「错误文本里含 page_stat」来判 ⇒ 将来任何提到 page_stat 的错误
        都会被说成「老库没升级」，反而把方向指错。
        """
        self.assertTrue(self.describe, "找不到 describe_error 的函数体")
        self.assertIn("no such table", self.describe)
        self.assertNotIn('find("page_stat"', self.describe)

    # -- 二维码与剪贴板 ----------------------------------------------------

    def test_qr_code_carries_the_landing_page_short_link(self):
        """二维码里放的是落地页短链（不是 GitHub 长链）。"""
        self.assertIn("Report.short_url(", self.text)
        self.assertIn("QRMessage:new", self.text)

    def test_clipboard_fallback_is_pcall_guarded(self):
        """剪贴板兜底必须 pcall 包住（那个 API 没核实过，失败不许把插件搞崩）。"""
        m = re.search(
            r"local function copy_to_clipboard\(.*?\nend\b",
            self.code,
            re.S,
        )
        self.assertIsNotNone(m, "main.lua 里没有 copy_to_clipboard 这个函数")
        body = m.group(0)
        self.assertIn("pcall(", body, "剪贴板那段没有 pcall 包住")
        self.assertIn("setClipboardText", body, "剪贴板那段没有复制链接")

    # -- 额度口径 ---------------------------------------------------------

    def test_credits_per_minutes_matches_the_server_registry(self):
        """插件折算的额度必须与服务端注册表一致（两边各写一份就会对不上账）。"""
        registry = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
        per = registry["sources"]["koreader"]["per"]
        m = re.search(r"CREDITS_PER_MINUTES\s*=\s*(\d+)", self.code)
        self.assertIsNotNone(m, "main.lua 里没有 CREDITS_PER_MINUTES")
        self.assertEqual(
            int(m.group(1)), per,
            "main.lua 的 CREDITS_PER_MINUTES 与 data/sources.json 的 koreader.per 不一致",
        )

    def test_uses_report_credits_for_the_credit_count(self):
        """额度要用纯逻辑模块的 credits（与服务端口径一致）。"""
        self.assertIn("Report.credits(", self.text)

    # -- 依赖 -------------------------------------------------------------

    def test_no_third_party_dependencies(self):
        """Lua 侧零第三方依赖：只准 require KOReader 自带的模块 + 同目录的纯逻辑模块。"""
        found = set(re.findall(r'require\(\s*"([^"]+)"', self.code))
        extra = found - ALLOWED_REQUIRES
        self.assertFalse(extra, "main.lua 里出现了不该有的依赖：%s" % sorted(extra))

    def test_meta_lua_has_fullname_and_description(self):
        """_meta.lua 要有 fullname 与 description（插件管理器里显示的东西）。"""
        self.assertIn("fullname", self.meta_text)
        self.assertIn("description", self.meta_text)


if __name__ == "__main__":
    unittest.main()
