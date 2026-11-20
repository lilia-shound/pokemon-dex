#!/usr/bin/env python3
"""从 data/pokedex.json 生成番茄钟用的精简宝可梦清单。

    python3 scripts/build_pokedex_lite.py

**为什么要精简版**：`data/pokedex.json` 有 1.8 MB（含图鉴页要用的简介、能力、进化链）。
番茄钟在手机上跑，只要「抽卡用得上的那几个字段」，1.8 MB 在弱网下会拖慢首屏。

输出 `site/pomodoro/pokedex-lite.json`（约 107 KB），字段：
    id / nameZh / art / artShiny / types
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pokedex.json"
OUT = ROOT / "site" / "pomodoro" / "pokedex-lite.json"

KEEP = ("id", "nameZh", "art", "artShiny", "types")


def build(pokedex: dict) -> dict:
    """挑出要留的字段。"""
    out = []
    for p in pokedex.get("pokemon") or []:
        row = {k: p.get(k) for k in KEEP if p.get(k) is not None}
        if row.get("art"):
            out.append(row)
    return {"count": len(out), "pokemon": out}


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    lite = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(lite, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    size = OUT.stat().st_size
    print(f"已生成 {OUT.relative_to(ROOT)}：{lite['count']} 只 · {size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
