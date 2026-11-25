#!/usr/bin/env python3
"""把 worker/ 的源文件组装成 Cloudflare Pages 的部署目录（worker/pages/）。

    python3 worker/build_pages.py
    cd worker && wrangler pages deploy pages --project-name=pokemon-dex-ingest --branch=main

**为什么要这一步**
Cloudflare Pages 的 `_worker.js` 高级模式要求**入口文件在部署目录的根**，
而它 `import` 的 `lib/*.cjs` 必须跟它在一起。
手抄两份会漂（改了一处忘了另一处）⇒ 这里机械地复制，源只有 `worker/index.js` 与 `worker/lib/`。

**★ 为什么用 Pages 而不是 workers.dev**
`workers.dev` 这个域在**国内被 DNS 污染**（解析到 Facebook 的 IP 段）⇒ 客户端与 cron 都连不上
（2026-11-25 实测：6/6 超时，DNS 返回 157.240.20.8）。
而 `pages.dev` **通**（实测 200）⇒ 接收端改挂在 Pages 上，**代码一行不用改**。

**部署目录里只有两样东西**（`_worker.js` 与 `lib/`）⇒ 源不会跟着站点公开。
"""

from __future__ import annotations

import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "pages"


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "lib").mkdir(parents=True)

    # 入口：worker/index.js（`export default { fetch: handleRequest }`）
    # —— Pages 的 `_worker.js` 高级模式用的**正是这个形状**，所以原样复制即可。
    shutil.copy2(HERE / "index.js", OUT / "_worker.js")

    # 入口 import 的两个纯逻辑模块
    for name in ("validate.cjs", "rules.cjs"):
        shutil.copy2(HERE / "lib" / name, OUT / "lib" / name)

    print("已组装部署目录：%s" % OUT)
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print("  %s" % p.relative_to(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
