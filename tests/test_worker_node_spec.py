"""把 Worker 的 node 单测规格接进 Python 测试。

`run_tests.py` 是 python 的 unittest discovery，**只收集 `tests/test_*.py`** ⇒
Worker 那两份 node 规格（`worker_validate_spec.js` / `worker_handler_spec.mjs`）
在 CI 里没人跑 ⇒ 改坏了 Worker 的校验逻辑或端点行为，也不会有任何测试变红
（它们会静静烂掉）。这个文件把它们接进来：

    node 在  → 真的跑一遍，规格红了这里就红
    node 不在 → 跳过（不能让整套测试挂掉）

★ 这个测试**不能**只检查退出码：规格文件被清空 ⇒ node 报「没有断言」也是 0 ⇒ 一片绿。
  所以还要盯：① 输出里有成功标记；② 规格文件本身不能太短。
"""

import unittest

from tests import worker_spec_bridge as bridge


class WorkerNodeSpecTest(unittest.TestCase):
    """Worker 的两份 node 规格：接进 Python 测试。"""

    @classmethod
    def setUpClass(cls):
        cls.node = bridge.find_node()
        if not cls.node:
            raise unittest.SkipTest("找不到 node，跳过 Worker 的 node 规格")

    def test_the_node_spec_files_exist(self):
        """两份规格都得在（少一份 = 那部分逻辑没人测）。"""
        for name, _rel, _marker in bridge.NODE_SPECS:
            with self.subTest(规格=name):
                self.assertTrue(
                    bridge.spec_path(name).is_file(),
                    "缺少 node 规格：%s" % name,
                )

    def test_the_node_specs_are_not_empty(self):
        """规格不能被清空（清空了 node 也是绿的 ⇒ 等于没测）。"""
        for name, _rel, _marker in bridge.NODE_SPECS:
            with self.subTest(规格=name):
                self.assertGreaterEqual(
                    bridge.spec_line_count(name), bridge.MIN_SPEC_LINES,
                    "%s 只有 %d 行 —— 断言是不是被清空了？"
                    % (name, bridge.spec_line_count(name)),
                )

    def test_the_node_specs_keep_their_assertions(self):
        """★ 断言条数不能被悄悄删掉。

        `node 规格绿` 只说明「**还剩的**断言都过了」——
        有人把某几条断言删了，规格照样绿 ⇒ 那几件事就没人测了。
        所以把断言条数钉个下限（往上是允许的：加断言不受限）。
        """
        for name, _rel, _marker in bridge.NODE_SPECS:
            with self.subTest(规格=name):
                floor = bridge.MIN_ASSERTS.get(name, 0)
                count = bridge.spec_assert_count(name)
                self.assertGreaterEqual(
                    count, floor,
                    "%s 只剩 %d 条 assert. 断言（下限 %d）—— 有人删断言了？"
                    % (name, count, floor),
                )

    def test_the_worker_validate_spec_passes(self):
        """worker/lib/validate.cjs + rules.cjs 的纯校验逻辑。"""
        self._run_one("worker_validate_spec.js")

    def test_the_worker_handler_spec_passes(self):
        """worker/index.js 的四个端点 + CORS。"""
        self._run_one("worker_handler_spec.mjs")

    # -- 规格是不是「假绿」 -----------------------------------------------

    def test_the_validate_spec_catches_a_broken_implementation(self):
        """★ 把「量超过单日上限就拒收」拆掉 ⇒ 规格必须红。

        为什么要这一步：`node 规格绿` 只说明「还剩的断言都过了」。
        有人把断言删掉，规格照样绿 ⇒ 那份规格就没在测任何东西。
        ⇒ 改坏实现，规格必须红；不红 = 白测。
        """
        red, out = bridge.spec_catches_breakage(self.node, "worker_validate_spec.js")
        self.assertTrue(
            red,
            "把 validate.cjs 的单日上限拆掉后，规格居然还是绿的 —— "
            "它压根没在断言那件事\n%s" % out,
        )

    def test_the_handler_spec_catches_a_broken_implementation(self):
        """★ 把响应上的 CORS 头拆掉 ⇒ 规格必须红。"""
        red, out = bridge.spec_catches_breakage(self.node, "worker_handler_spec.mjs")
        self.assertTrue(
            red,
            "把 index.js 的 CORS 头拆掉后，规格居然还是绿的 —— "
            "它压根没在断言那件事\n%s" % out,
        )

    # -- 内部 -----------------------------------------------------------

    def _run_one(self, name: str):
        proc = bridge.run_spec(self.node, name)
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.returncode, 0,
            "node 规格 %s 没过（退出码 %d）\n%s" % (name, proc.returncode, out),
        )
        marker = dict((n, m) for n, _r, m in bridge.NODE_SPECS)[name]
        self.assertIn(
            marker, proc.stdout,
            "node 规格 %s 没有输出成功标记「%s」—— 规格可能被清空了\n%s"
            % (name, marker, out),
        )


if __name__ == "__main__":
    unittest.main()
