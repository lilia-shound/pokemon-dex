#!/usr/bin/env python3
"""生成图鉴页用的 pokedex.json —— 把运行时用不到的字段去掉。

    python3 scripts/build_site_pokedex.py

**为什么要瘦身**：`data/pokedex.json` 是 1.6 MB，其中光一个 `profile` 字段就占 1.2 MB（74%）。
而图鉴页和插件**一个字段都没用到**（`profile` / `summary` / `nameEn` / `artSource` /
`flavorSource` 只有素材管线 `fetch_pokemon.py` 写、没人读）。

大文件在慢链路上是实打实的痛：实测同一台机器上
- curl 拿 898KB：约 1 秒
- 浏览器里拿同一个文件：53 秒

⇒ 首屏多等一分钟，白屏到超时。瘦身到 ~280 KB 是最省事、零风险的一刀
（读方要的字段一个不少，字段名也不变）。

**`data/pokedex.json` 保持完整** —— 它是素材管线的产物，字段全留着。
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pokedex.json"
OUT = ROOT / "site" / "pokedex.json"

# 读方（图鉴页 site/app.js + 油猴插件）真正用到的字段
KEEP = (
    "id", "nameZh", "nameJa", "types", "category", "abilities",
    "height", "weight", "evolution", "flavor",
    "art", "artShiny", "preview", "provisionalNo",
)


def build(pokedex: dict) -> dict:
    """只保留 KEEP 里的字段，缺字段的条目直接不带那个键。"""
    out = []
    for p in pokedex.get("pokemon") or []:
        row = {}
        for k in KEEP:
            v = p.get(k)
            if v is None:
                continue
            if isinstance(v, (list, str)) and len(v) == 0:
                continue
            row[k] = v
        out.append(row)
    return {"count": pokedex.get("count") or len(out), "pokemon": out}


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    lite = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(lite, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    src_size = SRC.stat().st_size
    out_size = OUT.stat().st_size
    print(f"已生成 {OUT.relative_to(ROOT)}：{lite['count']} 只 · "
          f"{src_size / 1024:.0f} KB → {out_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
