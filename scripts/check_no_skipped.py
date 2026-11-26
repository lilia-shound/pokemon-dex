#!/usr/bin/env python3
"""检查测试输出里有没有测试被**静默跳过**。

★ 为什么要有这个：
  unittest 里「跳过」**不是失败** —— 依赖没装好时（fengari / 素材缓存 / playwright）
  那几条会 skip 而不是 error ⇒ CI 全绿，但**少测了几十条** ⇒ 等于没测。
  2026-11-26 第一次接 CI 就踩到：本地 886 条，CI 只跑 858 条，**照样是绿的**。

⇒ 所以 CI 跑完测试要显式问一句「有没有被跳过的」。

用法：
    python3 -B run_tests.py 2>&1 | tee test.log
    python3 scripts/check_no_skipped.py test.log
"""

from __future__ import annotations

import re
import sys

# unittest 的成功行：行首的 "OK"（有 skip 时会写成 "OK (skipped=3)"）
OK_RE = re.compile(r"^OK\b", re.M)
SKIPPED_RE = re.compile(r"skipped=(\d+)")


def check(text: str) -> tuple[bool, str]:
    """看一段测试输出 ⇒ 返回（是否通过, 说明）。

    @param {str} text 测试输出（整个 stdout + stderr）
    @returns {tuple[bool, str]}
    """
    if not OK_RE.search(text):
        return False, "测试没全过（日志里没有行首的 OK）"
    m = SKIPPED_RE.search(text)
    # ★ 没有 "skipped=" 这个串 ⇒ unittest **根本没跳过任何东西**
    #   （unittest 只在有 skip 时才打印 "OK (skipped=N)"）
    n = int(m.group(1)) if m else 0
    if n:
        return False, (
            "有 %d 条测试被跳过 —— 依赖装好了吗？"
            "（fengari / 宝可梦素材缓存 / playwright 的 chromium）" % n
        )
    return True, "全部测试都跑了，没跳过。"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法：check_no_skipped.py <测试输出日志>")
        return 2
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        text = f.read()
    ok, msg = check(text)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
