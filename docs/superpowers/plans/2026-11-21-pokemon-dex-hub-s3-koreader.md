# 训练家图鉴中枢 S3（KOReader 插件） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 KOReader 成为第三个行为源：插件读出自己记的阅读时长 → 在 Kindle 屏幕上显示一个二维码 → 手机扫码落到一个网页 → 一键跳到预填好的 GitHub issue → 点 Submit → 服务端照旧收进账本、折算额度。

**Architecture:** 服务端新增**独立的 `koreader` 源**（单位分钟，15 分钟 = 1 额度）。插件（Lua）读 KOReader 内置 statistics 插件的 `settings/statistics.sqlite3`，用**与内置 statistics 完全一致的 SQL 口径**按天汇总时长，生成一条短链接（`/koreader/?days=...`）并用 KOReader 内置的 `QRMessage` 显示二维码。手机扫码打开落地页，落地页把短链展开成预填好的 GitHub issue URL，点 Submit 即可。**抽卡权仍然只在服务端**，插件只报「我读了多少分钟」。

**Tech Stack:** Lua（KOReader 插件，零依赖，用 KOReader 自带模块）；Python 3.11（服务端，标准库 only）；vanilla JS（手机落地页）；GitHub Issue 投递箱。

**上游设计：** [2026-11-18-pokemon-dex-hub-design.md](../specs/2026-11-18-pokemon-dex-hub-design.md) §7 / §8.3

---

## Global Constraints

- **沙箱里没有真机。** Kindle 上的实际渲染、扫码、GitHub 登录都验不了 —— 必须留一套**对账步骤**给她（见 Task 5）。
- **Lua 代码的纯逻辑部分必须能单测。** 沙箱没有 Lua 解释器，用 **fengari**（npm 上的 Lua 5.3 VM）跑：纯函数（拼 URL、格式化、解析）拆到独立模块 `report.lua`，用 Python 包装成 `tests/test_koreader_lua.py` 走进 `run_tests.py` ✓。
- **SQL 口径必须与 KOReader 内置 statistics 一模一样** —— 否则她对账时数字对不上，整个 S2/S3 就是自说自话。口径取自 `plugins/statistics.koplugin/main.lua` 的 `sqlDaily()`（同目录源码已核对，schema 版本 20221111）。
- **Python 侧一律标准库 only。** 测试放 `tests/test_*.py`，类名 `XxxTest`，方法名 `test_xxx`；中文注释与中文 docstring。
- **提交前 `python3 -B run_tests.py` 必须全绿。**
- **一次提交只做一件事**，commit message 用中文，动词开头。
- **沙箱 git**：`export GIT_SSL_NO_VERIFY=1`；push 用 `git -c http.expectContinue=false push origin HEAD:main`；
  ⚠️ **`git add -A` 也会漏改动** → `touch <文件> && git update-index --really-refresh`；
  ⚠️ **远端 main 会被 cron 推 commit** → 推之前 `git fetch origin main` + rebase。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `data/sources.json` | 修改 | 新增 `koreader` 源（分钟 / per 15 / push） |
| `plugin/koreader-dex.koplugin/_meta.lua` | 新建 | 插件元信息 |
| `plugin/koreader-dex.koplugin/report.lua` | 新建 | **纯逻辑**：拼短链、格式化天数、SQL 常量（可单测） |
| `plugin/koreader-dex.koplugin/main.lua` | 新建 | 插件主体：读 DB → 汇总 → 弹二维码 / 自检 |
| `plugin/koreader-dex.koplugin/README.md` | 新建 | 安装与用法（给她看的） |
| `site/koreader/index.html` | 新建 | 手机落地页：显示数字 → 跳预填 issue |
| `site/koreader/app.js` | 新建 | 落地页逻辑（拼 issue URL、显示额度） |
| `tests/test_koreader_source.py` | 新建 | 服务端源注册与额度折算的用例 |
| `tests/test_koreader_lua.py` | 新建 | 用 fengari 跑 Lua 纯逻辑的用例 |
| `tests/koreader_lua_spec.lua` | 新建 | Lua 侧的断言（fengari 执行） |
| `tests/test_koreader_sql.py` | 新建 | 用 Python sqlite3 跑**插件里那条同一个 SQL** 的口径用例 |
| `scripts/koreader_selftest.py` | 新建 | 造一个假的 statistics.sqlite3，用它验 SQL 与换算 |

---

## Task 1: 服务端加 `koreader` 源

