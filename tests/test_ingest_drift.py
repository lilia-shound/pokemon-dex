"""三个子页的投递逻辑不许分叉（**防漂移测试**）。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_ingest_drift

★ 为什么要有这条：
  三个子页（番茄钟 / 背单词 / KOReader）**各写一份**投递逻辑 ——
  子页只许引**一个** `<script>` ⇒ 不能抽共享文件 ⇒
  重复是**受约束的重复**，必须被守住。

  以前三页的 `pendingQueue` / `savePending` / `enqueueReport` / `shiftPending`
  是逐字相同的；`postIngest` / `ingestAvailable` / `flushPending`
  只有缩进、配置变量名（`state.ingest` vs `INGEST`）、末行提示不同。
  ⇒ 一处改了、另一处忘了改 ⇒ 三个页行为分叉，**而且没有任何测试会红**。
  （本轮修的三个 Important 全是这类「三页同款」的毛病。）

★ 怎么比：
  抠出三个子页的共享投递函数 → **归一化**（去注释、压空白、去标点旁的空格、
  把配置变量名统一成同一个占位符）⇒ 三份必须**逐字相同**。

★ 变异测试开关：环境变量 `POM_APP_JS` / `WORDS_APP_JS` / `KO_APP_JS`
  可以指向 app.js 的另一份拷贝 ⇒ 把其中一页改坏，这条测试必须红。
"""

from __future__ import annotations

import os
import pathlib
import re
import unittest

from tests.test_worker_client import _function_body, _strip_js_comments

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 三个子页：app.js 的路径（变异测试可以指到别处）
PAGES = [
    ("pomodoro",
     pathlib.Path(os.environ.get("POM_APP_JS") or (ROOT / "site" / "pomodoro" / "app.js"))),
    ("words",
     pathlib.Path(os.environ.get("WORDS_APP_JS") or (ROOT / "site" / "words" / "app.js"))),
    ("koreader",
     pathlib.Path(os.environ.get("KO_APP_JS") or (ROOT / "site" / "koreader" / "app.js"))),
]

# 三个子页**必须**逐字相同的那几个函数。
#
# ★ 为什么是这几个：
#   - 队列那四个（pendingQueue / savePending / enqueueReport / shiftPending）
#     决定「报告有没有存住、还剩几条」；
#   - postIngest / ingestAvailable / flushPending 决定「怎么交、失败怎么办」；
#   - isRealDate / todayCst / filterReportDays 决定「哪些日子服务端会收」
#     （与服务端 worker/lib/validate.cjs 对拍，见 tests/test_client_filter_parity.py）；
#   - flushMsg 是补发结果的措辞（三页同一句）。
SHARED_FUNCTIONS = [
    "pendingQueue",
    "savePending",
    "enqueueReport",
    "shiftPending",
    "postIngest",
    "ingestAvailable",
    "flushPending",
    "isRealDate",
    "todayCst",
    "filterReportDays",
    "flushMsg",
]

# 一个 JS 记号：字符串 / 注释 / 空白 / 其他
_TOKEN_RE = re.compile(
    r"""
      (?P<string>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`)
    | (?P<comment>//[^\n]*|/\*.*?\*/)
    | (?P<space>\s+)
    | (?P<other>.)
    """,
    re.S | re.X,
)


def _normalize(js: str) -> str:
    """把一段 JS 归一化，让「只有排版不同」的代码能逐字比较。

    处理掉这几处已知差异：

    1. 配置变量名：koreader / words 用 `state.ingest`，番茄钟用 `INGEST`
       ⇒ 统一替换成 `INGEST`；
    2. 注释：各页的注释写得不一样（上下文不同）⇒ 注释不算差异；
    3. 缩进与空白 ⇒ 压成单空格；
    4. 标点旁的空格 ⇒ 去掉（`if (x) {` 与 `if(x){` 应当一样）。
    """
    js = js.replace("state.ingest", "INGEST")
    js = _strip_js_comments(js)
    # 字符串先「保护」起来 —— 不然第 4 步的标点处理会动到字符串内容
    strings: list[str] = []

    def _stash(m):
        strings.append(m.group())
        return "\x00%d\x00" % (len(strings) - 1)

    js = _TOKEN_RE.sub(lambda m: _stash(m) if m.lastgroup == "string" else m.group(), js)
    js = re.sub(r"\s+", " ", js).strip()
    js = re.sub(r"\s*([{}()\[\];,=<>+\-*/%!?:&|])\s*", r"\1", js)
    js = re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], js)
    return js


class IngestDriftTest(unittest.TestCase):
    """三个子页的共享投递函数必须逐字相同。"""

    @classmethod
    def setUpClass(cls):
        cls.normalized = {}
        for name, app_js in PAGES:
            js = app_js.read_text(encoding="utf-8")
            cls.normalized[name] = {}
            for fn in SHARED_FUNCTIONS:
                body = _function_body(js, "function %s(" % fn)
                cls.normalized[name][fn] = _normalize(body)

    def test_the_three_pages_have_the_same_shared_functions(self):
        """★ 每个共享函数，三页归一化之后必须**逐字相同**。"""
        for fn in SHARED_FUNCTIONS:
            texts = {name: self.normalized[name][fn] for name, _ in PAGES}
            distinct = set(texts.values())
            self.assertEqual(
                len(distinct), 1,
                "★ 三个子页的 `%s` 分叉了 —— 只改了一页 ⇒ 三个页行为会不一样：\n%s"
                % (fn, "\n".join(
                    "  —— %s ——\n%s" % (name, text)
                    for name, text in sorted(texts.items())
                )),
            )

    def test_every_shared_function_exists_on_every_page(self):
        """★ 少一个函数也不行（抠不出来 ⇒ 直接报错，不许当成「一样」）。"""
        for name, app_js in PAGES:
            js = app_js.read_text(encoding="utf-8")
            for fn in SHARED_FUNCTIONS:
                self.assertIn(
                    "function %s(" % fn, js,
                    "%s 页里没有 `%s` —— 共享的投递逻辑不许各页少一块" % (name, fn),
                )

    def test_the_normalization_actually_normalizes(self):
        """★ 归一化本身要靠谱：只差排版的两段代码必须归一成同一个串。

        （不然「三页一样」这条断言可能是因为**什么都没比**。）
        """
        a = """
        function demo(x) {
          // 一句注释
          if (x > 0) { return 'a' + x; }
          return 'b';
        }
        """
        b = """function demo(x){/*另一句注释*/if(x>0){return 'a'+x;}return 'b';}"""
        self.assertEqual(_normalize(a), _normalize(b),
                         "归一化没把「只差排版」的两段代码归成同一个")

    def test_the_normalization_keeps_real_differences(self):
        """★ 但**真的**不一样的地方必须仍然不一样（不能归一过头）。"""
        a = "function demo(x) { return x > 0; }"
        b = "function demo(x) { return x >= 0; }"
        self.assertNotEqual(_normalize(a), _normalize(b),
                            "归一化过头了：真的差异被抹平了")
        c = "function demo(x) { return 'a' + x; }"
        d = "function demo(x) { return 'b' + x; }"
        self.assertNotEqual(_normalize(c), _normalize(d),
                            "归一化过头了：字符串内容被抹平了")


if __name__ == "__main__":
    unittest.main()
