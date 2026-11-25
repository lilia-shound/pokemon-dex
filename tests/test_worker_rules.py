"""Worker 的限幅表不许跟服务端注册表漂移；时区口径也要对得上。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_rules
"""

from __future__ import annotations

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
REGISTRY = ROOT / "data" / "sources.json"


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


class WorkerRulesTest(unittest.TestCase):
    """限幅表 = 源白名单 = 单日上限。"""

    def test_the_rules_file_exists(self):
        self.assertTrue(RULES_CJS.exists(), "缺 %s" % RULES_CJS)

    def test_the_rules_match_the_server_side_registry(self):
        """★ 限幅表与 data/sources.json 的 maxPerDay 必须逐字一致。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        sources = cfg.get("sources") or {}
        rules = _rules()
        # 白名单 = 注册表里所有 push 源（不多不少）
        push_sources = sorted(sid for sid, s in sources.items() if s.get("mode") == "push")
        self.assertEqual(sorted(rules.keys()), push_sources,
                         "限幅表的白名单与注册表里的 push 源对不上")
        for sid, cap in rules.items():
            self.assertEqual(sources[sid].get("maxPerDay"), cap,
                             "源 %s 的限幅漂了：Worker 说 %s，注册表说 %s"
                             % (sid, cap, sources[sid].get("maxPerDay")))

    def test_the_rules_only_cover_push_sources(self):
        """白名单里不该出现 pull 源（微信读书是服务端自己去拉的）。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        sources = cfg.get("sources") or {}
        for sid in _rules():
            self.assertEqual(sources[sid].get("mode"), "push",
                             "源 %s 不是 push 源，不该在投递白名单里" % sid)

    def test_the_registry_has_the_ingest_block(self):
        """注册表顶层要有 ingest 段（客户端从这儿读 URL 与 token）。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
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
        out = subprocess.run([node, "-e", code, str(ROOT / "worker" / "lib" / "validate.cjs")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "2026-11-26", "17:30Z 已经是北京时间的第二天了")
        self.assertEqual(lines[1], "2026-11-25", "15:30Z 还是北京时间当天")

    def test_the_worker_and_the_server_agree_on_what_is_valid(self):
        """★ **跨语言对拍**：同一批用例跑 Worker 的 JS 与服务端的 Python，
        两边结论必须一致 —— 一边收一边拒 ⇒ 会有一边静默丢数据。"""
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过跨语言对拍")
        today = "2026-11-25"
        cases = [
            {"v": 1, "source": "pom", "days": {"2026-11-21": 37}},     # 收
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1440}},   # 收（正好上限）
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1441}},   # 拒（超上限）
            {"v": 1, "source": "pom", "days": {}},                      # 拒（没有天数）
            {"v": 1, "source": "pom", "days": {"2026-11-21": -1}},     # 拒（负数）
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1.5}},    # 拒（不是整数）
            {"v": 1, "source": "pom", "days": {"2026-11-26": 5}},      # 拒（未来）
            {"v": 1, "source": "pom", "days": {"2026-13-01": 5}},      # 拒（假日期）
            {"v": 1, "source": "weread", "days": {"2026-11-21": 37}},  # 拒（pull 源）
            {"v": 1, "source": "words", "days": {"2026-11-21": 500}},  # 收（正好上限）
            {"v": 1, "source": "words", "days": {"2026-11-21": 501}},  # 拒（超上限）
            {"v": 2, "source": "pom", "days": {"2026-11-21": 37}},     # 拒（版本不对）
        ]
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
                                  str(ROOT / "worker" / "lib" / "validate.cjs"),
                                  str(RULES_CJS), path, today],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            js_flags = json.loads(out.stdout.strip())
        finally:
            os.unlink(path)

        cfg = src.load_config(REGISTRY)
        self.assertEqual(len(js_flags), len(cases))
        for idx, case in enumerate(cases):
            body = json.dumps(case, ensure_ascii=False)
            _days, err = cr.parse_report(body, cfg, today)
            py_ok = (err is None)
            self.assertEqual(bool(js_flags[idx]), py_ok,
                             "第 %d 条两边结论不一致：JS 说 %s，Python 说 %s（%s）"
                             % (idx, js_flags[idx], py_ok, err))


if __name__ == "__main__":
    unittest.main()