**为什么是独立源，不是并进 `weread`**：账本对「同源同一天」是**取 max**（幂等的关键）。
微信读书 1800 秒、KOReader 600 秒 —— 取 max = 1800，**KOReader 那份直接被吃掉** ✓。
独立源的额度照样相加进同一份额度（`day_credit` 把各启用源的额度求和 ✓），
「同一份额度」的意图完整保留 ✓。

改动（`data/sources.json`）：

```json
"koreader": {
  "name": "KOReader",
  "icon": "📚",
  "unit": "分钟",
  "per": 15,
  "mode": "push",
  "enabled": true,
  "maxPerDay": 1440
}
```

- `per: 15` ⇒ **15 分钟 = 1 个额度**（与微信读书的 900 秒同价 ✓）
- `maxPerDay: 1440` ⇒ 一天最多 1440 分钟（源级上限，防客户端乱报 ✓）
- **服务端代码不用改**：`collect_reports.py` 对任何 `mode == "push"` 的启用源一视同仁 ✓

**测试**（`tests/test_koreader_source.py`）：

- `test_koreader_source_registered`：注册表里有这个源 ✓
- `test_koreader_is_push_source`：mode 是 push ✓（否则 issue 上报会被拒 ✓）
- `test_koreader_minutes_to_credit`：25 分钟 → 1 个额度 ✓
- `test_koreader_credit_sums_with_weread`：微信读书 1 个额度 + KOReader 1 个额度 = 2 ⇒ 达标 ✓
- `test_koreader_clip_to_max_per_day`：报 9999 分钟 → 夹到 1440 → 96 个额度 ✓
- `test_report_parser_accepts_koreader`：`parse_report` 认这个源 ✓

---

## Task 2: 插件的纯逻辑（`report.lua`）

**能单测的部分全部搬到这里**（不碰任何 KOReader 模块 ✓）：

```lua
local Report = {}

-- 落地页的基址（手机扫码后打开的地方）
Report.BASE_URL = "https://lilia-shound.github.io/pokemon-dex/koreader/?days="

-- 与 KOReader 内置 statistics 的 sqlDaily() 一致的按天汇总口径
Report.DAILY_SQL = [[ ... ]]

function Report.encode_days(days)     -- {["2026-11-21"]=25} → "2026-11-21:25,2026-11-20:50"
function Report.decode_days(str)      -- 反向
function Report.short_url(days)       -- 拼落地页短链
function Report.to_minutes(seconds)   -- 秒 → 分钟（向下取整）
function Report.pick_days(all, max_days) -- 取最近 N 天（按日期倒序）
function Report.credits(days, per)   -- 每天多少额度
```

**测试**：`tests/test_koreader_lua.py`（Python 包装 fengari）+ `tests/koreader_lua_spec.lua`（断言）✓

- 编码/解码往返一致 ✓
- 日期倒序 ✓
- 分钟向下取整（119 秒 → 1 分钟 ✓）
- `pick_days` 只取最近 N 天、丢掉 0 分钟的天 ✓
- 额度 = floor(分钟 / per) ✓
- 短链里不含空格与中文（URL 安全 ✓）

---

## Task 3: 插件主体（`main.lua`）

**菜单**（挂在 KOReader 主菜单「工具」下 ✓）：

1. **汇报阅读时长（最近 7 天）** → 读 DB → 弹二维码 ✓
2. **只汇报今天** ✓
3. **自检** → 显示最近 30 天的表 + 数据来源与口径说明（**给对账用** ✓）

**读 DB**（`main.lua`）：

```lua
local SQ3 = require("lua-ljsqlite3/init")
local DataStorage = require("datastorage")
local db_location = DataStorage:getSettingsDir() .. "/statistics.sqlite3"
```

- 查询用 `Report.DAILY_SQL` ✓（与内置 statistics 同一个口径 ✓）
- `page_stat` 视图不存在（老库没升级）⇒ 报错要**说人话**：「先在 KOReader 里打开一次 Reading statistics，让它把数据库升级」✓
- DB 不存在 ⇒ 「KOReader 的统计数据库还没建 —— 先读一会儿书，或打开一次 Reading statistics」✓
- 统计插件没开（`is_enabled == false`）⇒ 提示 ✓

**弹二维码**：

```lua
UIManager:show(QRMessage:new{
    title = _("训练家图鉴 · 汇报阅读"),
    text = Report.short_url(days),
    invert_bidi = self.ui.view:shouldInvertBiDiLayoutMirroring(),
})
```

