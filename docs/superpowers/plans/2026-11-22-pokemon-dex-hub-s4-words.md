# 训练家图鉴中枢 S4（备考源） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 让「备考」成为第四个行为源：她在一个手机网页上记下今天背了多少词 → 折算额度 → 提交进同一个账本 → 和阅读、番茄钟、KOReader 一起凑宝可梦。

**Architecture:** 复用已有的 push 源通道（GitHub Issue 投递箱）—— 服务端**零改动**（`scripts/collect_reports.py` 对任意启用的 push 源一视同仁 ✓）。客户端是一个**纯静态记数页** `site/words/`：手机优先，大数字输入 + 快捷加减，按天记住最近 7 天，可补报前几天，一键生成预填好的 GitHub issue 链接。

**为什么是「自建小网页」而不是接某个背单词 App**：设计文档 §8.4 写明「工具待姜来指定」，而**多数背单词 App 没有开放 API**；设计文档风险表里给的兜底正是「自建一个小网页记数工具，同属 `words` 源」✓。这一版先把**工具无关**的部分做完（记数页 + 口径），等姜来定了具体 App（扇贝 / 百词斩 / 不背单词 / 墨墨），再按需加「自动导入」（油猴脚本读网页版 DOM，或读 App 的导出文件）—— **那是后加的一层，不影响这一版** ✓。

**Tech Stack:** vanilla JS + CSS（零外链）；Python 3.11 标准库 only；Playwright 真浏览器测试。

---

## Global Constraints

- **额度口径只有一条铁律**：每源声明 `per`（多少原始单位 = 1 额度），**按天 floor，零头不计，不跨天凑**；
  当天总额度 = 各启用源**相加**；`perAward = 2` 且一天最多 1 只。
- **三个已有源必须保持独立**（weread / pom / koreader）—— 账本对「同源同一天」取 max，合源会把其中一个平台的量吃掉 ✓。
- **`perAward` 现在是 2** ⇒ 30 分钟阅读 = 1 只宝可梦。S4 不许改它。
- **`per` 是「1 额度要多少原始单位」**，不是「1 只要多少」—— 1 只 = `perAward × per` 个原始单位。
- **`words` 源的 `per` 从 20 改成 100**（暂定值，见下），`maxPerDay` 保持 500。
  **为什么不是 20**：`per=20` ⇒ 1 只只要 40 个词 —— 一天背 20 个新词加一堆复习就白送一只，额度会通胀 ✓。
  **100 意味着**：按「新词 + 复习」计，一天 200 个词 = 1 只（≈ 一个番茄钟的量级 ✓）；按「只算新词」计则偏贵（详见「待姜来裁决」）✓。
- **客户端只报「我投入了多少」** —— 抽卡权只在服务端 ✓。
- **不许引外部 CDN 的框架/字体/脚本**（她那边大陆直连，外链会挂 ✓）。
- **拉不到 `sources.json` 时必须照常渲染**（用兜底 `per`）—— 这个项目的老教训：JSON 一挂整页卡在「载入中…」，看起来像网站坏了 ✓。
- **Python 只用标准库**；测试 `tests/test_*.py`，类名 `XxxTest`，方法名 `test_xxx`；中文注释与中文 docstring。
- **一次提交只做一件事**，commit message 中文、动词开头；提交前 `python3 -B run_tests.py` 全绿。
- ⚠️ **这台机 git 会漏报改动**（NFS index stat 缓存）：提交前 `touch <文件> && git update-index --really-refresh && git status --short`；`git add -A` 也可能漏 ⇒ 漏了再 `touch` 一次。
- ⚠️ **远端 main 会被 cron 推 commit** ⇒ 推之前 `git fetch origin main` + rebase。
- **不要跑 `scripts/deploy.sh`**（部署由 controller 做）。

---

## Task 1: 开 `words` 源 + 改 `per`

`data/sources.json`：`words` → `"enabled": true`（其它字段不动：`unit: 个`、`per: 100`、`mode: push`、`maxPerDay: 500`）。

**测试**（`tests/test_words_source.py`）：
- `words` 已启用 ✓
- `per` = 100、`unit` = 个、`mode` = push ✓
- 100 个词 = 1 个额度；**按天 floor**：25 + 50 个词 ⇒ 0 + 0 = **0 个额度**（不是 `floor(75/100)` = 0 ✓ —— 用 150 + 50 ⇒ 1 + 0 = 1 ✓ 这个例子更能说明「不跨天凑」✓）
- 额度与 weread / pom / koreader **相加** ✓
- `parse_report` 认这个源 ✓（服务端零改动，但要证明 ✓）
- 现有三个源的口径**不受影响** ✓

---

## Task 2: 记数页 `site/words/`

**三个文件**：`site/words/index.html`、`site/words/app.js`、`site/words/style.css` ✓

**布局（手机优先，390px 宽也要好看 ✓）**：
1. 标题 + 「今天背了多少个词」
2. **一个大数字输入**（`inputmode="numeric"` ✓）+ 快捷按钮 `+10 / +20 / +50 / 清零` ✓
3. 实时显示：「今天 150 个 → 折算 1 个额度（20 个 = 1 个额度，按天算，零头不计）」✓
   —— `per` 从 `sources.json` 读 ✓，读不到退回 100 ✓
