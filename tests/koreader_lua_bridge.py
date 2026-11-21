"""Python → fengari 的桥：让 Python 侧能直接问 Lua（report.lua）要答案。

沙箱里没有真的 Lua 解释器（Kindle 上才有），fengari 就是替身：
    node tests/koreader_lua_runner.js            跑断言集
    node tests/koreader_lua_runner.js --probe    再输出一行探针 JSON
    node tests/koreader_lua_runner.js --daily-sql 123   输出设备上真正执行的那条 SQL

**为什么抽出来**：现在有两个测试文件都要调 Lua（断言集那边要探针，
SQL 那边要 Lua 格式化出来的 SQL），各写一份「怎么找 node、怎么调 runner」
只会漂 —— 所以收在这里，一处改，两处跟着变。

每个函数都**不许抛异常**：node / fengari 缺失时由调用方决定跳过哪几条用例
（不能让整套测试挂掉）。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
RUNNER = TESTS / "koreader_lua_runner.js"
SPEC = TESTS / "koreader_lua_spec.lua"
FIXTURE = TESTS / "fixtures" / "koreader_days.txt"
WIRE_CASES = TESTS / "fixtures" / "koreader_wire_cases.txt"

# 每调一次 runner 都要起一个 node 进程 ⇒ 同一个 since 的 SQL 缓存一下
_daily_sql_cache: dict[int, str] = {}


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


def run_runner(node: str, *args: str) -> subprocess.CompletedProcess:
    """跑 fengari runner。**不用 shell=True**（参数直接进 argv）。"""
    return subprocess.run(
        [node, str(RUNNER), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


def run_probe(node: str) -> dict:
    """跑探针：让 Lua 侧把天数串过一遍，返回结构化结果。

    探针的输出里除了 fixture 的结果，还有 WIRE_CASES 里每一条线的结果
    （``cases``）—— 跨语言对拍要逐例比，不能只喂干净的 fixture。
    """
    proc = run_runner(node, "--probe")
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


def lua_daily_sql(node: str, since: int) -> str:
    """让 Lua 侧（report.lua 的 ``Report.daily_sql``）格式化出设备上真正执行的那条 SQL。

    走 runner 的 ``--daily-sql`` 模式：**原始字节**，不过 JSON ——
    JSON 转义一旦有 bug，SQL 就会被悄悄改掉，而我们要的是字节级一致。

    结果按 since 缓存（每调一次都要起一个 node 进程）。
    """
    key = int(since)
    if key in _daily_sql_cache:
        return _daily_sql_cache[key]
    proc = run_runner(node, "--daily-sql", str(key))
    if proc.returncode != 0:
        raise AssertionError(
            "取 daily_sql 失败（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr)
        )
    sql = proc.stdout
    _daily_sql_cache[key] = sql
    return sql


def read_wire_cases() -> list[str]:
    """读线格式的用例文件（一行一条线）。

    **不 trim 每一行** —— 带空白的行本身就是用例（规则是「段不完全匹配就整段丢掉」）。
    只去掉文件末尾那一个换行符（文件末尾换行是文本文件的常规，不是内容）。
    """
    text = WIRE_CASES.read_text(encoding="utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return []
    return text.split("\n")