- 二维码内容是**落地页短链**（不是 GitHub 长链 ✓ —— Kindle 上没法登录 GitHub ✓）
- 同时 `Device.input.setClipboardText(url)`（兜底 ✓）
- `QRMessage` 的载荷上限 2953 字节 ✓；30 天的短链约 400 字节 ✓，但默认只报 7 天（≈100 字节 ⇒ 二维码很稀疏、e-ink 上也扫得清 ✓）

**安装**：把 `koreader-dex.koplugin/` 整个目录复制到设备的 `plugins/`（Kindle：`/mnt/us/koreader/plugins/` ✓）→ 重启 KOReader ✓。

---

## Task 4: 手机落地页（`site/koreader/`）

打开 `https://lilia-shound.github.io/pokemon-dex/koreader/?days=2026-11-21:25,2026-11-20:50` ⇒

1. 显示每天的分钟数 + **合计折算成几个额度**（`per` 从 `sources.json` 读 ✓）
2. 一个按钮「去 GitHub 提交」⇒ 打开预填好的 issue URL ✓
   - title：`[report] koreader 2026-11-21`
   - body：`{"v":1,"source":"koreader","days":{"2026-11-20":50,"2026-11-21":25}}` ✓
3. 一段说明：这是给哪个仓库提的 issue、点 Submit 之后会发生什么 ✓

**测试**：Playwright（真实浏览器）✓ —— 落地页能显示数字、额度算对、按钮点开是合法的 issue URL（title/body 可解析 ✓）。

---

## Task 5: 对账方案（沙箱验不了的那部分）

给她一套**四步对账**，任何一步对不上就知道是哪一环错了：

1. **Kindle 上**：插件「自检」显示最近 30 天的表 ⇒ 与 KOReader 自带
   「Reading statistics → Time range → Last week by day」**逐天对照**（口径相同 ⇒ 必须一模一样 ✓）
2. **手机扫码**：落地页显示的数字 ⇒ 与 Kindle 上**一模一样** ✓（同一份数据 ✓）
3. **GitHub**：提交前 issue 预览里的 body ⇒ 与落地页显示的分钟数**一样** ✓
4. **服务端**：cron 跑完后，issue 下的评论会写「已采纳：N 天的量，共折算 M 个额度」⇒ M 与落地页算的一致 ✓
   账本里能看到 `{"日期": {"koreader": 分钟}}` ✓

---

## Task 6: 顺手修：cron 部署条件

**新问题**：`perAward = 2` 之后发奖变少，cron 的「只有抽到宝可梦才 deploy」会让
图鉴页的「今日 X/30 分」**旧一两天** ✗。

改法：cron 的 deploy 条件加一条 —— **今天的读数变了也部署** ✓
（一天 8 次小提交，gh-pages 重建约 20 秒 ✓）。

---

## Self-Review

**已知的坑（动手前先想清楚）**：

1. **别用「并进 weread 源」** —— 账本取 max 会吃掉一个平台的量 ✓（Task 1 已说明）
2. **口径必须一模一样** —— 插件的 SQL 与内置 statistics 的 `sqlDaily()` 逐字对齐 ✓（否则对账失败 ✗）
3. **内置 statistics 的落库有延迟** —— 每 50 次翻页 / 关书 / 息屏才写库 ✓
   ⇒ 插件要提示「刚读完的那几页可能还没落库，关掉书或息屏后再汇报」✓
4. **单页时长的口径**：内置 statistics 默认 `min_sec=5` / `max_sec=120` ✓
   ⇒ 我沿用它的记录值，不自己另算 ✓（她的设置改了，我的数字也跟着变 ✓）
5. **时区**：SQL 用 `'localtime'` ✓（设备本地时区 = 她在台州 = UTC+8 ✓ 与服务端的北京时间一致 ✓）
6. **二维码放不下长 URL** —— 放短链 ✓（落地页再展开 ✓）
7. **Kindle 上没法登录 GitHub** —— 所以不能把 issue URL 直接塞二维码 ✓
8. **落地页自己不发请求** —— 它只拼 URL 让用户点 ✓（静态站不能收 POST ✓）
9. **沙箱验不了真机** —— 所以插件必须有「自检」入口 ✓ + 给对账步骤 ✓

**不做**（YAGNI）：插件内直接发 HTTP 到 GitHub（要 token ✗ 风险太大 ✗）；
KOReader 进度同步（那是 KOReader 自己的事 ✗）；多本书的明细上报（只要总时长 ✓）。
