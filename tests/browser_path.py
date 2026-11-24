"""找 Chromium 可执行文件。

**别把版本号写死** —— 沙箱刷新后 Playwright 会换版本：
2026-11-23 实测从 `chromium-1243/chrome-linux/chrome` 变成
`chromium-1255/chrome-linux64/chrome`（**目录名都变了**）⇒ 写死的路径会让
所有浏览器测试直接挂掉（`BrowserType.launch: executable doesn't exist`）。

⇒ 这里动态找：先扫 Playwright 缓存里**版本号最大**的那个，
再退到系统装的 chromium / google-chrome。
"""

from __future__ import annotations

import glob
import os
import re

# 系统里可能装的浏览器（Playwright 缓存没有时的兜底）
SYSTEM_CANDIDATES = [
    "/usr/local/bin/chromium",
    "/usr/local/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]

_VERSION_RE = re.compile(r"chromium-(\d+)")


def _version_of(path: str) -> int:
    """从路径里抠出版本号（chromium-1255/... → 1255）；抠不到当 0。"""
    for part in path.split(os.sep):
        m = _VERSION_RE.fullmatch(part)
        if m:
            return int(m.group(1))
    return 0


def chromium_path() -> str:
    """返回可用的 Chromium 可执行文件路径。找不到就抛错（错误信息要说清怎么修）。"""
    # 1) Playwright 缓存：chromium-<版本>/chrome-linux*/chrome（目录名两个版本都出现过）
    found = [
        p for p in glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")
        if os.path.isfile(p) and os.access(p, os.X_OK)
    ]
    if found:
        return max(found, key=_version_of)

    # 2) 系统装的
    for path in SYSTEM_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise RuntimeError(
        "找不到可用的 Chromium。\n"
        "  试过：/root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome\n"
        "        %s\n"
        "  装一个：python3 -m playwright install chromium"
        % "、".join(SYSTEM_CANDIDATES)
    )


CHROME = chromium_path()
