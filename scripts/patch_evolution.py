#!/usr/bin/env python3
"""一次性补丁：给 `data/pokedex.json` 的每只宝可梦重写 `evolution` / `evolutionStage`。

    python3 scripts/patch_evolution.py --src <42arch 的 data/pokemon 目录> [--dry-run]

**为什么单独写一个补丁脚本，而不是重跑 `fetch_pokemon.py`**：
重跑管线要 clone 整个上游仓库 + 解析 1025 个 JSON + 逐个去 PokeAPI 兜底文案，
慢且容易在网络抖动时半途失败 ⇒ 数据文件被写坏。
补丁脚本只动两个字段（`evolution` / `evolutionStage`），
**flavor / art / types / height / … 一个字节都不碰** ⇒ 风险小得多。

★ `fetch_pokemon.py` 本身也已经改成用 `evo_tree` 算这两个字段
  （所以以后真跑管线，产出的结果和这个补丁一致）—— 本脚本是「不重跑管线的等价路径」。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import evo_tree as et  # noqa: E402

POKEDEX_PATH = ROOT / "data" / "pokedex.json"


def patch(pokedex: dict, tree: dict) -> tuple[int, int, list[str]]:
    """重写每条的 evolution / evolutionStage。返回（改了几条、没找到树的、缺失的名字）。"""
    changed = 0
    missing: list[str] = []
    for p in pokedex.get("pokemon") or []:
        name = p.get("nameZh")
        if name not in tree["root_of"]:
            missing.append(name)
            continue
        chain = et.evolution_array(tree, name)
        stage = et.stage_of(tree, name)
        if p.get("evolution") != chain or p.get("evolutionStage") != stage:
            p["evolution"] = chain
            p["evolutionStage"] = stage
            changed += 1
    return changed, len(missing), missing


def main() -> int:
    ap = argparse.ArgumentParser(description="重写 pokedex.json 的进化字段")
    ap.add_argument("--src", required=True, help="42arch 的 data/pokemon 目录")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    src_dir = pathlib.Path(args.src)
    if not src_dir.is_dir():
        print(f"[失败] 目录不存在：{src_dir}")
        return 1

    pokedex = json.loads(POKEDEX_PATH.read_text(encoding="utf-8"))
    species = et.load_species_from_dir(src_dir)
    if not species:
        print(f"[失败] {src_dir} 里没有 JSON")
        return 1

    # 预告条目（上游没收录）：各自独立成链
    extra = [p["nameZh"] for p in pokedex.get("pokemon") or []
             if p.get("preview") and p.get("nameZh")]
    tree = et.build_tree(species, extra)
    print(f"[管线] 上游 {len(species)} 只 + 预告 {len(extra)} 只 -> {len(tree['members'])} 条链")

    changed, missing_count, missing = patch(pokedex, tree)
    print(f"[管线] 改了 {changed} 条；图鉴里有 {missing_count} 只在树上找不到"
          + (f"：{', '.join(missing[:10])}" if missing else ""))

    # 一致性自检：同链成员的 evolution 数组必须逐字相同
    by_root: dict[str, list[str]] = {}
    for p in pokedex.get("pokemon") or []:
        name = p.get("nameZh")
        root = tree["root_of"].get(name)
        if root:
            by_root.setdefault(root, []).append(name)
    inconsistent = []
    for root, members in by_root.items():
        arrays = {json.dumps(next(p for p in pokedex["pokemon"]
                                  if p["nameZh"] == m).get("evolution"),
                             ensure_ascii=False) for m in members}
        if len(arrays) > 1:
            inconsistent.append(root)
    if inconsistent:
        print(f"[失败] {len(inconsistent)} 条链的成员 evolution 数组不一致："
              f"{', '.join(sorted(inconsistent)[:10])}")
        return 1
    print(f"[自检] {len(by_root)} 条链的成员 evolution 数组全部一致 ✓")

    if args.dry_run:
        print("[dry-run] 不写文件")
        return 0

    POKEDEX_PATH.write_text(
        json.dumps(pokedex, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[完成] 已写入 {POKEDEX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
