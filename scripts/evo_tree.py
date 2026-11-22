#!/usr/bin/env python3
"""进化树：把 42arch 的「多条 path」还原成一棵树，并算出每只的阶。

**为什么需要这个模块**：上游 `evolution_chains` 里每一项是一条**从根出发的路径**
（不是一棵树），一只宝可梦可能出现在多条路径里（伊布 = 8 条分支）。
`fetch_pokemon.py` 早期把多条路径**首尾相接**拼成一条数组 ⇒
皮卡丘的链变成 `['皮丘','皮卡丘','雷丘','皮丘','皮卡丘','雷丘']`（重复！），
伊布的 8 个分支被当成 8 个连续阶。

这个模块从**全库**的视角重建树：
- 每条 path 是一条有序链（`from` 字段记录父节点）
- 父节点用**全库多数票**判定（少数文件写错时不至于把整条链拆开）
- 每只的「阶」（stage）由它在树上的**深度**决定，而不是上游那个会打架的 stage 字段

**纯逻辑，不碰网络、不碰文件** —— 输入是解析好的 JSON 片段，输出是结构化的树。
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter, defaultdict

# 上游 stage 字段的中文取值 -> 阶数。
# 「未进化 / 幼年 / 不进化 / 无进化 / 亲代」都算第 0 阶（链的起点或独立个体）。
STAGE_MAP: dict[str, int] = {
    "未进化": 0,
    "幼年": 0,
    "不进化": 0,
    "无进化": 0,
    "亲代": 0,
    "1阶进化": 1,
    "2阶进化": 2,
}

# ★ 「亲代」与「未进化」都是第 0 阶，但它们在一条链里的**先后**不同：
#   玛纳霏（亲代）→ 霏欧纳（未进化）—— 亲代是起点，未进化是它的孩子。
#   所以判「父节点方向对不对」时要用这个**排序键**，不能用阶数（两者阶数相同）。
STAGE_ORDER: dict[str, int] = {
    "亲代": 0,
    "未进化": 1,
    "幼年": 2,
    "1阶进化": 3,
    "2阶进化": 4,
    "不进化": 5,
    "无进化": 6,
}


def stage_value(label: str | None) -> int:
    """上游 stage 标签 -> 阶数。未知标签按 0 阶处理（宁可当起点，别当进阶）。"""
    return STAGE_MAP.get(str(label or ""), 0)


def _paths_from_species(src: dict) -> list[list[dict]]:
    """从一只宝可梦的原始 JSON 里取出它的所有进化路径。

    上游的 `evolution_chains` 是「路径的列表」；每条路径是一串节点。
    同一个名字可能在多条路径里出现（分支）。
    """
    out: list[list[dict]] = []
    for link in src.get("evolution_chains") or []:
        if not link:
            continue
        nodes = [n for n in link if n.get("name")]
        if nodes:
            out.append(nodes)
    return out


def collect_edges(species: list[dict]) -> tuple[dict[str, str], dict[str, set[int]]]:
    """扫全库，收集「谁是谁的父节点」与「每只出现过的阶」。

    **父节点怎么定**（这里踩过坑，写清楚免得以后改错）：

    1. **path 内相邻关系是硬事实** —— 一条 path 是有序链，
       第 i 个节点的 `from` 必然是第 i-1 个节点的名字（上游就是这么生成的）。
       所以「A 在某条 path 里紧跟在 B 后面」⇒ **B 就是 A 的父节点**，票数 +1。
       ★ 只用「节点自己的 from 字段」是不够的：链的中间节点在自己的文件里
       往往**不出现**（它只出现在链根的文件里）⇒ 没有票 ⇒ 链被拆成
       「皮丘」「皮卡丘→雷丘」两截（实测 905 条链，伊布组只剩自己）。
    2. **跨 path 冲突用多数票** —— 少数文件的 `from` 写错时
       （实测有 12 只把自己的 from 写成 None，少数 path 还把父节点写串了），
       多数票能让整条链保持在一起。
    3. 平票取**字典序最小**的父节点名 ⇒ 结果确定、可复现。

    返回：
        parents: 名字 -> 父节点名（没有父节点则不在这个 dict 里）
        stages:  名字 -> 出现过的阶数集合
    """
    votes: dict[str, Counter] = defaultdict(Counter)
    raw_stages: dict[str, set[int]] = defaultdict(set)
    order_votes: dict[str, Counter] = defaultdict(Counter)

    # ---- 第一遍：只收 stage 标签（排序键要全库齐了才能判方向）----
    paths: list[list[dict]] = []
    for src in species:
        for path in _paths_from_species(src):
            paths.append(path)
            for node in path:
                raw_stages[node["name"]].add(stage_value(node.get("stage")))
                order_votes[node["name"]][str(node.get("stage") or "")] += 1

    def order_of(name: str) -> int:
        """stage 排序键（越小越靠前）。亲代 < 未进化 < 幼年 < 1阶 < 2阶。"""
        counter = order_votes.get(name)
        if not counter:
            return 99
        return min(STAGE_ORDER.get(label, 99) for label in counter)

    # ---- 第二遍：收集父子边 ----
    for path in paths:
        # ★ 整条 path 的顺序可能是**反的**（上游写的是「霏欧纳 → 玛纳霏」，
        #   可现实里是玛纳霏生霏欧纳）。判据：排序键应当**单调不降**；
        #   如果单调**递减**，说明这条 path 写反了 ⇒ 把它倒过来。
        orders = [order_of(n["name"]) for n in path]
        if len(orders) >= 2 and all(orders[i] > orders[i + 1]
                                    for i in range(len(orders) - 1)):
            path = list(reversed(path))
        for idx, node in enumerate(path):
            name = node["name"]
            # (1) path 内相邻：前一个节点**通常**就是父节点
            #     ★ 但要先看排序键 —— 顺序反的 path 上面已经翻过来了，
            #       这里再挡一次「父的排序键不比自己小」的边
            if idx > 0:
                prev = path[idx - 1]["name"]
                if prev != name and order_of(prev) < order_of(name):
                    votes[name][prev] += 1
            # (2) 节点自报的 from：跨 path 冲突时靠它投票
            parent = node.get("from")
            if parent and parent != name and order_of(parent) < order_of(name):
                votes[name][parent] += 1

    parents: dict[str, str] = {}
    for name, counter in votes.items():
        if not counter:
            continue
        # 多数票；平票取字典序最小 ⇒ 确定
        best = min(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        parents[name] = best[0]

    return parents, dict(raw_stages)


def build_tree(species: list[dict], extra_names: list[str] | None = None) -> dict:
    """重建进化树。

    `extra_names`：**上游没有收录、但本地数据里有**的条目（预告世代）。
    它们各自算一条独立链（自己是根、阶 0）—— 不给它们编造进化关系。

    返回：
        {
          "root_of":   {名字: 链根名},
          "depth_of":  {名字: 阶数（根=0）},
          "members":   {链根名: [成员名, ...]}（按阶排序，同阶按名排序）,
          "parent_of": {名字: 父节点名},
        }
    """
    parents, raw_stages = collect_edges(species)
    names = set(parents) | set(raw_stages)

    # 预告条目（上游没有）：各自独立成链
    for n in extra_names or []:
        if n not in names:
            names.add(n)
            raw_stages.setdefault(n, {0})

    # ---- 求每个节点的根（沿 parent 往上走；遇环就停在那儿）----
    root_of: dict[str, str] = {}
    for name in names:
        # chain 是「从自己往上」的一串：chain[0]=自己，chain[-1]=根
        chain: list[str] = []
        cur = name
        seen: set[str] = set()
        while cur in parents and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parents[cur]
        # ★ 根 = 往上走完之后停下的那个节点（chain 里没有 parent 的那个）
        #   chain[0]=自己、chain[-1]=离根最近的那个 ⇒ 根在 chain 之外 = cur
        root_of[name] = cur

    # ---- 深度：优先用「从根到该节点的路径长度」，路径长度和 stage 投票取一致者 ----
    depth_of: dict[str, int] = {}
    for name in names:
        chain: list[str] = []
        cur = name
        # ★ 必须有环保护：父节点是全库投票的结果，数据脏时可能出现环
        #   （A 的父是 B、B 的父是 A）⇒ 不加 seen 检查就会无限循环、
        #   内存爆掉被 OOM killer 杀掉（rc=137）。
        seen: set[str] = set()
        while cur in parents and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parents[cur]
        chain.append(cur)          # 链根（或环上的落点）
        depth = len(chain) - 1     # 根=0
        stage_votes = raw_stages.get(name) or {0}
        voted = max(stage_votes) if stage_votes else 0
        # 两者不一致时取**较小**的：宁可低估阶数，也不要把兄弟形态排成连续阶
        depth_of[name] = min(depth, voted) if depth != voted else depth

    # ---- 组内成员：按 (阶, 名) 排序 ----
    members: dict[str, list[str]] = defaultdict(list)
    for name in names:
        members[root_of[name]].append(name)
    for root in members:
        members[root].sort(key=lambda n: (depth_of[n], n))

    return {
        "root_of": root_of,
        "depth_of": depth_of,
        "members": {k: list(v) for k, v in members.items()},
        "parent_of": dict(parents),
    }


def evolution_array(tree: dict, name: str) -> list[str]:
    """某只宝可梦的 `evolution` 数组 —— **同链所有成员拿到完全一致的一份**。

    这是 S6 的关键修复：以前同一链的成员拿到的数组不一致
    （走路草 6 个名字、霸王花只有 3 个），页面显示会错，链分组也会被拆开。

    数组内容 = 该链的全部成员（按阶排序）。
    """
    root = tree["root_of"].get(name)
    if root is None:
        return []
    return list(tree["members"].get(root) or [])


def stage_of(tree: dict, name: str) -> int:
    """某只宝可梦在自己链上的阶数（根=0）。"""
    return int(tree["depth_of"].get(name, 0))


def stage_for_chain(tree: dict, chain_root: str) -> int:
    """一条链最深能到的阶数。"""
    members = tree["members"].get(chain_root) or []
    if not members:
        return 0
    return max(stage_of(tree, m) for m in members)


def load_species_from_dir(pokemon_dir: pathlib.Path) -> list[dict]:
    """读一个目录下的所有宝可梦 JSON（管线缓存 / 上游仓库的 data/pokemon）。"""
    out: list[dict] = []
    for f in sorted(pokemon_dir.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def main() -> int:
    """自检：打印树的统计，并抽查几个已知的链。"""
    import argparse

    ap = argparse.ArgumentParser(description="进化树自检")
    ap.add_argument("dir", help="宝可梦 JSON 目录（42arch 的 data/pokemon）")
    ap.add_argument("--extra", default="", help="逗号分隔的额外名字（预告世代，各自独立成链）")
    args = ap.parse_args()

    species = load_species_from_dir(pathlib.Path(args.dir))
    if not species:
        print(f"[失败] {args.dir} 里没有 JSON")
        return 1
    extra = [s for s in args.extra.split(",") if s.strip()]
    tree = build_tree(species, extra)
    print(f"[自检] {len(species)} 只 -> {len(tree['members'])} 条链")
    for probe in ("伊布", "皮卡丘", "走路草", "刺尾虫", "宝贝龙", "熊宝宝", "黏黏宝"):
        if probe not in tree["root_of"]:
            continue
        root = tree["root_of"][probe]
        print(f"  {probe}: 根={root} 阶={stage_of(tree, probe)} "
              f"成员={tree['members'][root]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
