"""KOReader 两份说明书的一致性检查。

**为什么要有这个文件**：沙箱里没有真机 —— 插件的「自检表」和这两份文档
是这一整套东西**唯一的验收手段**。她照着做的时候看到什么，必须和文档写的一致。

文档里最容易漂、又最难在沙箱里发现的，就是这几处：

- **菜单位置**（插件用 `sorting_hint = "more_tools"` ⇒ 落在「工具 → 更多工具」，
  和内置那个「Reading statistics」（在「工具」一级）不是同一处）；
- **内置统计开关的真实位置**（上游把它挂在「工具」组，开关是第一个子项「Enabled」
  —— **设置页里没有这一项**；照着「设置 → Reading statistics」找会白翻半天）；
- **上游那行的格式**（每行是「日期 (星期几)  时长 (N 页)」—— **带星期几**；
  示例少了星期几 ⇒ 她会以为内置那边多了一列 ⇒ 误判插件坏了）；
- **对账第 1 步的顺序**（上游查询之前会先落库 ⇒ 先开内置那一页、再跑自检；
  顺序反了会差出刚落库的那一段 ⇒ **假警报**）；
- 上游还有「Last year by day」（365 天）我们用不上；
- 上游的**时长显示格式**会随设置变（默认 classic = 时:分:秒）；
- **插件文件从哪儿下载**（线上没有插件目录的话，她根本不知道从哪拿）：
  目录 URL（**发布之后**才有）只是索引，还得给出 4 个文件的**直链**；
  文档也不许写成「已经发布好了」（还没部署 ⇒ 她白跑一趟）。

这些全是**评审点名的项**，用断言钉住 ⇒ 以后谁改了，一跑测试就红。
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugin" / "koreader-dex.koplugin"
MAIN_LUA = PLUGIN_DIR / "main.lua"
PLUGIN_README = PLUGIN_DIR / "README.md"
SETUP_DOC = ROOT / "docs" / "koreader-setup.md"
ROOT_README = ROOT / "README.md"
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"

# 两份文档都要说的东西（插件的说明书 + 装到用起来的手册）
DOC_NAMES = ("插件 README", "装到用起来的手册")

# 插件目录在 gh-pages 上的地址（**发布之后**才有）
PLUGIN_DIR_URL = "https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/"
# 插件目录里的 4 个文件 —— 直链同样**发布之后**才有
PLUGIN_FILES = ("main.lua", "koreader_dex_report.lua", "_meta.lua", "README.md")

# 上游那条真实路径：「工具 → 阅读统计（Reading statistics）→ 已启用（Enabled）」。
#
# 钉「内置那项在工具一级、**不在更多工具里**」时不能用「同一行里有『工具』和
# 『Reading statistics』」这种松正则 —— **「更多工具」里也含「工具」两个字**，
# 写成「更多工具 → Reading statistics」照样蒙混过关。
# 这条正则是三条一起钉：
#   1. 「工具」前面不能是「更多」（⇒ 那个「工具」是一级工具）；
#   2. 「工具」到「Reading statistics」之间不许出现「更多工具」（⇒ 路径不是「更多工具 → …」）；
#   3. 「Reading statistics」后面不远处要点名**具体的开关**「已启用 / Enabled」
#      （⇒ 写的是上游真实路径，而不是随口提一句）。
BUILTIN_SWITCH_PATH = re.compile(
    r"(?<!更多)工具"                     # 一级「工具」（「更多工具」里的那个不算）
    r"(?:(?!更多工具)[\s\S]){0,120}?"    # 到 Reading statistics 之间不许出现「更多工具」
    r"Reading statistics"
    r"(?:(?!更多工具)[\s\S]){0,160}?"    # 到开关之间也不许出现「更多工具」
    r"(?:已启用|Enabled)"
)


class KoreaderDocsTest(unittest.TestCase):
    """两份说明书里的「路径 / 格式」不许漂。"""

    @classmethod
    def setUpClass(cls):
        cls.plugin_readme = PLUGIN_README.read_text(encoding="utf-8")
        cls.setup_doc = SETUP_DOC.read_text(encoding="utf-8")
        cls.root_readme = ROOT_README.read_text(encoding="utf-8")
        cls.texts = {
            "插件 README": cls.plugin_readme,
            "装到用起来的手册": cls.setup_doc,
        }
        # 提到「插件文件从哪儿下载」的三份文档（含仓库根 README）
        cls.download_texts = {
            "插件 README": cls.plugin_readme,
            "装到用起来的手册": cls.setup_doc,
            "仓库根 README": cls.root_readme,
        }

    # -- 菜单位置 ---------------------------------------------------------

    def test_both_docs_say_where_the_menu_item_is(self):
        """两份文档都要写清楚：菜单项在「工具 → 更多工具 → 训练家图鉴」。

        插件的 `sorting_hint = "more_tools"` ⇒ 上游把它插进**工具 → 更多工具**
        （`more_tools` 是 `tools` 的子菜单）—— 写成「主菜单里找」她只能自己翻。
        """
        for name, text in self.texts.items():
            with self.subTest(doc=name):
                self.assertIn("更多工具", text, "%s 没写菜单位置（更多工具）" % name)
                self.assertIn("训练家图鉴", text)

    def test_both_docs_keep_the_builtin_menu_at_the_tools_level(self):
        """内置那个「Reading statistics」在**工具**一级，不在「更多工具」里 —— 别写混。

        这条钉子以前太松：只要**同一行 40 字内**同时出现「工具」和「Reading statistics」
        就算过 —— 可「更**多工具**」里也含「工具」两个字 ⇒ 文档就算写成
        「更多工具 → Reading statistics」也蒙混过关 ⇒ 钉不住「它不在更多工具里」。

        现在要求写出**上游那条真实路径**：「工具 → 阅读统计（Reading statistics）→
        已启用（Enabled）」—— 得点名那个具体的开关，才说明是真照着上游写的；
        并且这条路径里**不许**夹着「更多工具」。
        """
        for name, text in self.texts.items():
            with self.subTest(doc=name):
                m = BUILTIN_SWITCH_PATH.search(text)
                self.assertIsNotNone(
                    m,
                    "%s 没说内置统计在「工具」一级（不是「更多工具」）："
                    "要写「工具 → 阅读统计（Reading statistics）→ 已启用（Enabled）」" % name,
                )

    def test_the_stricter_builtin_path_rejects_more_tools(self):
        """把上面那条正则的松紧自己也钉一下（免得以后有人把它改回松的）。

        「更多工具 → Reading statistics」这种写法**必须**匹配不上。
        """
        bad = "路径：更多工具 → Reading statistics → 已启用（Enabled）"
        self.assertIsNone(
            BUILTIN_SWITCH_PATH.search(bad),
            "内置统计在「更多工具」里的写法竟然匹配上了 —— 这条钉子被改松了",
        )

    # -- 内置统计开关的真实位置 -------------------------------------------

    def test_both_docs_point_at_the_real_switch_location(self):
        """开关的位置要按上游写：**工具 → Reading statistics → Enabled**。

        上游把「Reading statistics」挂在 tools 组，Enabled 是它的第一个子项；
        **设置页里没有这一项** ⇒ 文档里不许再出现「设置 → Reading statistics」。
        """
        for name, text in self.texts.items():
            with self.subTest(doc=name):
                self.assertIn("Reading statistics", text, "%s 没提内置统计那一项" % name)
                self.assertIn("Enabled", text, "%s 没点名开关 Enabled" % name)
                self.assertNotIn(
                    "设置 → Reading statistics", text,
                    "%s 写了上游不存在的「设置 → Reading statistics」路径" % name,
                )

    # -- 上游那行的格式（带星期几） ---------------------------------------

    def test_both_docs_show_the_weekday_in_the_upstream_row_example(self):
        """举例的那一行必须**带星期几**（上游真实格式）。

        上游 plugins/statistics.koplugin/main.lua:
          string.format("%s (%s)", os.date("%Y-%m-%d", timestamp),
                        datetime.shortDayOfWeekTranslation[os.date("%a", timestamp)])
        ⇒ 每行是「2026-11-19 (Thu)   00:00:45 (1 page)」
        （中文界面里 Thu 翻译成「周四」）。
        """
        pattern = re.compile(r"\d{4}-\d{2}-\d{2} \((周[一二三四五六日]|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\)")
        for name, text in self.texts.items():
            with self.subTest(doc=name):
                m = pattern.search(text)
                self.assertIsNotNone(
                    m, "%s 里的上游示例少了星期几（格式：日期 (星期几)  时长 (N 页)）" % name,
                )

    # -- 对账第 1 步：顺序、365 天那张、时长格式 ---------------------------

    def test_the_setup_doc_explains_why_the_order_cannot_be_reversed(self):
        """手册里要写明**为什么顺序不能反**（不然她照着反了会以为插件坏了）。

        上游 getDatesFromAll 在查询**之前**会先 insertDB() ⇒
        先开内置那一页，两边读到的才是同一批落库数据；
        反过来内置那边会多出刚落库的那一段 ⇒ **假警报**。
        """
        self.assertIn("顺序不能反", self.setup_doc)
        self.assertIn("insertDB", self.setup_doc)
        self.assertIn("假警报", self.setup_doc)

    def test_the_setup_doc_mentions_the_365_day_table(self):
        """上游还有「Last year by day」（365 天）—— 要说一句我们用不上。"""
        self.assertIn("Last year by day", self.setup_doc)
        self.assertIn("365", self.setup_doc)

    def test_the_setup_doc_mentions_the_duration_format(self):
        """上游的时长**格式**跟着设置走（默认 classic = 时:分:秒）。"""
        self.assertIn("classic", self.setup_doc)
        self.assertIn("时长", self.setup_doc)

    # -- 插件文件从哪儿下载 ------------------------------------------------

    def test_the_setup_doc_says_where_to_get_the_plugin_files(self):
        """手册要写清**插件文件从哪儿下载**（不然她没地方拿）。"""
        self.assertIn(
            "https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/",
            self.setup_doc,
            "手册里没写插件目录的线上地址",
        )
        self.assertIn("plugins/", self.setup_doc, "手册里没说要拷到设备的 plugins/ 目录")

    def test_the_plugin_readme_says_where_to_get_the_plugin_files(self):
        """插件自己的说明书也要写下载方式。"""
        self.assertIn(
            "https://lilia-shound.github.io/pokemon-dex/koreader-dex.koplugin/",
            self.plugin_readme,
            "插件 README 里没写插件目录的线上地址",
        )

    # -- 目录页 + 4 条文件直链（目录 URL 要能打开） -------------------------

    def test_all_three_docs_list_the_four_direct_links(self):
        """三份文档都要给出 **4 个文件的直链**（目录页只是索引，不给文件）。

        她要的是「把文件下下来放进 `koreader-dex.koplugin/`」⇒ 只给一个目录地址
        不够用（目录页上并没有「打包下载」这个按钮）。
        """
        for name, text in self.download_texts.items():
            with self.subTest(doc=name):
                self.assertIn(
                    PLUGIN_DIR_URL, text, "%s 没写插件目录的线上地址" % name,
                )
                for fn in PLUGIN_FILES:
                    self.assertIn(
                        PLUGIN_DIR_URL + fn, text,
                        "%s 少了 %s 的直链" % (name, fn),
                    )

    def test_all_three_docs_say_the_dir_page_is_an_index(self):
        """目录地址要写清楚**它只是索引，文件直链在最下面**。"""
        for name, text in self.download_texts.items():
            with self.subTest(doc=name):
                self.assertIn("索引", text, "%s 没说目录页是个索引" % name)
                self.assertIn("最下面", text, "%s 没说文件直链在目录页的最下面" % name)

    def test_the_docs_do_not_claim_the_plugin_is_already_online(self):
        """线上那几条地址**现在还不存在** ⇒ 文档不许写成「已经发布好了」。

        这个仓库的老教训：文档写「已经」，其实还没部署 ⇒ 她白跑一趟（还以为是坏了）。
        正确说法是「跑完 `scripts/deploy.sh` 之后才会有」。
        """
        for name, text in self.download_texts.items():
            with self.subTest(doc=name):
                for bad in ("已经发布好了", "线上已经发布", "已经上线", "线上有现成的"):
                    self.assertNotIn(
                        bad, text, "%s 写了还没发生的事：%s" % (name, bad),
                    )
                self.assertIn(
                    "deploy.sh", text,
                    "%s 没说这些地址要等跑完 deploy.sh（发布）之后才有" % name,
                )

    def test_deploy_publishes_an_index_page_for_the_plugin_dir(self):
        """发布时得给插件目录写一个 `index.html` ⇒ 目录 URL 才打得开。

        GitHub Pages **不会**给没有 `index.html` 的目录生成索引页 ⇒ 只发目录的话，
        她点那个「推荐」的目录链接就是 404 ⇒ 插件根本下不到。
        """
        script = DEPLOY_SH.read_text(encoding="utf-8")
        self.assertIn(
            "koreader-dex.koplugin/index.html", script,
            "deploy.sh 没给插件目录写索引页（目录 URL 会 404）",
        )
        # 索引页必须在**拷贝插件目录之后**写（否则会被前面的 rm -rf 抹掉）
        copy_at = script.index('cp -r "$ROOT/plugin/koreader-dex.koplugin"')
        index_at = script.index("koreader-dex.koplugin/index.html")
        self.assertLess(
            copy_at, index_at, "索引页要在拷贝插件目录之后写",
        )
        # 索引页里的 4 条直链用**相对路径**（域名换了也不用改）
        for fn in PLUGIN_FILES:
            self.assertIn(
                'href="%s"' % fn, script,
                "索引页里没有 %s 的相对直链" % fn,
            )
        # 仓库里的插件目录**不能**有 index.html —— 那是插件本体，
        # KOReader 会读它；索引页是**部署产物**，只进 gh-pages 的发布树。
        self.assertFalse(
            (PLUGIN_DIR / "index.html").exists(),
            "仓库里的插件目录不该有 index.html（它是部署产物，不进版本库）",
        )

    # -- 插件 README 里贴的自检表 = 插件真正弹出来的那张表 -------------------

    def test_the_plugin_readme_quotes_the_self_test_text_verbatim(self):
        """插件 README 里贴的那张自检表，必须和插件**真正弹出来**的那段一字不差。

        弹窗文本是逐行拼出来的（`lines[#lines + 1] = "…"`）⇒ 这里把源码里的字面量
        抽出来，逐行在 README 的代码块里找。
        两处不一致 = 给了她两套说法（照着哪一套做都可能对不上）。
        """
        lua = MAIN_LUA.read_text(encoding="utf-8")
        literals = re.findall(r'lines\[#lines \+ 1\] = "([^"]*)"', lua)
        self.assertTrue(literals, "main.lua 里没找到自检表的弹窗文本")
        start = next(
            (i for i, s in enumerate(literals) if s.startswith("怎么对照")),
            None,
        )
        self.assertIsNotNone(start, "main.lua 的自检表里没有「怎么对照」那一段")
        block = self.plugin_readme.split("```\n训练家图鉴 · 自检\n", 1)
        self.assertEqual(len(block), 2, "插件 README 里找不到自检表的代码块")
        block = block[1].split("```", 1)[0]
        for line in literals[start:]:
            if not line.strip():
                continue
            self.assertIn(
                line, block,
                "插件 README 里贴的自检表和插件实际弹的不一致：%s" % line,
            )
        # 每次都会出现的那句提醒也一样
        m = re.search(r'local MSG_LAG = "([^"]*)"', lua)
        self.assertIsNotNone(m, "main.lua 里没有 MSG_LAG")
        self.assertIn(m.group(1), block, "README 里的自检表示例少了那句「还没落库」提醒")


if __name__ == "__main__":
    unittest.main()
