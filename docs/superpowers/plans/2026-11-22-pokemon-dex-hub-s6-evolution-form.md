# S6 · 进化链形态改写

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 让 `collected[].form` 真正跟着进化链变化 —— 链上收服了更高阶的成员后，同链已收服条目的展示形态随之推进；并把上游拍平的进化数据修成一棵真树。

**Architecture:** 新增 `scripts/evo_tree.py`（纯逻辑：从全库重建进化树、算每只的阶）。`scripts/fetch_pokemon.py` 改用它输出**同链一致的** `evolution` 数组 + 新的 `evolutionStage` 字段。服务端 `scripts/dex_core.py` 新增链形态推进逻辑，`scripts/award.py` 在发奖后调用。图鉴页展示「当前形态」。

**Tech Stack:** Python 3.11（标准库 only）、unittest、GitHub Pages（静态站）。

---

## 背景：真实数据把进化关系拍平了

42arch 的 `evolution_chains` 里每一项是一条**从根出发的路径**（不是一棵树），一只宝可梦可能出现在多条路径里（伊布 = 8 条分支）。

`scripts/fetch_pokemon.py` 早期把多条路径**首尾相接**拼成一条数组：

```python
for link in src.get("evolution_chains") or []:
    for node in link:
        chain.append(node["name"])
```

⇒ 皮卡丘的链变成 `['皮丘','皮卡丘','雷丘','皮丘','皮卡丘','雷丘']`（重复），
伊布的 8 个分支被当成 8 个连续阶。

**后果**（S1 时就已记录在规格里）：
- 同链成员拿到的 `evolution` 数组**不一致**（走路草 6 个名字、霸王花只有 3 个）
- 「这一阶对应哪只」**没有唯一答案** ⇒ 形态改写硬做出来就是「养了 90 天还是没进化」

---

## Task 1: `scripts/evo_tree.py` —— 从全库重建进化树

**Files:**
- Create: `scripts/evo_tree.py`
- Create: `tests/test_evo_tree.py`

**Interfaces:**
- `build_tree(species: list[dict], extra_names: list[str] | None = None) -> dict`
  —— `species` 是上游宝可梦 JSON 的列表；`extra_names` 是上游没收录的预告条目。
  返回 `{"root_of", "depth_of", "members", "parent_of"}`。
- `evolution_array(tree, name) -> list[str]` —— 同链成员拿到**完全一致**的一份数组。
- `stage_of(tree, name) -> int` —— 该只在自己链上的阶数（根=0）。
- `stage_for_chain(tree, chain_root) -> int` —— 链最深能到的阶数。

**规则（★ 每条都是踩过坑之后定下来的，别改）：**

1. **path 内相邻关系是父节点的硬事实** —— 一条 path 是有序链，第 i 个节点的 `from`
   必然是第 i-1 个节点的名字。**只用节点自报的 `from` 是不够的**：
   链的中间节点在自己的文件里往往不出现（只在链根的文件里出现）⇒ 没有票 ⇒
   链被拆成「皮丘」「皮卡丘→雷丘」两截（实测 905 条链，伊布组只剩自己）。
2. **跨 path 冲突用多数票** —— 少数文件的 `from` 写错时（实测 12 只把自己的 `from`
   写成 None，少数 path 还把父节点写串了），多数票让整条链保持在一起。
   平票取字典序最小 ⇒ 确定、可复现。
3. **向上遍历必须有环保护** —— 父节点是投票结果，数据脏时可能成环。
   不加 `seen` 检查就是无限循环 + 内存爆掉被 OOM killer 杀（rc=137）。**这个坑踩过。**
4. **根 = 往上走完之后停下的那个节点**（不是 `chain[-1]`，那是离根最远的）。
5. **阶 = 树上的深度**（根=0），与上游 `stage` 标签不一致时**取较小者** ——
   宁可低估阶数，也不要把兄弟形态排成连续阶（美丽花/霸王花同阶，不是 2 阶和 3 阶）。
6. **预告条目（上游没收录）各自独立成链**，不编造进化关系。