4. **最近 7 天**：一行小卡片（日期 + 个数 ✓），点某天可以补录 ✓（今天高亮 ✓）
5. 「去 GitHub 提交」按钮 ⇒ 打开预填好的 issue 链接 ✓
   - title：`[report] words <最新的一天>`
   - body：`{"v":1,"source":"words","days":{"2026-11-22":150,"2026-11-21":80}}` ✓
     （**与番茄钟的 body 格式完全一样** ✓ —— 服务端 `parse_report` 通用 ✓）
6. 一段说明：提交给哪个仓库、点 Submit 之后会发生什么、额度按天清零 ✓

**技术要点**：
- **零外链** ✓；只用相对路径取 `sources.json`（同源 ✓ —— 注意 KOReader 落地页用的是绝对 CDN 地址，
  但那里是为了让测试能拦截；本页在同源下，用相对路径更稳 ✓）
- **localStorage** 存最近 7 天（key 用 `pokemon-dex-words-v1` ✓）——
  **localStorage 不可用时要兜住**（隐私模式 / 沙箱 iframe 下 `localStorage` 会抛异常 ⇒ 不能让整页崩 ✓）
- **零天数时**：显示友好提示「今天还没记」✓，不要显示一个孤零零的 0 ✓
- **提交前把每天的个数夹到 `maxPerDay`** ✓（服务端会整份拒收超上限的报告 ✓，夹了要有提示 ✓）
- 页面要能处理**被直接打开**（无 `?` 参数 ✓）与**带参数打开**两种情况 ✓

**测试**（`tests/test_words_landing.py`，Playwright 真浏览器 ✓）：
- 输入 150 → 显示「1 个额度」✓；输入 250 → 「2 个额度」✓
- **按天 floor**：第一天 250、第二天 50 ⇒ 合计 **2 个额度**（不是 `floor(300/100)` = 3 ✓）——
  **这条必须钉住具体数字** ✓
- 快捷按钮 `+20` 生效 ✓
- localStorage 记住：刷新后数字还在 ✓
- 点「补录」某天可以改历史 ✓，且**只把有数的天**放进 body ✓
- issue 链接的 title/body 正确，且 body 能被**服务端的 `parse_report` 认** ✓（端到端）
- 拉不到 `sources.json` 时仍然渲染（用兜底 per ✓），不卡在「载入中…」✓
- **零外链**（HTML 的 `src|href` 双/单引号、CSS 的 `url()`/`@import`、JS 里的 `http(s)://` 字面量）✓
- 390px 宽下不横向溢出 ✓

---

## Task 3: 文档

- 根 `README.md`：加「备考源」一节（怎么用、额度口径、**1 只 = 200 个词** ✓）
- `docs/words-setup.md`：一页讲清「她怎么记、怎么提交、提交后发生什么」✓
  （**别写「自动导入」** —— 那一层还没做 ✓ 写清楚现状就是手动记数 ✓）

---

## Task 4（**以后做，取决于姜来定哪个 App**）

- 若用网页版（扇贝 web / 百词斩 web / 不背单词 web）⇒ **油猴脚本**读 DOM 自动填「今天的词数」✓
  （和 `plugin/weread-dex.user.js` 同一套思路 ✓）
- 若用手机 App 且有导出文件（墨墨的备份/CSV ✓）⇒ **上传导出文件 → 解析** ✓
- 这一层做完之前，**手动记数就是正式流程** ✓，不要在文档里承诺自动 ✓

---

## 待姜来裁决（不阻塞开发，但影响额度）

1. **记「新词」还是「新词 + 复习」？**
   - 只算新词（一天 20~30 个）⇒ `per` 得在 10 左右，否则一个月才一只
   - 新词+复习（一天 100~200 个）⇒ `per = 100` 合适 ✓（本计划采用这个）
2. **具体用哪个 App**（决定 Task 4 能不能自动 ✓）

---

## Self-Review

**已知的坑（动手前先想清楚）**：

1. **`per` 改成 100 会不会太严** ⇒ 她一天背 100~200 个（含复习）✓ 一天一个额度到两个额度 ✓ 合理 ✓
2. **`per=20` 那个占位值是通胀的** ⇒ 40 个词就一只 ⇒ 一天白送一只 ⇒ 必须改 ✓
3. **`words` 启用后，账本里多一列** ⇒ 旧账本没有这一列 ⇒ 读取方要容错（`credit_for_day` 对缺失的源按 0 ✓）
4. **记数页的 localStorage 可能不可用** ⇒ 隐私模式 / iframe ⇒ 必须兜住 ✓
5. **别把「手动记数」写成「自动」** —— 这个项目有过「文档写『已经』但其实还没有」的坑 ✓
6. **提交前夹 `maxPerDay`** ⇒ 否则服务端整份拒收，她以为交上去了 ✓
7. **额度是按天 floor 的** ⇒ 落地页与服务端必须一致 ✓（番茄钟那页已经踩过这个坑 ✓）
