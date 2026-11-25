"""Python → node 的桥：让 Python 侧能跑 Worker 的 node 单测规格。

仓库里有两份 node 规格（Worker 侧的东西不是 Python 写的，用 node 测最直接）：
    node tests/worker_validate_spec.js    —— worker/lib/validate.cjs + rules.cjs 的纯校验逻辑
    node tests/worker_handler_spec.mjs    —— worker/index.js 的四个端点 + CORS

**为什么要有这个桥**：`run_tests.py` 是 python 的 unittest discovery，
它**只收集 `tests/test_*.py`** ⇒ 这两份 node 规格**在 CI 里没人跑**，
改坏了 Worker 的校验逻辑也不会有人发现（它们会静静烂掉）。
⇒ 把它们接进 Python 测试，node 在就跑、node 不在就跳过。

每个函数都**不许抛异常**：node 缺失时由调用方决定跳过（不能让整套测试挂掉）。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

from tests import koreader_lua_bridge

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

#: 要接进 Python 测试的 node 规格：(显示名, 相对路径, 期望输出里出现的成功标记)
NODE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("worker_validate_spec.js", "tests/worker_validate_spec.js", "全部通过"),
    ("worker_handler_spec.mjs", "tests/worker_handler_spec.mjs", "全部通过"),
)

#: 规格文件至少要有这么多行 —— 防止有人把断言清空 ⇒ 一片绿但什么都没测
MIN_SPEC_LINES = 40

#: 每份规格至少要有多少条 `assert.` 断言 —— **防止有人悄悄删断言**。
#:
#: ★ 单靠「规格绿」不够：删掉一条断言，规格照样绿 ⇒ 那一件事就没人测了。
#:   下限**就取当前的实际条数**（31 / 70）：往上加断言不受限，往下删一条就红。
#:   与 `spec_catches_breakage` 一起用：那条证明「断言还活着」，这条证明「断言一条没少」。
MIN_ASSERTS: dict[str, int] = {
    "worker_validate_spec.js": 31,
    "worker_handler_spec.mjs": 70,
}

#: 每份规格配一个「把实现改坏」的改法，用来**证明这份规格真在断言**。
#:
#: ★ 为什么要这一步：`node 规格红了` 只说明「规格文件里还剩的断言都通过了」。
#:   有人把断言删掉几行，规格照样绿 ⇒ node 这层是**假绿**的。
#:   ⇒ 把实现改坏一份，跑规格的**副本** ⇒ 规格应该红；不红 = 它压根没在测那件事。
#:
#: 格式：(要改坏的实现文件, 旧串, 新串) —— 路径相对仓库根。
BREAKS: dict[str, tuple[str, str, str]] = {
    # 拆掉「量超过单日上限就拒收」⇒ 规格里「1441 应被拒」那条必须红
    "worker_validate_spec.js": (
        "worker/lib/validate.cjs",
        "if (amount > cap) {",
        "if (false) {",
    ),
    # 拆掉响应上的 CORS 头 ⇒ 规格里那几条 CORS 断言必须红
    "worker_handler_spec.mjs": (
        "worker/index.js",
        "}, CORS_HEADERS),",
        "}),",
    ),
}


def find_node() -> str | None:
    """找 node。复用 koreader_lua_bridge 那份（别写第二套）。"""
    return koreader_lua_bridge.find_node()


def spec_path(name: str) -> pathlib.Path:
    """规格文件的绝对路径。"""
    return ROOT / "tests" / name


def run_spec(node: str, name: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """跑一份 node 规格。**不用 shell=True**（参数直接进 argv）。"""
    return subprocess.run(
        [node, str(spec_path(name))],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )


def spec_line_count(name: str) -> int:
    """规格文件有多少行。"""
    try:
        return len(spec_path(name).read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def spec_assert_count(name: str) -> int:
    """规格文件里有多少条 ``assert.`` 断言。"""
    try:
        text = spec_path(name).read_text(encoding="utf-8")
    except OSError:
        return 0
    return text.count("assert.")


def spec_catches_breakage(node: str, name: str, timeout: int = 120) -> tuple[bool, str]:
    """把 Worker 的实现改坏一份，跑规格的**副本** ⇒ 规格应该红。

    返回 ``(是否真的红了, 输出尾巴)``。
    ``False`` = 改坏了实现规格都没红 ⇒ 这份规格**没在测那件事**（白测了）。

    实现办法：把 ``worker/`` 与那份规格复制到临时目录（**保持相对布局** ——
    规格用 ``../worker/index.js`` 导入入口）⇒ 在副本上改坏 ⇒ 跑副本。
    仓库里的文件一个字节都不动。
    """
    if name not in BREAKS:
        raise ValueError("没有给 %s 配「改坏实现」的办法" % name)
    rel_impl, old, new = BREAKS[name]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="wspec-"))
    try:
        shutil.copytree(ROOT / "worker", tmp / "worker")
        (tmp / "tests").mkdir()
        shutil.copy2(spec_path(name), tmp / "tests" / name)
        impl = tmp / rel_impl
        text = impl.read_text(encoding="utf-8")
        if old not in text:
            return False, "改坏失败：%s 里找不到要替换的那段（实现变了？）" % rel_impl
        impl.write_text(text.replace(old, new, 1), encoding="utf-8")
        proc = subprocess.run(
            [node, str(tmp / "tests" / name)],
            cwd=str(tmp), capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode != 0, out[-2000:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