**验证点（钉死数字）：**
- 1025 只上游 + 3 只预告 ⇒ **543 条链**（= 真实生物链数，与规格一致）
- 伊布组 9 只、走路草组 4 只、刺尾虫组 5 只、熊宝宝组 3 只、黏黏宝组 3 只
- 同链成员的 `evolution` 数组**逐字相同**

---

## Task 2: 修 `scripts/fetch_pokemon.py`

**Files:**
- Modify: `scripts/fetch_pokemon.py`

**要改的：**
- `extract()` 不再自己拼 `evolution` ⇒ 改由 `evo_tree.evolution_array()` 提供
- 新增字段 `evolutionStage`（该只的阶数）
- ★ **管线要两遍走**：进化关系是**全库**信息，单只文件里不全
  ⇒ 第一遍收全库建树，第二遍才产出条目
- 预告条目（`preview_pokemon.merge_preview`）的 `evolution` 要跟着改成一致数组
  （现在它们是 `['叶眉鸟']` 这种单元素，也算一致 ✓，但要确认合并后仍一致）

**⚠️ 管线重跑成本高**（clone 整个上游仓库 + 1025 个 JSON + PokeAPI 兜底文案）：
**不重跑**。改成写一个**一次性补丁脚本** `scripts/patch_evolution.py`：
读现有 `data/pokedex.json` + 全库树 ⇒ 只重写 `evolution` / `evolutionStage` 两个字段，
**其余字段（flavor / art / types / …）一个字节都不动**。

---

## Task 3: 服务端 —— 链形态推进

**Files:**
- Modify: `scripts/dex_core.py`
- Modify: `scripts/award.py`
- Create: `tests/test_chain_form.py`

**形态推进规则（★ 本阶段最核心的裁决）：**

> **链推进 = 这条链收服了阶更高的成员。**
> 收服 X（阶 s）时：若同链已有收服条目的阶 < s ⇒ 那些条目的 `form` 改成 X 的 id。
> 若已有条目阶 ≥ s ⇒ **不动**（高阶不会被低阶拉回去）。

理由：
- 只有「阶」能定义「这一阶对应哪只」—— 上游拍平的数据里做不到，树上可以
- 低阶不能覆盖高阶：先收服了暴飞龙（2 阶），后收服宝贝龙（0 阶），
  宝贝龙的 form **不能**把暴飞龙改回去
- 兄弟形态同阶 ⇒ 谁先来谁生效，天然幂等

**幂等要求**：同样的 `collected` 反复跑，结果必须一样。

**`collected[].form` 的语义**（规格 §5.4）：
- 进化**不改变** `collected[].id`（否则「已收服 N/1028」会倒退）
- `form` 记录该只当前所处的形态 id
- `form` 对应的宝可梦本身不必被单独收服

**老数据**：`form` 缺失 ⇒ 等于 `id`（规格 §5.3）⇒ 前端已有两级回退 ✓

---

## Task 4: 图鉴页 —— 展示「当前形态」

**Files:**
- Modify: `site/app.js`
- Modify: `site/style.css`

- 图鉴卡片已经在用 `collected[].form` ✓（S5 做的），这条路径现在**真的有数据**了
- 链级养成那一块加一行：当前形态（`form` 对应的名字）
- ⚠️ `site/pokedex.json` 由 `scripts/build_site_pokedex.py` 生成，
  要把 `evolutionStage` 带进去 ⇒ `KEEP` 字段加一个

---

## Task 5: 迁移 + 测试 + 部署

- `data/dex.json` 里现有 8 条：`form` 全等于 `id` ⇒ 迁移后应等于链内最高阶成员的 id
  （例：暴飞龙在宝贝龙链里是 2 阶，是链内最高 ⇒ form 不变；
  但如果同链还有低阶的已收服条目，那些才会被改写）
- `python3 -B run_tests.py` 全绿
- 部署（`scripts/deploy.sh`）+ 真浏览器验证 + 截图

---

## 不做（YAGNI）

- **不做** 跨链杂交、反向退化（规格 §10 明确排除）
- **不做** 进化动画、形态立绘的额外素材（沿用现有立绘）
- **不做** 链的「进化路线图」页面（一条链最多 9 个成员，一张卡说得清）
- **不改** `sources.json` 的任何口径（`per` / `perAward` 一律不动 ⇒ cron 提示词不用改）
