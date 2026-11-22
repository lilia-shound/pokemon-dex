# 训练家图鉴中枢 S5（图鉴页完整版） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 图鉴页顶部从「今日 X/30 分」改成**多源额度进度**；图鉴墙按收服时的**形态**显示图；加**链级养成**（保底可用数）进度。阈值文案从「读满 X 分钟」改成额度语义。

**Architecture:** 纯前端改造 —— `site/app.js` + `site/style.css` + `site/index.html` ✓。**服务端零改动** ✓（`today.sources`、`ledger`、`chains`、`collected[].form` 该有的数据都已经在线上 ✓）。

**Tech Stack:** vanilla JS / CSS（零外链）；Python 3.11 标准库 only；Playwright 真浏览器测试。

---

## 关键事实（都已在线上核对过，别猜）

**`data/dex.json`（v2）现在长这样**：
```json
{
  "version": 2, "collected": [...], "streak": 3, "lastAwardDate": "2026-11-22",
  "today": {"date": "2026-11-22", "seconds": 1753, "threshold": 1800,
            "sources": {"weread": 1753, "pom": 25}, "credit": 2, "perAward": 2},
  "ledger": {"2026-11-22": {"weread": 1753, "pom": 25}, ...},   // 逐日逐源
  "chains": {"宝贝龙": {"firstDate": "2026-11-20", "lastShinyDate": null}, ...}
}
```

**`site/pokedex.json`**：`{count, pokemon:[{id, nameZh, nameJa, types, category, abilities, height, weight, evolution, flavor, art, artShiny}]}` ✓
—— `art` = `"<id>.webp"` ✓；图片在 `site/img/<id>.webp` ✓

**注册表 `site/sources.json`**：`{perAward: 2, sources: {weread:{icon,unit,per,...}, pom:{...}, words:{...}, koreader:{...}}}` ✓

**⚠️ 铁律：额度是「按天 floor 再相加」**（跟服务端 `scripts/sources.py` 的 `credit_for_day` 一模一样 ✓）：
```python
min(raw, maxPerDay)  →  max(0, ...)  →  // per      # 每个源各自 floor
# 当天总额度 = 各启用源的额度之和
```
**前端算出来的额度必须和服务端算的完全相同** ✓（差一个 she'll see 「还差 1 个额度」但服务端说达标 ✗✗）。

---

## Task 1: 顶部 → 多源额度进度

**要改的**：`site/index.html` 的 `#todayMini` 块 ✓、`site/app.js` 的 `todayInfo()` / `thresholdSentence()` / 渲染逻辑 ✓、`site/style.css` ✓

**新结构**（一行一个源）：
```
今日额度
├─ 📖 微信读书   1753 秒 → 1 个额度
├─ 🍅 番茄钟       25 分钟 → 1 个额度
├─ 📚 KOReader      0 分钟 → 0 个额度
└─ 🔤 背单词        0 个 → 0 个额度
   ▓▓▓▓▓░░░░░  2 / 2 个额度 —— 今天已经凑够啦
```
- **只列注册表里 `enabled: true` 的源** ✓（`words` 现在也是 enabled ✓ ⇒ 四行全出 ✓）
- 每行显示：**图标 + 名称 + 今日原始量 + 单位 + 折算的额度** ✓
- 总进度条 = `合计额度 / perAward` ✓（`perAward` 来自注册表 ✓；注册表拿不到就用 `today.perAward` ✓）
- **数据来源**：今日原始量取 `today.sources[源id]` ✓（**没有这个键就是 0** ✓ —— 那个源今天没量 ✓）
  —— `today.seconds` 是**微信读书专用**的旧字段 ✓（`threshold` 也是 ✓），**不能拿它当多源口径** ✓
- **保留 `today.seconds` / `today.threshold` 这两个字段本身** ✓（微信读书插件还在读它们 ✓ —— 一个都不许删 ✓）

**文案改额度语义**（`thresholdSentence()` ✓）：
- 现在：「读满 30 分钟就能遇上新的宝可梦。」✗
- 改成：「凑满 2 个额度（= 30 分钟阅读）就能遇上新的宝可梦。」✓
  —— **各源的「等价值」怎么写**：微信读书 900 秒 = 1 额度 ⇒ 2 额度 = 1800 秒 = 30 分钟 ✓；
  番茄钟 25 分钟 = 1 额度 ⇒ 2 额度 = 50 分钟 ✓。**写法自己定，但要让人一眼看懂 ✓**
  —— 拿不到注册表时（旧数据/离线）⇒ **退回含糊但不说错的话** ✓（「今天凑够额度就能遇上新的宝可梦」✓）

**顶部还应该显示 streak**（连读天数 ✓ —— 数据里有 `streak` ✓）

---

## Task 2: 图鉴墙按**形态**显示

**现状**：`site/app.js` 用 `collected[].id` 查 pokedex 取 `art` ✓ ⇒ 进化了也显示基础形态 ✗
**要改成**：用 `collected[].form`（收服时的那只形态的 id ✓）查 ✓

