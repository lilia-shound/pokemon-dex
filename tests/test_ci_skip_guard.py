"""CI 的「不许有测试被静默跳过」检查 —— 真跑一遍那个脚本。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_ci_skip_guard

★ 为什么要有这条：
  2026-11-26 第一次接 CI 时，它**绿了**，但只跑了 858 条（本地 886）
  ⇒ 少了 28 条（fengari 的 Lua 单测 19 条 + 素材缓存的真实数据测试 9 条）
  —— 因为 unittest 里「跳过」**不是失败** ⇒ 依赖没装好就静悄悄少测了几十条。

⇒ 检查脚本本身也得被测（不然它写错了，CI 就永远红或者永远绿）。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_no_skipped.py"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


class CheckNoSkippedTest(unittest.TestCase):
    """`scripts/check_no_skipped.py` 的行为断言。"""

    def test_the_script_exists(self):
        self.assertTrue(SCRIPT.exists(), "跳过检查脚本不见了")

    def test_a_clean_run_passes(self):
        """★ unittest 零 skip 时输出的是光秃秃的 `OK`（**没有** skipped=）⇒ 必须认得。"""
        ok, msg = self._check("Ran 886 tests in 131.985s\n\nOK\n")
        self.assertTrue(ok, msg)

    def test_a_run_with_skips_fails(self):
        """★ 有跳过 ⇒ 必须红（哪怕 unittest 自己说 OK）。"""
        ok, msg = self._check("Ran 858 tests in 60s\n\nOK (skipped=14)\n")
        self.assertFalse(ok, "有 14 条被跳过却判通过了")
        self.assertIn("14", msg)

    def test_a_failed_run_fails(self):
        """测试没过 ⇒ 也要红（不能只看 skip 数）。"""
        ok, msg = self._check("Ran 10 tests in 1s\n\nFAILED (failures=1)\n")
        self.assertFalse(ok, "测试没过却判通过了")

    def test_an_empty_log_fails(self):
        """日志是空的 ⇒ 不能判通过（那会让 CI 空转）。"""
        ok, msg = self._check("")
        self.assertFalse(ok, "空日志却判通过了")

    def test_the_workflow_actually_calls_it(self):
        """★ workflow 必须真的调它（不然这个脚本只是摆设）。"""
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("check_no_skipped.py", text,
                      "CI 没调跳过检查脚本 ⇒ 少测几十条也会是绿的")

    # -- 辅助 -----------------------------------------------------------

    def _check(self, text: str):
        """在子进程里真跑一次那个脚本（喂一份假的测试输出）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            log = pathlib.Path(td) / "test.log"
            log.write_text(text, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), str(log)],
                capture_output=True, text=True, timeout=60,
            )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


if __name__ == "__main__":
    unittest.main()
