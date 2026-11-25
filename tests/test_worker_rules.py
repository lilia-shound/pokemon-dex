"""Worker 的限幅表不许跟服务端注册表漂移；时区口径也要对得上。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_rules
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_reports as cr  # noqa: E402
from scripts import sources as src  # noqa: E402

RULES_CJS = ROOT / "worker" / "lib" / "rules.cjs"
VALIDATE_CJS = ROOT / "worker" / "lib" / "validate.cjs"
REGISTRY = ROOT / "data" / "sources.json"

# 对拍用的日期 —— **写死**，不看机器的时钟 ⇒ 任何时区跑出来都一样。
TODAY = "2026-11-25"        # 「今天」（北京时间口径）
PAST_DAY = "2026-11-21"     # 今天之前的一天
FUTURE_DAY = "2026-11-26"   # 今天之后的一天（该被拒）
FAKE_DAY = "2026-13-01"     # 格式对、但不存在的日子（该被拒）
NORMAL = 37                # 一个远低于任何单日上限的正常用量


def _node() -> str | None:
    """找 node —— 找不到就优雅跳过（不能让整套测试挂掉）。"""
    return shutil.which("node")


def _rules() -> dict:
    """让 node 把限幅表吐成 JSON —— .cjs 里是 JS 字面量，Python 读不了。"""
    node = _node()
    if not node:
        raise unittest.SkipTest("找不到 node，跳过限幅表对拍")
    code = "console.log(JSON.stringify(require(process.argv[1]).RULES))"
    out = subprocess.run([node, "-e", code, str(RULES_CJS)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError("node 读限幅表失败：%s" % out.stderr.strip())
    return json.loads(out.stdout.strip())


def _registry() -> dict:
    """注册表的原始 JSON。"""
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _enabled_push_sources(cfg: dict) -> dict:
    """注册表里**当前 enabled** 的 push 源（源 id → 单日上限）。

    ★ 只取 enabled 的源，这是**有意的**：
      - 白名单 = 注册表里**所有** push 源（**不论 enabled**）——
        Worker 只管格式与限幅，源被停用是**服务端**拒收的理由；
      - 源被停用时 ⇒ **Worker 照样收**（它不知道源被停用了）、
        **服务端拒收**（`parse_report` 查 enabled）⇒
        「Worker 收 / 服务端拒」这条差异是**设计如此**，不是两边漂了
        ⇒ **不纳入跨语言对拍**（否则停用任何一个源，对拍就会永远红，
        而它红的原因不是 bug，是这条差异本来就该有）。
    """
    out: dict[str, int] = {}
    for sid, s in (cfg.get("sources") or {}).items():
        if s.get("mode") != "push" or not s.get("enabled"):
            continue
        cap = s.get("maxPerDay")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1:
            out[sid] = cap
    return out


def _too_many_days() -> dict:
    """比单次上限多一天的天数表（今天是上限 30 天 ⇒ 这里是 31 天）。"""
    today = datetime.date.fromisoformat(TODAY)
    out: dict[str, int] = {}
    for i in range(cr.MAX_REPORT_DAYS + 1):
        day = (today - datetime.timedelta(days=cr.MAX_REPORT_DAYS - i)).isoformat()
        out[day] = 1
    return out


def _cross_language_cases(cfg: dict) -> list[dict]:
    """构造跨语言对拍用例（**从注册表现取**）。

    每个 enabled 的 push 源各三条：正常量、**正好上限**、超上限一格。
    再加几条结构性的：空天数 / 负数 / 非整数值 / 未来 / 假日期 / 版本不对 /
    整数值浮点 / 一次带太多天 / pull 源。
    """
    caps = _enabled_push_sources(cfg)
    if not caps:
        raise unittest.SkipTest("注册表里没有 enabled 的 push 源，没法对拍")
    cases: list[dict] = []
    for sid, cap in sorted(caps.items()):
        cases.append({"v": 1, "source": sid, "days": {PAST_DAY: min(NORMAL, cap)}})  # 收
        cases.append({"v": 1, "source": sid, "days": {PAST_DAY: cap}})                 # 收（正好上限）
        cases.append({"v": 1, "source": sid, "days": {PAST_DAY: cap + 1}})             # 拒（超上限）
        # ★ 整数值浮点：JSON 里 1440.0 与 1440 是同一个数（JS 更是分不出来）
        #   ⇒ 两边都得收（Worker 侧 Number.isInteger(1440.0) 是 true）
        cases.append({"v": 1, "source": sid, "days": {PAST_DAY: float(cap)}})
    primary = sorted(caps)[0]
    cases.append({"v": 1, "source": primary, "days": {}})                   # 拒（没有天数）
    cases.append({"v": 1, "source": primary, "days": {PAST_DAY: -1}})       # 拒（负数）
    cases.append({"v": 1, "source": primary, "days": {PAST_DAY: 1.5}})      # 拒（不是整数）
    cases.append({"v": 1, "source": primary, "days": {FUTURE_DAY: 5}})      # 拒（未来）
    cases.append({"v": 1, "source": primary, "days": {FAKE_DAY: 5}})        # 拒（假日期）
    cases.append({"v": 2, "source": primary, "days": {PAST_DAY: 5}})        # 拒（版本不对）
    cases.append({"v": 1, "source": primary, "days": _too_many_days()})     # 拒（一次最多 30 天）
    pull = sorted(sid for sid, s in (cfg.get("sources") or {}).items()
                  if s.get("mode") == "pull")
    if pull:
        cases.append({"v": 1, "source": pull[0], "days": {PAST_DAY: NORMAL}})  # 拒（pull 源）
    return cases


class WorkerRulesTest(unittest.TestCase):
    """限幅表 = 源白名单 = 单日上限。"""

    def test_the_rules_file_exists(self):
        self.assertTrue(RULES_CJS.exists(), "缺 %s" % RULES_CJS)

    def test_the_rules_match_the_server_side_registry(self):
        """★ 限幅表与 data/sources.json 的 maxPerDay 必须逐字一致。"""
        cfg = _registry()
        sources = cfg.get("sources") or {}
        rules = _rules()
        # 白名单 = 注册表里所有 push 源（不多不少）
        push_sources = sorted(sid for sid, s in sources.items() if s.get("mode") == "push")
        self.assertEqual(sorted(rules.keys()), push_sources,
                         "限幅表的白名单与注册表里的 push 源对不上")
        for sid, cap in rules.items():
            # ★ 用 sources.get(sid)：白名单里出现注册表没有的源时要给出**干净的失败**，
            #   而不是 KeyError（测试 ERROR 而不是 FAIL）。
            s = sources.get(sid) or {}
            self.assertEqual(s.get("maxPerDay"), cap,
                             "源 %s 的限幅漂了：Worker 说 %s，注册表说 %s"
                             % (sid, cap, s.get("maxPerDay")))

    def test_the_rules_only_cover_push_sources(self):
        """白名单里不该出现 pull 源（微信读书是服务端自己去拉的）。

        ★ 用 `sources.get(sid)` 而不是 `sources[sid]` —— 白名单里出现注册表没有的源时，
          要一条**干净**的失败信息，而不是 KeyError（测试 ERROR 而不是 FAIL）。
          （与上面「白名单 = 注册表里的 push 源」那条有重叠；留着是因为这里的
          失败信息更直白。）
        """
        cfg = _registry()
        sources = cfg.get("sources") or {}
        for sid in _rules():
            s = sources.get(sid)
            self.assertIsNotNone(s, "白名单里的源 %s 在注册表里不存在" % sid)
            self.assertEqual(s.get("mode"), "push",
                             "源 %s 不是 push 源，不该在投递白名单里" % sid)

    def test_the_registry_has_the_ingest_block(self):
        """注册表顶层要有 ingest 段（客户端从这儿读 URL 与 token）。"""
        cfg = _registry()
        self.assertIn("ingest", cfg, "sources.json 顶层缺 ingest 段")
        ingest = cfg["ingest"]
        self.assertIsInstance(ingest.get("enabled"), bool, "ingest.enabled 必须是布尔")
        self.assertIsInstance(ingest.get("url"), str, "ingest.url 必须是字符串")
        self.assertIsInstance(ingest.get("token"), str, "ingest.token 必须是字符串")

    def test_the_worker_uses_beijing_time(self):
        """★ Worker 判「今天」用 UTC+8 —— 与账本口径一致。"""
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过时区口径检查")
        code = ("const V=require(process.argv[1]);"
                "console.log(V.todayCst(Date.parse('2026-11-25T17:30:00Z')));"
                "console.log(V.todayCst(Date.parse('2026-11-25T15:30:00Z')))")
        out = subprocess.run([node, "-e", code, str(VALIDATE_CJS)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "2026-11-26", "17:30Z 已经是北京时间的第二天了")
        self.assertEqual(lines[1], "2026-11-25", "15:30Z 还是北京时间当天")

    def test_the_report_day_limit_matches_the_ledger_window(self):
        """★ 一次最多带的天数 = 账本保留窗口（两边都要等于 ledgerDays）。

        服务端的 `MAX_REPORT_DAYS`（collect_reports）与 Worker 的
        `MAX_REPORT_DAYS`（validate.cjs）各自写死了一个 30 ——
        少了这条断言，改了其中一个而另一个没改，**没有任何测试会红**。
        """
        ledger_days = int(_registry()["ledgerDays"])
        self.assertEqual(cr.MAX_REPORT_DAYS, ledger_days,
                         "服务端的单次天数上限与账本窗口对不上")
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过 Worker 侧的天数上限检查")
        code = "console.log(require(process.argv[1]).MAX_REPORT_DAYS)"
        out = subprocess.run([node, "-e", code, str(VALIDATE_CJS)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(int(out.stdout.strip()), ledger_days,
                         "Worker 的单次天数上限与账本窗口对不上")

    def test_the_worker_and_the_server_agree_on_what_is_valid(self):
        """★ **跨语言对拍**：同一批用例跑 Worker 的 JS 与服务端的 Python，
        两边结论必须一致 —— 一边收一边拒 ⇒ 会有一边静默丢数据。"""
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过跨语言对拍")
        cfg = src.load_config(REGISTRY)
        cases = _cross_language_cases(cfg)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(cases, fh, ensure_ascii=False)
            path = fh.name
        try:
            code = ("const V=require(process.argv[1]);const R=require(process.argv[2]);"
                    "const cases=JSON.parse(require('fs').readFileSync(process.argv[3],'utf8'));"
                    "const today=process.argv[4];"
                    "console.log(JSON.stringify(cases.map("
                    "c=>V.validateReport(c,R.RULES,today).ok)));")
            out = subprocess.run([node, "-e", code,
                                  str(VALIDATE_CJS),
                                  str(RULES_CJS), path, TODAY],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            js_flags = json.loads(out.stdout.strip())
        finally:
            os.unlink(path)

        self.assertEqual(len(js_flags), len(cases))
        for idx, case in enumerate(cases):
            body = json.dumps(case, ensure_ascii=False)
            _days, err = cr.parse_report(body, cfg, TODAY)
            py_ok = (err is None)
            self.assertEqual(bool(js_flags[idx]), py_ok,
                             "第 %d 条两边结论不一致：JS 说 %s，Python 说 %s（%s）"
                             % (idx, js_flags[idx], py_ok, err))


if __name__ == "__main__":
    unittest.main()