- ⚠️ **老数据可能没有 `form`** ⇒ 回退到 `id` ✓
- 形态的**名字**也用形态的 `nameZh` ✓；基础名作为副标题 ✓（「妙蛙种子 → 妙蛙花」✓）
- 查表要 **O(1)**：建一个 `id → ���录` 的 Map ✓（1028 只 ✓ 别每次线性找 ✓）
- ⚠️ **`collected[].form` 可能在 pokedex 里查不到**（万一）⇒ 回退到 `id` ✓，再回退到剪影 ✓

---

## Task 3: 链级养成进度

**数据**：`chains` = `{链名: {firstDate, lastShinyDate}}` ✓

**要显示的**（每条链一行 ✓）：
- 链名 ✓（宝贝龙 ✓）
- 「保底可用数」= `floor((今天 - 基准日) / 30)` ✓
  —— **基准日**：进化链的保底规则是「这条链首次被收服后养满 30 天，再次抽到它就必定闪光」✓
  ⇒ 基准日 = `firstDate` ✓；若 `lastShinyDate` 存在 ⇒ 基准日 = `lastShinyDate` ✓（养完就该重置 ✓）
- 进度条 = 「距离下一次保底还差 N 天」✓（`30 - (今天 - 基准日) % 30` ✓，**满 30 天就满格** ✓）
- ⚠️ **「今天」用页面自己的日期** ✓（`today.date` ✓ —— 那和服务端同一个口径 ✓；拿不到就用本地日期 ✓）
- 链的**基础形态图**可以用链名去 pokedex 的 `evolution` 字段里找？**不做** ✓（`evolution` 存的是名字不是 id ✓ —— 查起来脆 ✗）⇒ **只显示文字 ✓**

---

## Task 4: 测试

**Playwright 真浏览器**（`tests/test_dex_page_s5.py` ✓）：
- 顶部列出**四个源**（构造一份含 4 个源的注册表 ✓）✓，每行的原始量/额度对 ✓
- **按天 floor 钉死具体数字** ✓：造 `{"weread": 1753, "pom": 25}`（per 900 / 25）
  ⇒ 微信读书 `floor(1753/900)` = **1** ✓、番茄钟 `floor(25/25)` = **1** ✓ ⇒ 合计 **2** ✓
  —— 再造一个跨天的：今天 150 个词 + 昨天 50 个词 ⇒ 合计额度必须是 **1**（不是 `floor(200/100)` = 2 ✓）
  —— **服务端 `scripts/sources.py` 的 `credit_for_day` / `credits_for_days` 要一起跑一遍，断言两边相等** ✓
- 某源今天没量 ⇒ 该行显示 0 ✓、**不隐藏** ✓
- 阈值文案不再出现「读满 30 分钟」✓（断言文案里有「额度」✓）
- 图鉴墙：`collected[].form` ≠ `id` ⇒ **显示的是形态的图与名字** ✓；没有 `form` ⇒ 回退到 `id` ✓
- 链：造 `chains` ⇒ 断言「保底可用数」与「还差 N 天」✓（**钉具体数字** ✓）
- 注册表拿不到 ⇒ 页面**不崩**、显示含糊文案 ✓
- **零外链** ✓
- 390px 不横向溢出 ✓

---

## Global Constraints

- **服务端零改动** ✓ —— 不许改 `scripts/`、`data/`、`plugin/` ✓
- **不许删 `dex.json` 里任何现有字段** ✓（`today.seconds` / `today.threshold` / `today.credit` / `today.perAward` / `streak` / `ledger` / `chains` / `collected[].id` / `collected[].nameZh` / `collected[].shiny` / `collected[].at` ✓ 微信读书插件还在读 ✓）
- **不许改** `site/pomodoro/`、`site/koreader/`、`site/words/` ✓
- 零外链 ✓；纯静态 ✓
- Python 只用标准库（Playwright 除外 ✓）；中文注释与中文 docstring ✓；测试类名 `XxxTest`、方法名 `test_xxx` ✓
- 一次提交只做一件事 ✓；commit message 中文、动词开头 ✓；提交前 `python3 -B run_tests.py` 全绿 ✓
- ⚠️ **这台机 git 会漏报改动** ⇒ 提交前 `touch <文件> && git update-index --really-refresh && git status --short` ✓
- **不要 push**，不要跑 `scripts/deploy.sh` ✓

---

## Self-Review（动手前先想清楚）

1. **`today.seconds` / `today.threshold` 是微信读书专用的旧口径** —— 别拿它们当多源口径 ✓（但**不许删** ✓）
2. **额度必须按天 floor** —— 前端与服务端必须一致 ✓（差一个就是「明明够了却说她没够」✗）
3. **注册表里 `enabled: false` 的源不列** ✓（现在四个都 enabled ✓）
4. **`per` / `maxPerDay` 都在注册表里** ✓ ⇒ 前端能自己算夹取 ✓（与服务端同口径 ✓）
5. **`collected[].form` 可能没有** ⇒ 三级回退：form → id → 剪影 ✓
6. **`chains` 可能为空** ⇒ 那一块整个不显示 ✓（别显示一个空标题 ✓）
7. **别把「今天」算成浏览器本地时区** —— 用 `today.date` ✓（服务端同一个口径 ✓）
8. **旧的 `todayMini` 块要替换掉** —— 但 `thresholdSentence()` 被好几处调用 ✓（图鉴墙的锁定文案 ✓）⇒ 改文案时**所有调用点都要对** ✓
