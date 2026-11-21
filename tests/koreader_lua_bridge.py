"""Python → fengari 的桥：让 Python 侧能直接问 Lua（koreader_dex_report.lua）要答案。

沙箱里没有真的 Lua 解释器（Kindle 上才有），fengari 就是替身：
    node tests/koreader_lua_runner.js            跑断言集
    node tests/koreader_lua_runner.js --probe    再输出一行探针 JSON
    node tests/koreader_lua_runner.js --daily-sql 123   输出设备上真正执行的那条 SQL
    node tests/koreader_lua_runner.js --window <时间戳> <天数>   问日历窗口的上下界

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
    """让 Lua 侧（koreader_dex_report.lua 的 ``Report.daily_sql``）格式化出设备上真正执行的那条 SQL。

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


def lua_compile(node: str, path: pathlib.Path) -> subprocess.CompletedProcess:
    """只**编译**一个 Lua 文件（不执行）—— 抓 main.lua 里的低级错误。

    走 runner 的 ``--syntax`` 模式：``luaL_loadstring`` 只编译不执行 ⇒
    顶层的 ``require`` 不会被调用（沙箱里根本没有 KOReader 的模块）。
    """
    return run_runner(node, "--syntax", str(path))


def lua_collect(node: str, fn: str, cols: list[list]) -> dict:
    """让 Lua 侧（koreader_dex_report.lua 的 ``Report.collect_days`` / ``Report.collect_pages``）
    处理一份「查询结果」，返回 ``{日期: 数字}``。

    ``cols`` 是**「每一列一个数组」**的形状 —— 正是 ljsqlite3 的
    ``conn:exec(sql)`` 返回的那种（不是行数组）。

    走 runner 的 ``--collect-days`` 模式：结果表由 runner 塞进 Lua 的
    全局 ``RES_TABLE``，再把返回值序列化成 JSON 吐回来。
    """
    payload = json.dumps({"fn": fn, "cols": cols})
    proc = run_runner(node, "--collect-days", payload)
    if proc.returncode != 0:
        raise AssertionError(
            "%s 调用失败（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
            % (fn, proc.returncode, proc.stdout, proc.stderr)
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "%s 没有返回 JSON：%s\nstdout:\n%s" % (fn, exc, proc.stdout)
        ) from exc


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


def strip_lua_comments(text: str) -> str:
    """去掉 Lua 的注释，只留**代码**。

    静态检查要用它：**注释里提到某个 API 不算数**，代码里真的用了才算。
    （比如插件主体里会写「`require("report")` 这种通用名会被别的插件抢走」——
    这种说明性的注释不该被当成「真的 require 了 report」。）

    处理两种注释：
      - `--[[ ... ]]` 长注释（含嵌套的写法不管）；
      - `--` 到行尾。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("--[[", i):
            end = text.find("]]", i + 4)
            i = n if end == -1 else end + 2
            continue
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def lua_window(node: str, now_stamp: int, count: int) -> dict:
    """让 Lua 侧算日历窗口的下界/上界（跨语言对拍用）。

    走 runner 的 ``--window`` 模式：把「当前时刻」当成**参数**传进去
    （``Report.window_lower(now_stamp, count)`` / ``Report.window_upper(now_stamp)``），
    所以结果与跑测试的机器的当前时间无关。

    时区：runner 固定成 UTC（``KOREADER_TEST_TZ`` 可覆盖），
    Python 侧要算「同一个本地日」也得用 UTC（见 tests/test_koreader_lua.py）。
    """
    proc = run_runner(node, "--window", str(int(now_stamp)), str(int(count)))
    if proc.returncode != 0:
        raise AssertionError(
            "取 window_bounds 失败（退出码 %d）\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr)
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "window_bounds 没有返回 JSON：%s\nstdout:\n%s" % (exc, proc.stdout)
        ) from exc
