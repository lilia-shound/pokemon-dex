# 训练家图鉴 S8 · 免登录投递接收端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把三个 push 源（番茄钟 / 背单词 / KOReader）的投递从「登录 GitHub + 点两次」改成「点一下，原地显示已记录」，服务端仍只认自己算出来的账。

**Architecture:** 在 Cloudflare Worker 上开一个免登录的 `POST /ingest` 端点，报告进 Workers KV 队列；cron 每 3 小时用 `GET /pull` 把队列拉回来，**复用 `collect_reports.parse_report` 的校验 + `ledger.merge_amounts` 的合并**并进逐日账本，再 `POST /ack` 删掉已并账的条目。结算核心（`dex_core.py` / `award.py` / `ledger.py` / `sources.py` 的结算口径）一行不改；GitHub issue 老路保留当兜底。

**Tech Stack:** Python 3.11（**标准库 only**，测试用 `unittest`）、JavaScript（Worker 用 ESM + CommonJS 混合，node 直接跑单测）、Cloudflare Workers + Workers KV、Playwright（浏览器观感断言）。

**Spec:** `docs/superpowers/specs/2026-11-25-pokemon-dex-hub-s8-worker-ingest-design.md`（commit `94c79ca`）

## Global Constraints

这些是全项目的硬规矩，**每个任务都隐含包含**：

- **Python 只用标准库**（`urllib` / `json` / `datetime` / `unittest` …）⇒ 不许 `pip install` 任何东西。
- **`scripts/ledger.py` / `scripts/dex_core.py` 不碰网络与文件**；`scripts/sources.py` 只有 `load_config` 一处 IO。
- **结算核心一行不改**：`dex_core.py` / `award.py` 的判定逻辑、`sources.py` 的额度口径、`ledger.py` 的合并语义都不动。
- **现有读方依赖的字段一个都不能删**：`collected[].id/nameZh/shiny/at`、`streak`、`today.seconds`、`today.threshold`。
- **抽卡权只在服务端**；客户端只报「我投入了多少」。
- **测试**：`tests/test_*.py`，类名 `XxxTest`，方法名 `test_xxx`，**中文注释与中文 docstring**。
- **跑测试**：`python3 -B run_tests.py` 必须全绿（当前基线：**661 个**）。node 的单测由 Python 测试调用（node 缺失时**优雅跳过**，不能让整套挂掉 —— 照 `tests/test_koreader_lua.py` 的写法）。
- **一次提交只做一件事**；commit message 中文、**动词开头**。
- **沙箱 git**：`export GIT_SSL_NO_VERIFY=1`；push 用 `GIT_ASKPASS` 传 token（token 走环境变量，不进 URL、不进命令行）。
  ⚠️ **这台机 `git status` / `git add -A` 会漏改动** ⇒ 提交前先 `touch <文件> && git update-index --really-refresh && git add <文件>`。
  ⚠️ **`git status` 说「nothing to commit」也可能是骗人的** ⇒ 以 `git log` 为准。
- **远端 main 会被 cron 推 commit** ⇒ 推之前 `git fetch origin main && git rebase origin/main`。
- **沙箱 → GitHub TLS 抖动** ⇒ 重试 3~6 次、间隔 7~12 秒；**重试循环里必须 `> /tmp/p.log 2>&1` 再判 `$?`**（不能 `if cmd | tail`）。
- **沙箱 → npm registry 很慢**（30MB 的元数据都可能超时）⇒ 装 wrangler 要给足时间，必要时换 `npx --yes wrangler@3`。
- **测试里别写死 Playwright 的 Chromium 版本路径** ⇒ 用 `tests/browser_path.py` 的 `chromium_path()`。
- **「子页只许引一个 `<script>`」的纪律** ⇒ 客户端逻辑写在各子页自己的 `app.js` 里，**不许再引第二个脚本**。
- **子页的网络请求纪律** ⇒ 三个子页本来就在 fetch `sources.json` ⇒ ingest 配置从**同一份** `sources.json` 里读，**不许新增 fetch**。
- **客户端只报数** ⇒ 客户端发出去的 body 与 GitHub issue 那条路**逐字同构**（`{v, source, days}`）。
- **改 `site/app.js` 就要 bump 版本号**（`app.js?v=N`）—— 本次不改 `site/app.js`，但要确认没碰。

---

## 文件结构

| 文件 | 职责 | 本次动作 |
|---|---|---|
| `worker/package.json` | 把 `worker/` 标成 ESM（`{"type":"module"}`）⇒ node 能直接 import Worker 入口 | 新建 |
| `worker/lib/validate.cjs` | **纯校验逻辑**（CommonJS）—— 报告合不合法、天数夹不夹 | 新建 |
| `worker/lib/rules.cjs` | **单日上限表**（CommonJS）—— 源 id → maxPerDay（同时就是源白名单） | 新建 |
| `worker/index.js` | Worker 入口（ESM）：`/ingest` `/pull` `/ack` `/health` + CORS | 新建 |
| `worker/wrangler.toml` | 部署配置（Worker 名、KV 绑定） | 新建 |
| `scripts/collect_worker.py` | 服务端接收端：拉队列 → 校验 → 并账本 → ack | 新建 |
| `data/sources.json` | 源注册表 —— **新增顶层 `ingest` 段**（url / token / enabled） | 修改 |
| `scripts/sources.py` | `validate_config` 加 `ingest` 段的校验 | 修改 |
| `site/pomodoro/app.js` | 番茄钟页：同步按钮改成 POST + 待发队列 + 老办法入口 | 修改 |
| `site/words/app.js` | 背单词页：同上 | 修改 |
| `site/words/index.html` | 提交按钮 `<a>` → `<button>` + 加老办法链接 | 修改 |
| `site/koreader/app.js` | KOReader 落地页：同上 | 修改 |
| `site/koreader/index.html` | 同上 + 加 `#submitHint` | 修改 |
| `tests/worker_validate_spec.js` | Worker 校验逻辑的 node 单测 | 新建 |
| `tests/worker_handler_spec.mjs` | Worker 入口的 node 单测（假 env / 假 KV） | 新建 |
| `tests/test_worker_collect.py` | `collect_worker.py` 的 Python 单测 | 新建 |
| `tests/test_worker_rules.py` | 限幅表不漂移 + 时区口径 | 新建 |
| `tests/test_worker_client.py` | 三个客户端的契约断言（形状 / 老办法 / 队列） | 新建 |
| `docs/superpowers/specs/2026-11-25-pokemon-dex-hub-s8-worker-ingest-design.md` | 设计文档（**规则表文件名要同步**，见 Task 1 末尾） | 修改 |
| `README.md` | 写清楚新通道怎么用、怎么回滚 | 修改 |

---

### Task 1: Worker 的纯校验逻辑

**Files:**
- Create: `worker/package.json`
- Create: `worker/lib/validate.cjs`
- Create: `worker/lib/rules.cjs`
- Create: `tests/worker_validate_spec.js`

**Interfaces:**
- Consumes: 无（这是最底层的一块）
- Produces:
  - `validate.cjs` 导出 `validateReport(body, rules, today)` → `{ok:true, days:{日期:整数}, source:string}` 或 `{ok:false, error:string}`
  - `validate.cjs` 导出 `todayCst(nowMs)` → `"YYYY-MM-DD"`（**UTC+8**）
  - `rules.cjs` 导出 `RULES` → `{源id: 单日上限}`（**同时就是源白名单**）

- [ ] **Step 1: 写失败的 node 单测**

创建 `tests/worker_validate_spec.js`：

```javascript
'use strict';
/* Worker 校验逻辑的 node 单测。跑法：node tests/worker_validate_spec.js */

const assert = require('assert');
const path = require('path');
const V = require(path.join(__dirname, '..', 'worker', 'lib', 'validate.cjs'));
const R = require(path.join(__dirname, '..', 'worker', 'lib', 'rules.cjs'));

const TODAY = '2026-11-25';

function ok(report, today) {
  const r = V.validateReport(report, R.RULES, today || TODAY);
  assert.strictEqual(r.ok, true, '本该通过：' + JSON.stringify(r));
  return r;
}
function bad(report, today) {
  const r = V.validateReport(report, R.RULES, today || TODAY);
  assert.strictEqual(r.ok, false, '本该被拒：' + JSON.stringify(report));
  return r.error;
}

// ── 正常形状 ─────────────────────────────────────────────
const r = ok({ v: 1, source: 'pom', days: { '2026-11-21': 37 } });
assert.deepStrictEqual(r.days, { '2026-11-21': 37 });
assert.strictEqual(r.source, 'pom');

// ── 版本不对 ─────────────────────────────────────────────
assert.ok(bad({ v: 2, source: 'pom', days: { '2026-11-21': 37 } }).indexOf('版本') >= 0);

// ── 源不在白名单 ─────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'weread', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: '', days: { '2026-11-21': 37 } }).indexOf('source') >= 0);

// ── 白名单不能被原型链绕过 ───────────────────────────────
// ★ 直接 table[source] 会命中 Object.prototype.constructor（一个函数）
//   ⇒ 「量比上限大」变成和 NaN 比（恒 false）⇒ 白名单和单日上限同时失效。
assert.ok(bad({ v: 1, source: 'constructor', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: '__proto__', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: 'toString', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
// 绕过时的实际打法：拿一个不存在的源报一个爆表的量
assert.ok(bad({ v: 1, source: 'constructor', days: { '2026-11-21': 999999 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: 'hasOwnProperty', days: { '2026-11-21': 999999 } }).indexOf('不认识这个源') >= 0);

// ── days 的形状 ──────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: {} }).indexOf('没有天数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: [] }).indexOf('days') >= 0);
// 正好 30 天要通过（一次最多带 30 天 ⇒ 30 天收，31 天才拒）
const thirty = {};
{
  const end = new Date(Date.UTC(2026, 9, 27));   // 2026-10-27，往后推 30 天
  for (let i = 0; i < 30; i++) {
    thirty[end.toISOString().slice(0, 10)] = 1;
    end.setUTCDate(end.getUTCDate() + 1);
  }
}
assert.strictEqual(Object.keys(thirty).length, 30);
assert.strictEqual(Object.keys(thirty).sort()[0], '2026-10-27');
assert.strictEqual(Object.keys(thirty).sort()[29], '2026-11-25');
ok({ v: 1, source: 'pom', days: thirty });
const many = {};
for (let i = 1; i <= 31; i++) many['2026-10-' + String(i).padStart(2, '0')] = 1;
assert.ok(bad({ v: 1, source: 'pom', days: many }).indexOf('一次最多带 30 天') >= 0);

// ── 日期 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21 10:00': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-13-01': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-26': 5 } }).indexOf('未来') >= 0);
// ★ 假日期：V8 里 new Date('2026-02-30T00:00:00Z') 解析成功（得到 2026-03-02），
//   isNaN 拦不住 ⇒ 只有 isDate 里「toISOString 逐字比对」那一步能拦。
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-02-30': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-02-29': 5 } }).indexOf('日期格式') >= 0);  // 2026 不是闰年
// 日期正好是今天要通过（北京时间今天）
ok({ v: 1, source: 'pom', days: { '2026-11-25': 37 } });

// ── 数值 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': -1 } }).indexOf('负数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1.5 } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': '37' } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1441 } }).indexOf('超过上限') >= 0);
assert.ok(bad({ v: 1, source: 'words', days: { '2026-11-21': 501 } }).indexOf('超过上限') >= 0);
// 量正好等于单日上限要通过
ok({ v: 1, source: 'pom', days: { '2026-11-21': 1440 } });
ok({ v: 1, source: 'koreader', days: { '2026-11-21': 1440 } });
ok({ v: 1, source: 'words', days: { '2026-11-21': 500 } });

// ── 今天的判定：UTC+8，不是 UTC ──────────────────────────
// 2026-11-25T17:30:00Z ⇒ 北京时间已经是 11-26 了
const t = Date.parse('2026-11-25T17:30:00Z');
assert.strictEqual(V.todayCst(t), '2026-11-26');
// 2026-11-25T15:30:00Z ⇒ 北京时间还是 11-25
assert.strictEqual(V.todayCst(Date.parse('2026-11-25T15:30:00Z')), '2026-11-25');

// ── 限幅表 ───────────────────────────────────────────────
assert.deepStrictEqual(Object.keys(R.RULES).sort(), ['koreader', 'pom', 'words']);

console.log('worker 校验逻辑：全部通过');
```

- [ ] **Step 2: 跑单测，确认它失败**

Run: `node tests/worker_validate_spec.js`
Expected: 报错 `Cannot find module .../worker/lib/validate.cjs`

- [ ] **Step 3: 写实现**

创建 `worker/package.json`：

```json
{
  "name": "pokemon-dex-ingest-worker",
  "private": true,
  "type": "module",
  "description": "训练家图鉴的免登录投递接收端（Cloudflare Worker）"
}
```

创建 `worker/lib/rules.cjs`：

```javascript
'use strict';
/**
 * **单日上限表** —— 源 id → 该源一天最多能报多少原始单位。
 *
 * ★ 这张表**同时就是源白名单**：表里没有的源，报告一律拒收。
 * ★ 它必须与 `data/sources.json` 的 `sources.<id>.maxPerDay` **逐字一致**
 *   —— `tests/test_worker_rules.py` 盯着这件事，漂了就红。
 *   （跟 KOReader 插件里 `Report.MAX_MINUTES_PER_DAY` 一个套路：
 *   常量抄一份，但用测试守住它不漂。）
 *
 * 为什么不用 CommonJS 的 .json：Worker 入口是 ESM，node 里 import JSON
 * 要 import attributes，各家 bundler 支持得不齐 —— 一律用 .cjs 最省事。
 */

const RULES = {
  pom: 1440,       // 分钟（一天 24 小时）
  koreader: 1440,  // 分钟
  words: 500,      // 个
};

module.exports = { RULES };
```

创建 `worker/lib/validate.cjs`：

```javascript
'use strict';
/**
 * 免登录投递通道的**纯校验逻辑**。
 *
 * 这个文件**不碰网络、不碰 KV、不碰 Date.now 之外的任何东西** ——
 * 所以 node 可以直接 require 它跑单测（见 tests/worker_validate_spec.js），
 * 而 Worker 入口（worker/index.js，ESM）也 import 同一份 ⇒ **只有一份实现**。
 *
 * 校验清单（与服务端 scripts/collect_reports.py 的 parse_report **同构**）：
 *   1. body 是 JSON 对象
 *   2. `v` == 1
 *   3. `source` 在限幅表里（限幅表 = 白名单）
 *   4. `days` 是非空对象、条数 ≤ 30
 *   5. 每个 key 是合法 YYYY-MM-DD，且不晚于今天（**今天按 UTC+8 算**）
 *   6. 每个 value 是非负整数，且 ≤ 该源的单日上限
 *
 * **任何一条不过就整份拒收**（不部分采纳）—— 一条坏数据不该只污染一半。
 *
 * ★ 白名单查的是**自有属性**（hasOwnProperty）——
 *   直接 `table[body.source]` 会命中 `Object.prototype`
 *   （`constructor` / `__proto__` / `toString` …）⇒ 限幅层空转。
 */

const REPORT_VERSION = 1;
const MAX_REPORT_DAYS = 30;

/** 是不是整数（**布尔不算** —— JS 里没有 Python 那个 bool 是 int 子类的坑）。 */
function isInt(v) {
  return typeof v === 'number' && Number.isInteger(v);
}

/** 是不是合法的 YYYY-MM-DD（且是真实存在的日子）。 */
function isDate(s) {
  if (typeof s !== 'string' || s.length !== 10) return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(s + 'T00:00:00Z');
  if (isNaN(d.getTime())) return false;
  return d.toISOString().slice(0, 10) === s;   // 2026-02-30 这种会被这一步踢掉
}

/**
 * 北京时间（UTC+8）的今天。
 *
 * ★ 写死 +8 小时，不做时区推导：账本、图鉴页、scripts/sources.py
 *   全部按 Asia/Shanghai 分天 ⇒ Worker 若用 UTC 就会在每天 0:00~8:00
 *   （北京时间）判错一天。
 */
function todayCst(nowMs) {
  const ms = (typeof nowMs === 'number' && Number.isFinite(nowMs)) ? nowMs : Date.now();
  return new Date(ms + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

/**
 * 校验一份报告。
 * @param {*} body 客户端发来的 JSON（已解析）
 * @param {Object<string, number>} rules 源 id → 单日上限（同时是白名单）
 * @param {string} today 今天（YYYY-MM-DD，北京时间）
 * @returns {{ok: boolean, error?: string, days?: Object<string, number>, source?: string}}
 */
function validateReport(body, rules, today) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'body 不是 JSON 对象' };
  }
  if (body.v !== REPORT_VERSION) {
    return { ok: false, error: '版本不对：期望 v=1，收到 ' + JSON.stringify(body.v) };
  }
  if (typeof body.source !== 'string' || !body.source) {
    return { ok: false, error: 'source 不是源 id：' + JSON.stringify(body.source) };
  }
  // ★ 白名单必须查**自有属性**：直接 table[body.source] 会命中 Object.prototype
  //   （constructor / __proto__ / toString …）⇒ 拿到的 cap 是函数，
  //   「量比上限大」那个比较变成和 NaN 比（恒 false）⇒ 单日上限同时失效。
  const table = rules || {};
  if (!Object.prototype.hasOwnProperty.call(table, body.source)) {
    return { ok: false, error: '不认识这个源：' + body.source };
  }
  const cap = table[body.source];
  const days = body.days;
  if (!days || typeof days !== 'object' || Array.isArray(days)) {
    return { ok: false, error: 'days 不是对象：' + JSON.stringify(days) };
  }
  const keys = Object.keys(days);
  if (!keys.length) {
    return { ok: false, error: '报告里没有天数' };
  }
  if (keys.length > MAX_REPORT_DAYS) {
    return { ok: false, error: '一次最多带 ' + MAX_REPORT_DAYS + ' 天，收到 ' + keys.length + ' 天' };
  }
  const out = {};
  for (const day of keys) {
    if (!isDate(day)) {
      return { ok: false, error: '日期格式不对：' + day + '（要 YYYY-MM-DD）' };
    }
    if (day > today) {
      return { ok: false, error: '日期在未来：' + day };
    }
    const amount = days[day];
    if (!isInt(amount)) {
      return { ok: false, error: day + ' 的量不是整数：' + JSON.stringify(amount) };
    }
    if (amount < 0) {
      return { ok: false, error: day + ' 的量是负数：' + amount };
    }
    if (amount > cap) {
      return { ok: false, error: day + ' 的量 ' + amount + ' 超过上限 ' + cap };
    }
    out[day] = amount;
  }
  return { ok: true, days: out, source: body.source };
}

module.exports = { validateReport, todayCst, isInt, isDate, REPORT_VERSION, MAX_REPORT_DAYS };
```

- [ ] **Step 4: 跑单测，确认通过**

Run: `node tests/worker_validate_spec.js`
Expected: `worker 校验逻辑：全部通过`

- [ ] **Step 5: 提交**

```bash
git add worker/package.json worker/lib/validate.cjs worker/lib/rules.cjs tests/worker_validate_spec.js
git commit -m "feat: Worker 的纯校验逻辑与单日上限表（node 单测）"
```

- [ ] **Step 6: 同步 spec 里的文件名**

spec 里写的是 `worker/lib/rules.json`，实现用的是 `worker/lib/rules.cjs`（理由：Worker 入口是 ESM，node import JSON 要 import attributes，各家 bundler 支持不齐）。改 spec，让两份文件对得上：

```bash
cd /workspace/pokemon-reading-dex
sed -i 's|worker/lib/rules\.json|worker/lib/rules.cjs|g' \
  docs/superpowers/specs/2026-11-25-pokemon-dex-hub-s8-worker-ingest-design.md
git add docs/superpowers/specs/2026-11-25-pokemon-dex-hub-s8-worker-ingest-design.md
git commit -m "docs: spec 里限幅表的文件名跟上实现（rules.json → rules.cjs）"
```

---

### Task 2: Worker 的请求处理（四个端点 + CORS）

**Files:**
- Create: `worker/index.js`
- Create: `tests/worker_handler_spec.mjs`

**Interfaces:**
- Consumes: Task 1 的 `validate.cjs`（`validateReport`、`todayCst`）与 `rules.cjs`（`RULES`）
- Produces: `worker/index.js` 导出 `handleRequest(request, env)` → `Response`；默认导出 `{fetch: handleRequest}`（wrangler 要的形状）
  - `env.INGEST_KV`：KV 命名空间（`get(key,'text')` / `put(key,value,{expirationTtl})` / `delete(key)` / `list({prefix,limit})`）
  - `env.INGEST_TOKEN`：`POST /ingest` 的凭证（string）
  - `env.ADMIN_TOKEN`：`GET /pull`、`POST /ack` 的凭证（string）

- [ ] **Step 1: 写失败的 node 单测**

创建 `tests/worker_handler_spec.mjs`：

```javascript
/* Worker 入口的 node 单测：假 env + 假 KV，不联网、不碰 Cloudflare。
   跑法：node tests/worker_handler_spec.mjs */

import assert from 'node:assert';
import { handleRequest } from '../worker/index.js';

/** 假的 KV：只实现 Worker 用到的那几个方法。 */
function fakeKV() {
  const map = new Map();
  return {
    map,
    async get(key) { return map.has(key) ? map.get(key) : null; },
    async put(key, value) { map.set(key, value); },
    async delete(key) { map.delete(key); },
    async list({ prefix, limit }) {
      const keys = [...map.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name }));
      return { keys: keys.slice(0, limit) };
    },
  };
}

const ENV = { INGEST_TOKEN: 'tok-ingest', ADMIN_TOKEN: 'tok-admin' };

function post(path, body, headers) {
  return new Request('https://w.example' + path, {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
    body: JSON.stringify(body),
  });
}

async function call(req, env) {
  const resp = await handleRequest(req, env || ENV);
  const text = await resp.text();
  return { status: resp.status, data: text ? JSON.parse(text) : null, headers: resp.headers };
}

// ── CORS 预检 ────────────────────────────────────────────
{
  const resp = await handleRequest(new Request('https://w.example/ingest', { method: 'OPTIONS' }), ENV);
  assert.strictEqual(resp.status, 204);
  assert.strictEqual(resp.headers.get('Access-Control-Allow-Origin'), '*');
  assert.ok((resp.headers.get('Access-Control-Allow-Headers') || '').indexOf('X-Dex-Token') >= 0);
}

// ── ingest：凭证不对 ─────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'wrong' }), env);
  assert.strictEqual(r.status, 401);
}

// ── ingest：正常 ─────────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.ok, true);
  assert.ok(r.data.id.startsWith('q:'));
  assert.strictEqual(env.INGEST_KV.map.size, 1);
}

// ── ingest：非法 body ────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'weread', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 400);
  assert.strictEqual(r.data.ok, false);
}

// ── pull：凭证不对 / 正常 ────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const bad = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'nope' } }), env);
  assert.strictEqual(bad.status, 401);

  await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  const good = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'tok-admin' } }), env);
  assert.strictEqual(good.status, 200);
  assert.strictEqual(good.data.items.length, 1);
  assert.strictEqual(good.data.items[0].body.source, 'pom');
  assert.deepStrictEqual(good.data.items[0].body.days, { '2026-11-21': 37 });
}

// ── ack：删掉条目 ────────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const ins = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  const id = ins.data.id;
  const ack = await call(post('/ack', { ids: [id] }, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(ack.status, 200);
  assert.strictEqual(ack.data.removed, 1);
  assert.strictEqual(env.INGEST_KV.map.size, 0);
}

// ── health / 未知接口 ────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const h = await call(new Request('https://w.example/health'), env);
  assert.strictEqual(h.status, 200);
  assert.strictEqual(h.data.pending, 0);
  const u = await call(new Request('https://w.example/nope'), env);
  assert.strictEqual(u.status, 404);
}

console.log('Worker 入口：全部通过');
```

- [ ] **Step 2: 跑单测，确认它失败**

Run: `node tests/worker_handler_spec.mjs`
Expected: 报错 `Cannot find module .../worker/index.js`

- [ ] **Step 3: 写实现**

创建 `worker/index.js`：

```javascript
/**
 * 训练家图鉴 · 免登录投递接收端（Cloudflare Worker）。
 *
 * 四个端点：
 *   POST /ingest   客户端交报告（X-Dex-Token）
 *   GET  /pull     cron 拉队列（X-Dex-Admin）
 *   POST /ack      cron 确认已并账（X-Dex-Admin）
 *   GET  /health   排查用（不鉴权）
 *
 * ★ 校验逻辑**不在这里** —— 全在 lib/validate.cjs（node 也能单测），
 *   这里只做「取 header、拼 KV 读写、拼响应」。
 * ★ CORS 头**每条响应都要带**（包括 401/404）——
 *   少了浏览器会把响应整个吞掉，前端只看到「网络错误」，看不出哪里不对。
 */

import validate from './lib/validate.cjs';
import rules from './lib/rules.cjs';

const { validateReport, todayCst } = validate;
const RULES = rules.RULES;

/** 队列最多留多少条（防刷：别人拿公开的 ingest token 灌数据）。 */
const MAX_QUEUE = 200;
/** 队列条目的存活时间（秒）—— 14 天。 */
const TTL_SECONDS = 60 * 60 * 24 * 14;

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,X-Dex-Token,X-Dex-Admin',
  'Access-Control-Max-Age': '86400',
};

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: Object.assign({ 'Content-Type': 'application/json; charset=utf-8' }, CORS_HEADERS),
  });
}

function bearer(request, name) {
  return request.headers.get(name) || '';
}

async function handleIngest(request, env) {
  if (!env.INGEST_TOKEN || bearer(request, 'X-Dex-Token') !== env.INGEST_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  let body = null;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'body 不是合法 JSON' }, 400);
  }
  const v = validateReport(body, RULES, todayCst(Date.now()));
  if (!v.ok) {
    return json({ ok: false, error: v.error }, 400);
  }
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
  if (listed.keys.length >= MAX_QUEUE) {
    return json({ ok: false, error: '队列满了（' + MAX_QUEUE + ' 条）' }, 429);
  }
  const id = 'q:' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  const record = {
    id: id,
    body: { v: 1, source: v.source, days: v.days },
    receivedAt: new Date().toISOString(),
  };
  await env.INGEST_KV.put(id, JSON.stringify(record), { expirationTtl: TTL_SECONDS });
  return json({ ok: true, id: id, receivedAt: record.receivedAt });
}

async function handlePull(request, env) {
  if (!env.ADMIN_TOKEN || bearer(request, 'X-Dex-Admin') !== env.ADMIN_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
  const items = [];
  for (const key of listed.keys) {
    const raw = await env.INGEST_KV.get(key.name, 'text');
    if (!raw) continue;
    try {
      items.push(JSON.parse(raw));
    } catch (e) {
      // 坏条目跳过（别让一条垃圾把整批拉取搞挂）
    }
  }
  return json({ ok: true, items: items });
}

async function handleAck(request, env) {
  if (!env.ADMIN_TOKEN || bearer(request, 'X-Dex-Admin') !== env.ADMIN_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  let body = null;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'body 不是合法 JSON' }, 400);
  }
  const ids = Array.isArray(body.ids) ? body.ids : [];
  let removed = 0;
  for (const id of ids) {
    if (typeof id !== 'string' || !id.startsWith('q:')) continue;
    await env.INGEST_KV.delete(id);
    removed++;
  }
  return json({ ok: true, removed: removed });
}

async function handleHealth(env) {
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
  return json({ ok: true, pending: listed.keys.length, source: 'pokemon-dex-ingest' });
}

/** 路由。★ `OPTIONS` 要在所有路径之前处理（预检不带路径语义）。 */
export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (path === '/ingest' && request.method === 'POST') return handleIngest(request, env);
  if (path === '/pull' && request.method === 'GET') return handlePull(request, env);
  if (path === '/ack' && request.method === 'POST') return handleAck(request, env);
  if (path === '/health' && request.method === 'GET') return handleHealth(env);
  return json({ ok: false, error: '没有这个接口' }, 404);
}

export default { fetch: handleRequest };
```

- [ ] **Step 4: 跑单测，确认通过**

Run: `node tests/worker_handler_spec.mjs`
Expected: `Worker 入口：全部通过`

- [ ] **Step 5: 提交**

```bash
git add worker/index.js tests/worker_handler_spec.mjs
git commit -m "feat: Worker 的四个端点与 CORS（假 KV 的 node 单测）"
```

---

### Task 3: 注册表加 `ingest` 段（先关着）

**Files:**
- Modify: `data/sources.json`
- Modify: `scripts/sources.py`（`validate_config`）
- Create: `tests/test_worker_rules.py`

**Interfaces:**
- Consumes: 无
- Produces: `data/sources.json` 顶层多一个 `ingest` 段：
  `{"url": string, "token": string, "enabled": boolean}`
  —— 客户端从**同一份** `sources.json` 里读它（三个子页本来就在 fetch 它 ⇒ **零新增网络请求**）

- [ ] **Step 1: 写失败的 Python 测试**

创建 `tests/test_worker_rules.py`：

```python
"""Worker 的限幅表不许跟服务端注册表漂移；时区口径也要对得上。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_rules
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_reports as cr  # noqa: E402
from scripts import sources as src  # noqa: E402

RULES_CJS = ROOT / "worker" / "lib" / "rules.cjs"
REGISTRY = ROOT / "data" / "sources.json"


def _node() -> str | None:
    """找 node —— 找不到就优雅跳过（不能让整套测试挂掉）。"""
    return shutil.which("node")


def _rules() -> dict:
    """让 node 把限幅表吐成 JSON —— .cjs 里是 JS 字面量，Python 读不了。"""
    node = _node()
    if not node:
        raise unittest.SkipTest("找不到 node，跳过限幅表对拍")
    code = "console.log(JSON.stringify(require(process.argv[1]).RULES))"
    out = subprocess.run([node, "-e", code, str(RULES_CJS)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError("node 读限幅表失败：%s" % out.stderr.strip())
    return json.loads(out.stdout.strip())


class WorkerRulesTest(unittest.TestCase):
    """限幅表 = 源白名单 = 单日上限。"""

    def test_the_rules_file_exists(self):
        self.assertTrue(RULES_CJS.exists(), "缺 %s" % RULES_CJS)

    def test_the_rules_match_the_server_side_registry(self):
        """★ 限幅表与 data/sources.json 的 maxPerDay 必须逐字一致。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        sources = cfg.get("sources") or {}
        rules = _rules()
        # 白名单 = 注册表里所有 push 源（不多不少）
        push_sources = sorted(sid for sid, s in sources.items() if s.get("mode") == "push")
        self.assertEqual(sorted(rules.keys()), push_sources,
                         "限幅表的白名单与注册表里的 push 源对不上")
        for sid, cap in rules.items():
            self.assertEqual(sources[sid].get("maxPerDay"), cap,
                             "源 %s 的限幅漂了：Worker 说 %s，注册表说 %s"
                             % (sid, cap, sources[sid].get("maxPerDay")))

    def test_the_rules_only_cover_push_sources(self):
        """白名单里不该出现 pull 源（微信读书是服务端自己去拉的）。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        sources = cfg.get("sources") or {}
        for sid in _rules():
            self.assertEqual(sources[sid].get("mode"), "push",
                             "源 %s 不是 push 源，不该在投递白名单里" % sid)

    def test_the_registry_has_the_ingest_block(self):
        """注册表顶层要有 ingest 段（客户端从这儿读 URL 与 token）。"""
        cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertIn("ingest", cfg, "sources.json 顶层缺 ingest 段")
        ingest = cfg["ingest"]
        self.assertIsInstance(ingest.get("enabled"), bool, "ingest.enabled 必须是布尔")
        self.assertIsInstance(ingest.get("url"), str, "ingest.url 必须是字符串")
        self.assertIsInstance(ingest.get("token"), str, "ingest.token 必须是字符串")

    def test_the_worker_uses_beijing_time(self):
        """★ Worker 判「今天」用 UTC+8 —— 与账本口径一致。"""
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过时区口径检查")
        code = ("const V=require(process.argv[1]);"
                "console.log(V.todayCst(Date.parse('2026-11-25T17:30:00Z')));"
                "console.log(V.todayCst(Date.parse('2026-11-25T15:30:00Z')))")
        out = subprocess.run([node, "-e", code, str(ROOT / "worker" / "lib" / "validate.cjs")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "2026-11-26", "17:30Z 已经是北京时间的第二天了")
        self.assertEqual(lines[1], "2026-11-25", "15:30Z 还是北京时间当天")

    def test_the_worker_and_the_server_agree_on_what_is_valid(self):
        """★ **跨语言对拍**：同一批用例跑 Worker 的 JS 与服务端的 Python，
        两边结论必须一致 —— 一边收一边拒 ⇒ 会有一边静默丢数据。"""
        node = _node()
        if not node:
            raise unittest.SkipTest("找不到 node，跳过跨语言对拍")
        today = "2026-11-25"
        cases = [
            {"v": 1, "source": "pom", "days": {"2026-11-21": 37}},     # 收
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1440}},   # 收（正好上限）
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1441}},   # 拒（超上限）
            {"v": 1, "source": "pom", "days": {}},                      # 拒（没有天数）
            {"v": 1, "source": "pom", "days": {"2026-11-21": -1}},     # 拒（负数）
            {"v": 1, "source": "pom", "days": {"2026-11-21": 1.5}},    # 拒（不是整数）
            {"v": 1, "source": "pom", "days": {"2026-11-26": 5}},      # 拒（未来）
            {"v": 1, "source": "pom", "days": {"2026-13-01": 5}},      # 拒（假日期）
            {"v": 1, "source": "weread", "days": {"2026-11-21": 37}},  # 拒（pull 源）
            {"v": 1, "source": "words", "days": {"2026-11-21": 500}},  # 收（正好上限）
            {"v": 1, "source": "words", "days": {"2026-11-21": 501}},  # 拒（超上限）
            {"v": 2, "source": "pom", "days": {"2026-11-21": 37}},     # 拒（版本不对）
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(cases, fh, ensure_ascii=False)
            path = fh.name
        try:
            code = ("const V=require(process.argv[1]);const R=require(process.argv[2]);"
                    "const cases=JSON.parse(require('fs').readFileSync(process.argv[3],'utf8'));"
                    "const today=process.argv[4];"
                    "console.log(JSON.stringify(cases.map("
                    "c=>V.validateReport(c,R.RULES,today).ok)));")
            out = subprocess.run([node, "-e", code,
                                  str(ROOT / "worker" / "lib" / "validate.cjs"),
                                  str(RULES_CJS), path, today],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            js_flags = json.loads(out.stdout.strip())
        finally:
            os.unlink(path)

        cfg = src.load_config(REGISTRY)
        self.assertEqual(len(js_flags), len(cases))
        for idx, case in enumerate(cases):
            body = json.dumps(case, ensure_ascii=False)
            _days, err = cr.parse_report(body, cfg, today)
            py_ok = (err is None)
            self.assertEqual(bool(js_flags[idx]), py_ok,
                             "第 %d 条两边结论不一致：JS 说 %s，Python 说 %s（%s）"
                             % (idx, js_flags[idx], py_ok, err))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python3 -B -m tests.test_worker_rules`
Expected: FAIL —— `KeyError: 'ingest'` 或「缺 worker/lib/rules.cjs」（Task 1 已建 rules.cjs，所以这条会挂在 ingest 段上）

- [ ] **Step 3: 给注册表加 `ingest` 段**

编辑 `data/sources.json`，在顶层加：

```json
  "ingest": {
    "url": "",
    "token": "",
    "enabled": false
  },
```

（**先关着**：`url` 与 `token` 是空串、`enabled` 是 false ⇒
客户端认得这个字段但**不用它**（照旧走 GitHub 老路）⇒ 部署好了再回来填真值。
**不许**在这里写「TBD」之类的占位符 —— 空串就是空串。）

- [ ] **Step 4: 给 `scripts/sources.py` 加 `ingest` 段的校验**

在 `scripts/sources.py` 的 `validate_config` 末尾（`for sid, s in sources.items():` 那个循环之后）加：

```python
    # 免登录投递通道的配置（顶层 ingest 段）。
    # **可选** —— 没这一段就当通道没开（客户端走 GitHub 老路）。
    # 写了就必须是能用的形状：enabled 是布尔、url 与 token 是字符串。
    # ⚠️ 这里**不校验 url 非空** —— 通道关着的时候 url 就是空串。
    if "ingest" in cfg:
        ingest = cfg["ingest"]
        if not isinstance(ingest, dict):
            raise SourceConfigError("sources.json 的 ingest 必须是对象")
        if not isinstance(ingest.get("enabled"), bool):
            raise SourceConfigError("ingest.enabled 必须是布尔值，实际 %r" % ingest.get("enabled"))
        for key in ("url", "token"):
            if not isinstance(ingest.get(key), str):
                raise SourceConfigError("ingest.%s 必须是字符串，实际 %r" % (key, ingest.get(key)))
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `python3 -B -m tests.test_worker_rules`
Expected: 6 tests OK

- [ ] **Step 6: 跑整套测试，确认没弄坏别的**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK（基线 661 + 新增 5）

- [ ] **Step 7: 提交**

```bash
git add data/sources.json scripts/sources.py tests/test_worker_rules.py
git commit -m "feat: 源注册表加 ingest 段（先关着），并校验它的形状"
```

---

### Task 4: 服务端接收端 `scripts/collect_worker.py`

**Files:**
- Create: `scripts/collect_worker.py`
- Create: `tests/test_worker_collect.py`

**Interfaces:**
- Consumes: `scripts.collect_reports.parse_report(body, cfg, today)`、`scripts.ledger.merge_amounts(ledger, source_id, days)`、`scripts.sources.load_config` / `day_credit`
- Produces:
  - `pull_items(url, admin_token, timeout=30)` → `list[dict]`（每条 `{"id","body","receivedAt"}`；拉不到**抛异常**）
  - `ack_items(url, admin_token, ids, timeout=30)` → `int`（删除的条数）
  - `process_items(items, cfg, today, ledger)` → `(新账本, 结果列表)`（**纯函数**）
  - `main(argv=None)` → `int`

- [ ] **Step 1: 写失败的 Python 测试**

创建 `tests/test_worker_collect.py`：

```python
"""服务端接收端（scripts/collect_worker.py）的单测。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_collect
"""

from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_worker as cw
from scripts import sources as src

REGISTRY = ROOT / "data" / "sources.json"


def _cfg():
    return src.load_config(REGISTRY)


class ProcessItemsTest(unittest.TestCase):
    """队列条目 → 账本。"""

    def test_an_accepted_item_merges_into_the_ledger(self):
        cfg = _cfg()
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "accepted")
        self.assertEqual(results[0]["source"], "pom")
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_the_same_day_takes_the_max_not_the_sum(self):
        """同源同一天取 max —— 重复投递是幂等的。"""
        cfg = _cfg()
        items = [
            {"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}},
            {"id": "q:2", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 20}}},
        ]
        ledger, _ = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_an_illegal_item_is_rejected_but_kept_in_the_results(self):
        """非法条目不并账，但**要出现在结果里**（主流程据此把它 ack 掉）。"""
        cfg = _cfg()
        items = [{"id": "q:bad", "body": {"v": 1, "source": "weread", "days": {"2026-11-21": 37}}}]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "rejected")
        self.assertEqual(results[0]["id"], "q:bad")
        self.assertTrue(results[0]["reason"], "拒收必须写清原因")
        self.assertEqual(ledger, {})

    def test_a_broken_item_does_not_kill_the_batch(self):
        cfg = _cfg()
        items = [
            {"id": "q:bad", "body": None},
            {"id": "q:ok", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}},
        ]
        ledger, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(len(results), 2)
        self.assertEqual(ledger["2026-11-21"]["pom"], 37)

    def test_the_credit_is_computed_the_same_way_as_the_issue_channel(self):
        """折算出的额度必须与 Issue 通道一致（同一个 day_credit）。"""
        cfg = _cfg()
        items = [{"id": "q:1", "body": {"v": 1, "source": "pom", "days": {"2026-11-21": 37}}}]
        _, results = cw.process_items(items, cfg, "2026-11-25", {})
        self.assertEqual(results[0]["credits"], {"2026-11-21": 1})


class MainBehaviourTest(unittest.TestCase):
    """没配置 / 拉不到 ⇒ 跳过，**绝不阻塞结算**。"""

    def test_it_skips_when_the_channel_is_not_configured(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("WORKER_URL", "WORKER_ADMIN_TOKEN")}
        try:
            self.assertEqual(cw.main([]), 0)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_a_broken_endpoint_is_skipped_not_fatal(self):
        import os
        os.environ["WORKER_URL"] = "http://127.0.0.1:9/not-there"
        os.environ["WORKER_ADMIN_TOKEN"] = "x"
        old_date = os.environ.get("DEX_DATE")
        try:
            # 拉不到 ⇒ 打印警告并 exit 0（不阻塞发奖）
            self.assertEqual(cw.main([]), 0)
        finally:
            os.environ.pop("WORKER_URL", None)
            os.environ.pop("WORKER_ADMIN_TOKEN", None)
            if old_date is not None:
                os.environ["DEX_DATE"] = old_date


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python3 -B -m tests.test_worker_collect`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.collect_worker'`

- [ ] **Step 3: 写实现**

创建 `scripts/collect_worker.py`：

```python
#!/usr/bin/env python3
"""免登录投递通道（Cloudflare Worker）的服务端接收端。

    python3 scripts/collect_worker.py            # 拉队列 → 校验 → 并账本 → ack
    python3 scripts/collect_worker.py --dry-run  # 只打印，不写盘不 ack

**为什么多这一条通道**：push 源（番茄钟 / 背单词 / KOReader）原来的投递方式是
「客户端打开 GitHub 新建 issue 页，人点 Submit」—— 要登录、要点两次。
Worker 通道让客户端**点一下就完事**，服务端照样只认自己算出来的账。

**信任边界**：ingest token 写在她网站公开的 JS 里 ⇒ 这条通道天生「任何人可写」。
所以：
  1. Worker 侧限幅（源白名单 / 单日上限 / ≤30 天 / 队列 ≤200 条）
  2. 服务端**再校验一遍**（复用 collect_reports.parse_report —— 与 Issue 通道同构）
  3. 并账本取 max（同源同一天）⇒ 重复投递幂等
  4. 结算规则不变（perAward=2）⇒ 一天最多掉 1 只，被刷最多让图鉴走得快一点

**结算核心一行不改** —— 这里只负责「把远端数据变成账本里的量」。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import collect_reports as cr  # noqa: E402
from scripts import ledger as lg  # noqa: E402
from scripts import sources as src  # noqa: E402

DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
SOURCES_PATH = pathlib.Path(os.environ.get("SOURCES_PATH", ROOT / "data" / "sources.json"))


def _http(url: str, method: str = "GET", payload=None, headers=None, timeout: int = 30):
    """发一个 HTTP 请求，返回 (status, 解析后的 JSON 或原始文本)。

    连不上时返回 `(0, 原因字符串)` —— **不抛异常**，
    好让上层把「通道不通」和「数据有问题」分开说。
    """
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data)
    for k, v in hdrs.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 —— 连不上/超时/证书…全归一类
        return 0, str(exc)


def pull_items(url: str, admin_token: str, timeout: int = 30) -> list[dict]:
    """从 Worker 拉待处理的队列。**拉不到就抛异常**（调用方负责跳过）。"""
    status, data = _http(url.rstrip("/") + "/pull", "GET",
                         headers={"X-Dex-Admin": admin_token}, timeout=timeout)
    if status != 200:
        raise RuntimeError("拉队列失败：HTTP %s %s" % (status, data))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError("拉队列返回的不是预期形状：%s" % data)
    return data["items"]


def ack_items(url: str, admin_token: str, ids, timeout: int = 30) -> int:
    """确认已并账，把这些条目从队列里删掉。"""
    ids = [i for i in (ids or []) if isinstance(i, str) and i]
    if not ids:
        return 0
    status, data = _http(url.rstrip("/") + "/ack", "POST", payload={"ids": ids},
                         headers={"X-Dex-Admin": admin_token}, timeout=timeout)
    if status != 200:
        raise RuntimeError("ack 失败：HTTP %s %s" % (status, data))
    return len(ids)


def process_items(items, cfg: dict, today: str, ledger: dict):
    """把一批队列条目处理掉，返回 `(新账本, 结果列表)`。

    **纯函数**（不碰网络、不碰文件）⇒ 可以直接单测。

    结果每条：
      {"id": str|None, "action": "accepted"|"rejected", "reason": str|None,
       "source": str|None, "days": dict, "credits": dict}

    合并用 `ledger.merge_amounts`（同源同一天取 max）⇒ 重复投递是幂等的。
    """
    new_ledger = ledger
    results = []
    for item in items or []:
        iid = item.get("id") if isinstance(item, dict) else None
        body = item.get("body") if isinstance(item, dict) else None
        raw = json.dumps(body, ensure_ascii=False) if body is not None else ""
        days, err = cr.parse_report(raw, cfg, today)
        if err:
            # 校验不过 ⇒ 不并账。**但仍然会被 ack 掉**（主流程负责）——
            # Worker 已经限过幅，这里失败说明两边规则漂了，要修的是代码，
            # 留着它只会让每轮都拉一遍、拉不动的噪音。
            results.append({"id": iid, "action": "rejected", "reason": err,
                            "source": None, "days": {}, "credits": {}})
            continue
        source = body.get("source")
        new_ledger = lg.merge_amounts(new_ledger, source, days)
        credits = {d: src.day_credit(cfg, {source: v}) for d, v in days.items()}
        results.append({"id": iid, "action": "accepted", "reason": None,
                        "source": source, "days": days, "credits": credits})
    return new_ledger, results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从免登录投递通道（Worker）收报告并进账本")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘不 ack")
    ap.add_argument("--date", default=None, help="今天（YYYY-MM-DD），默认取北京时间今天")
    args = ap.parse_args(argv)

    url = os.environ.get("WORKER_URL")
    admin_token = os.environ.get("WORKER_ADMIN_TOKEN")
    if not url or not admin_token:
        print("[投递] 未配置 WORKER_URL / WORKER_ADMIN_TOKEN，跳过")
        return 0

    today = args.date or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

    try:
        cfg = src.load_config(SOURCES_PATH)
    except src.SourceConfigError as exc:
        print("[投递] 源注册表有问题：%s" % exc, file=sys.stderr)
        return 1

    if not DEX_PATH.exists():
        print("[投递] 找不到状态文件 %s" % DEX_PATH, file=sys.stderr)
        return 1

    try:
        items = pull_items(url, admin_token)
    except Exception as exc:  # noqa: BLE001
        # ★ 拉不到就跳过 —— **绝不阻塞发奖**（这是旁路）。
        print("[投递] 拉取失败：%s，跳过" % exc, file=sys.stderr)
        return 0

    if not items:
        print("[投递] 队列是空的")
        return 0

    state = json.loads(DEX_PATH.read_text(encoding="utf-8"))
    ledger = state.get("ledger") or {}
    new_ledger, results = process_items(items, cfg, today, ledger)

    accepted = [r for r in results if r["action"] == "accepted"]
    for r in results:
        if r["action"] == "accepted":
            print("[投递] 采纳 %s %s %s → 额度 %s"
                  % (r["id"], r["source"], r["days"], r["credits"]))
        else:
            print("[投递] 拒收 %s %s" % (r["id"], r["reason"]), file=sys.stderr)

    if not accepted:
        print("[投递] 没有可采纳的报告，账本没变")
        # 非法条目也要 ack 掉（别让每轮都拉同一批垃圾）
        if not args.dry_run:
            try:
                ack_items(url, admin_token, [r["id"] for r in results])
            except Exception as exc:  # noqa: BLE001
                print("[投递] ack 失败：%s" % exc, file=sys.stderr)
        return 0

    if args.dry_run:
        print("[dry-run] 不写盘、不 ack")
        return 0

    if new_ledger != ledger:
        state["ledger"] = new_ledger
        DEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEX_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print("账本已更新：%s 天" % len(new_ledger))

    try:
        removed = ack_items(url, admin_token, [r["id"] for r in results])
        print("[投递] 已 ack %s 条" % removed)
    except Exception as exc:  # noqa: BLE001
        # ack 失败不要紧：条目还在队列里，下轮会重拉 ⇒ 并账取 max ⇒ 幂等不多算。
        print("[投递] ack 失败（下轮会重拉，账本取 max 不会多算）：%s" % exc, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python3 -B -m tests.test_worker_collect`
Expected: 7 tests OK

- [ ] **Step 5: 跑整套测试**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK（661 + 5 + 7）

- [ ] **Step 6: 提交**

```bash
git add scripts/collect_worker.py tests/test_worker_collect.py
git commit -m "feat: 服务端接收端 collect_worker.py（拉队列 → 校验 → 并账本 → ack）"
```

---

### Task 5: 客户端 · 番茄钟页

**Files:**
- Modify: `site/pomodoro/app.js`（`loadList()` 里读 ingest 段；新增投递函数；改 `doSync()`；改 `renderSync()` 的提示）
- Create: `tests/test_worker_client.py`
- （`site/pomodoro/index.html` **不用改** —— 老办法链接由 JS 渲染进 `#syncHint`）

**Interfaces:**
- Consumes: `data/sources.json` 顶层 `ingest` 段（Task 3 已加，Task 8 填真值）
- Produces: 页面里三个新东西 ——
  - `INGEST`（模块级变量）：从 `sources.json` 读来的 ingest 配置
  - `postIngest(report)` → `Promise<{ok:true, id:string}>`（失败抛异常）
  - `pendingQueue()` / `savePending(list)` / `enqueueReport(report)` / `shiftPending()` / `flushPending()`

- [ ] **Step 1: 写失败的契约测试**

创建 `tests/test_worker_client.py`（本任务先只写番茄钟那部分，后面两个任务往里加）：

```python
"""三个客户端页面的契约断言：投递通道的形状、老办法入口、待发队列。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_client
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

POM_DIR = ROOT / "site" / "pomodoro"
POM_JS = POM_DIR / "app.js"
POM_HTML = POM_DIR / "index.html"


class PomodoroIngestTest(unittest.TestCase):
    """番茄钟页：点同步直接交上去。"""

    def test_the_page_posts_to_the_ingest_endpoint(self):
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("postIngest", js, "要有 postIngest")
        self.assertIn("X-Dex-Token", js, "要带 ingest token 头")
        self.assertIn("method: 'POST'", js, "要用 POST")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置来自**已经 fetch 的** sources.json ⇒ 不许新增网络请求。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"d\.ingest|\['ingest'\]", js) is not None,
                        "要从 sources.json 顶层读 ingest 段")
        # 页面本来就有 3 个 fetch（pokedex-lite.json / dex.json / sources.json），
        # 加了投递之后最多 4 个 —— **不许再多来一个注册表请求**
        self.assertLessEqual(len(re.findall(r"fetch\(", js)), 4,
                             "番茄钟页的网络请求不该变多")

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        """老办法不许删 —— workers.dev 在国内可能抽风。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("buildIssueUrl", js, "老办法的 URL 构造要留着")
        self.assertIn("issues/new", js, "老办法的 GitHub 链接要留着")
        self.assertIn("syncOldWay", js, "老办法链接要有固定的 id")
        self.assertIn("ingestAvailable", js, "通道没开时要能退回老办法")

    def test_the_page_queues_failed_reports(self):
        """交不上去要存下来，下次打开自动补发。"""
        js = POM_JS.read_text(encoding="utf-8")
        self.assertIn("pendingIngest", js, "要有待发队列")
        self.assertIn("flushPending", js, "页面打开时要补发")
        self.assertIn("localStorage", js, "待发队列要落 localStorage")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python3 -B -m tests.test_worker_client`
Expected: FAIL —— `AssertionError: 要有 postIngest`

- [ ] **Step 3: 改 `site/pomodoro/app.js`**

**(a) 顶部加常量**（放在 `const ISSUE_NEW = ...` 那几行下面）：

```javascript
/* ---------- 免登录投递通道 ---------- */
/** 待发队列的 localStorage 键 —— 交不上去的先存这儿，下次打开页面自动补发。 */
const PENDING_KEY = 'pendingIngest';
/** 单次投递的超时（毫秒） */
const INGEST_TIMEOUT_MS = 15000;
/** 免登录通道的配置（从 sources.json 顶层读，见 loadList） */
let INGEST = null;
```

**(b) 改 `loadList()` 里 sources.json 那一段**（现在只有 `UNIT_PER_CREDIT`）：

```javascript
    .then(d => {
      const per = d && d.sources && d.sources[SOURCE_ID] && d.sources[SOURCE_ID].per;
      if (per > 0) { UNIT_PER_CREDIT = per; renderSync(); }
      // 免登录通道的配置 —— **同一份 sources.json**，不再多发一个请求
      INGEST = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
      // 配置到了 ⇒ 把上次没交上去的补发掉
      flushPending();
    })
```

**(c) 新增投递函数**（放在 `buildIssueUrl()` 后面）：

```javascript
/* ---------- 投递（免登录通道） ---------- */

/** 读待发队列（坏数据当空队列）。 */
function pendingQueue() {
  try {
    const raw = window.localStorage.getItem(PENDING_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}
/** 写待发队列（只留最近 50 条）。 */
function savePending(list) {
  try {
    window.localStorage.setItem(PENDING_KEY, JSON.stringify((list || []).slice(-50)));
  } catch (e) { /* 存不住就算了 —— 顶多这次没交上 */ }
}
/** 把一份报告排进待发队列。 */
function enqueueReport(report) {
  const list = pendingQueue();
  list.push(report);
  savePending(list);
}
/** 取出队首（没有就 null）。 */
function shiftPending() {
  const list = pendingQueue();
  if (!list.length) return null;
  const head = list.shift();
  savePending(list);
  return head;
}

/**
 * 把一份报告 POST 到免登录通道。
 * 成功返回服务端的回包；失败**抛异常**（调用方负责提示与排队）。
 */
async function postIngest(report) {
  if (!ingestAvailable()) {
    throw new Error('免登录通道没开');
  }
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, INGEST_TIMEOUT_MS);
  let resp = null;
  try {
    resp = await fetch(INGEST.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dex-Token': INGEST.token || '' },
      body: JSON.stringify(report),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = null; }
  if (!resp.ok || !data || data.ok !== true) {
    throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
  }
  return data;
}

/** 免登录通道现在能不能用？（配置在、开着、有地址） */
function ingestAvailable() {
  return !!(INGEST && INGEST.enabled === true && INGEST.url);
}

/** 把上次没交上去的补发掉（页面打开时调一次）。 */
async function flushPending() {
  if (!ingestAvailable()) return;      // 通道没开 ⇒ 没有可补的（也别把它越攒越多）
  let sent = 0;
  let report = shiftPending();
  while (report) {
    try {
      await postIngest(report);
      sent++;
    } catch (e) {
      enqueueReport(report);   // 还是交不上去 ⇒ 放回队首，下次再说
      break;
    }
    report = shiftPending();
  }
  if (sent) { toast('补发了 ' + sent + ' 条', 3000); renderSync(); }
}
```

**(d) 改 `doSync()`**：

```javascript
function doSync() {
  const days = syncDays();
  if (!Object.keys(days).length) { toast('还没有能交上去的分钟数'); return; }
  // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
  //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点同步只会报错。
  if (!ingestAvailable()) {
    window.open(buildIssueUrl(), '_blank', 'noopener');
    S().lastSyncAt = Date.now();
    S().syncedDays = Object.keys(days);
    Store.write();
    renderSync();
    toast('已打开提交页 —— 在 GitHub 那个页面上点一下 Submit', 4200);
    return;
  }
  const report = buildReport();
  postIngest(report).then(function () {
    S().lastSyncAt = Date.now();
    S().syncedDays = Object.keys(days);
    Store.write();
    renderSync();
    toast('已记录 ' + Object.keys(days).length + ' 天', 3000);
  }).catch(function (err) {
    enqueueReport(report);
    renderSync();
    toast('暂时交不上去（' + (err && err.message ? err.message : '未知原因') +
      '）—— 已存着，下次打开自动补发', 4200);
  });
}
```

**(e) 改 `renderSync()` 的提示**（最后那个 `hint.innerHTML = ...`）：

```javascript
  const hint = $('#syncHint');
  if (hint) {
    const oldWay = '<a id="syncOldWay" href="' + esc(buildIssueUrl()) +
      '" target="_blank" rel="noopener noreferrer">用 GitHub 提交（老办法）</a>';
    hint.innerHTML = dayCount
      ? '点「同步到图鉴」直接交上去（不用登录 GitHub）。连不上？' + oldWay
      : '还没有攒下分钟数 —— 完成一轮专注再来。';
  }
```

⚠️ **`renderSync()` 里 `buildIssueUrl()` 只在有量时调用** —— 没有量时 `dayCount` 为 0，走 else 分支，不会构造链接 ✓。

- [ ] **Step 4: 确认 `site/pomodoro/index.html` 不用改**

老办法链接由 JS 渲染进 `#syncHint` 里（`renderSync()`）⇒ **HTML 一个字都不用动**。
确认一下那个提示容器本来就在：

```bash
grep -n 'id="syncHint"' site/pomodoro/index.html
```

Expected: 有一行含 `id="syncHint"`（`site/pomodoro/index.html:61` 已经有）✓

- [ ] **Step 5: 跑测试，确认通过**

Run: `python3 -B -m tests.test_worker_client`
Expected: 4 tests OK

- [ ] **Step 6: 跑整套测试**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK（661 + 5 + 7 + 4）

- [ ] **Step 7: 提交**

```bash
git add site/pomodoro/app.js tests/test_worker_client.py
git commit -m "feat: 番茄钟页的同步改成直传免登录通道（交不上去就排队，下次补发）"
```

---

### Task 6: 客户端 · 背单词页

**Files:**
- Modify: `site/words/app.js`
- Modify: `site/words/index.html`
- Modify: `tests/test_worker_client.py`

**Interfaces:**
- Consumes: Task 5 那套同名函数（**每个子页各写一份** —— 子页只许引一个 `<script>`）
- Produces: 背单词页的 `postIngest` / `pendingQueue` / `savePending` / `enqueueReport` / `shiftPending` / `flushPending`；`state.ingest`

- [ ] **Step 1: 写失败的契约测试**

在 `tests/test_worker_client.py` 里加：

```python
WORDS_DIR = ROOT / "site" / "words"
WORDS_JS = WORDS_DIR / "app.js"
WORDS_HTML = WORDS_DIR / "index.html"


class WordsIngestTest(unittest.TestCase):
    """背单词页：点「交上去」直接交。"""

    def test_the_page_posts_to_the_ingest_endpoint(self):
        js = WORDS_JS.read_text(encoding="utf-8")
        self.assertIn("postIngest", js, "要有 postIngest")
        self.assertIn("X-Dex-Token", js, "要带 ingest token 头")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        js = WORDS_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"d\.ingest|\['ingest'\]", js) is not None,
                        "要从 sources.json 顶层读 ingest 段")
        self.assertLessEqual(len(re.findall(r"fetch\(", js)), 2,
                             "背单词页的网络请求不该变多（只有 sources.json 一个）")

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        js = WORDS_JS.read_text(encoding="utf-8")
        self.assertIn("buildIssueUrl", js, "老办法的 URL 构造要留着")
        html = WORDS_HTML.read_text(encoding="utf-8")
        self.assertIn("oldWayBtn", html, "页面里要有老办法链接")

    def test_the_submit_button_is_a_button_not_a_link(self):
        """主按钮改成 <button>（它不再跳去 GitHub）。"""
        html = WORDS_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'<button[^>]*id="submitBtn"')
        self.assertNotRegex(html, r'<a[^>]*id="submitBtn"')

    def test_the_page_queues_failed_reports(self):
        js = WORDS_JS.read_text(encoding="utf-8")
        self.assertIn("pendingIngest", js)
        self.assertIn("flushPending", js)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python3 -B -m tests.test_worker_client`
Expected: FAIL —— `AssertionError: 要有 postIngest`

- [ ] **Step 3: 改 `site/words/index.html`**

把

```html
  <a class="btn" id="submitBtn" href="#" target="_blank" rel="noopener noreferrer">去 GitHub 提交</a>
  <p class="hint center" id="submitHint">点开是 GitHub 的新建 issue 页，标题和内容都填好了 —— 在那个页面上点一下 <b>Submit</b> 才算交上去。</p>
```

换成

```html
  <button class="btn" id="submitBtn" type="button">交上去</button>
  <p class="hint center" id="submitHint">点「交上去」直接交（不用登录 GitHub）。</p>
  <p class="hint center"><a id="oldWayBtn" href="#" target="_blank" rel="noopener noreferrer">用 GitHub 提交（老办法）</a></p>
```

（`.btn` 是**类选择器**（`site/words/style.css:116`），`<button class="btn">` 样式一样 ✓。）

同时把下面那块「点完 Submit 之后」的说明改一改（它讲的是老路）：

```html
  <section class="card">
    <h2>交上去之后</h2>
    <ol class="steps">
      <li>老办法会把这条报告发到 <b>lilia-shound/pokemon-dex</b> 这个仓库的 issue，标题长这样：<code id="issueTitle">[report] words …</code>。</li>
      <li>服务端的定时任务读它 → 校验（源、日期、数值）→ 并进逐日账本（同源同一天取最大值，重复交不吃亏）。</li>
      <li>当天额度凑够了，就从还没收服的池子里掉一只宝可梦，进你的图鉴。</li>
```

（只改标题与第一条的措辞，其余不动。）

- [ ] **Step 4: 改 `site/words/app.js`**

**(a) 顶部加常量**（放在 `const ISSUE_NEW = ...` 下面）：

```javascript
  /* ---------- 免登录投递通道 ---------- */
  /** 待发队列的 localStorage 键 —— 交不上去的先存这儿，下次打开页面自动补发。 */
  const PENDING_KEY = 'pendingIngest';
  /** 单次投递的超时（毫秒） */
  const INGEST_TIMEOUT_MS = 15000;
```

**(b) `state` 里加一个字段**：

```javascript
  const state = {
    days: {}, selected: todayKey(),
    per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, perAward: DEFAULT_PER_AWARD,
    registryLoaded: false, storageOk: true,
    enabled: true, linkNote: '',
    draft: null,
    ingest: null,        // 免登录通道的配置（从 sources.json 顶层读）
  };
```

**(c) `loadRegistry()` 里读 ingest**（在 `.then(d => {` 里，`return {...}` 之前）：

```javascript
        state.ingest = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
```

并在 `.catch` 里也置 `state.ingest = null;`（拉不到注册表 ⇒ 没有通道配置 ⇒ 走老路）。

**(d) 新增投递函数**（放在 `buildIssueUrl()` 后面）—— 与番茄钟那份**逐字相同**（只有命名空间不同，本文件在 IIFE 里）：

```javascript
  /** 读待发队列（坏数据当空队列）。 */
  function pendingQueue() {
    try {
      const raw = window.localStorage.getItem(PENDING_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  /** 写待发队列（只留最近 50 条）。 */
  function savePending(list) {
    try {
      window.localStorage.setItem(PENDING_KEY, JSON.stringify((list || []).slice(-50)));
    } catch (e) { /* 存不住就算了 */ }
  }
  /** 把一份报告排进待发队列。 */
  function enqueueReport(report) {
    const list = pendingQueue();
    list.push(report);
    savePending(list);
  }
  /** 取出队首（没有就 null）。 */
  function shiftPending() {
    const list = pendingQueue();
    if (!list.length) return null;
    const head = list.shift();
    savePending(list);
    return head;
  }

  /** 把一份报告 POST 到免登录通道。成功返回回包，失败**抛异常**。 */
  async function postIngest(report) {
    if (!ingestAvailable()) throw new Error('免登录通道没开');
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, INGEST_TIMEOUT_MS);
    let resp = null;
    try {
      resp = await fetch(state.ingest.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dex-Token': state.ingest.token || '' },
        body: JSON.stringify(report),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    const text = await resp.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { data = null; }
    if (!resp.ok || !data || data.ok !== true) {
      throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
    }
    return data;
  }

  /** 免登录通道现在能不能用？（配置在、开着、有地址） */
  function ingestAvailable() {
    return !!(state.ingest && state.ingest.enabled === true && state.ingest.url);
  }

  /** 把上次没交上去的补发掉（页面打开时调一次）。 */
  async function flushPending() {
    if (!ingestAvailable()) return;      // 通道没开 ⇒ 没有可补的（也别越攒越多）
    let sent = 0;
    let report = shiftPending();
    while (report) {
      try {
        await postIngest(report);
        sent++;
      } catch (e) {
        enqueueReport(report);
        break;
      }
      report = shiftPending();
    }
    if (sent) {
      $('#submitHint').textContent = '补发了 ' + sent + ' 条';
    }
  }
```

**(e) 改 `renderSubmit()`**：

```javascript
  /** 提交按钮：点一下直接交（交不上去就排队）；没有量时禁用。 */
  function renderSubmit() {
    const { days } = reportDays();
    const btn = $('#submitBtn');
    const has = Object.keys(days).length > 0;
    const old = $('#oldWayBtn');
    if (old) old.href = has ? buildIssueUrl(days) : '#';
    if (has && state.enabled) {
      btn.classList.remove('off');
      btn.removeAttribute('aria-disabled');
      $('#submitHint').textContent = '点「交上去」直接交（不用登录 GitHub）。';
    } else if (!state.enabled) {
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
      $('#submitHint').textContent = '备考源现在被停用了（注册表里 enabled 不是 true）—— ' +
        '交上去服务端也不会收这一类上报。';
    } else {
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
      $('#submitHint').textContent = '还没有记下任何词 —— 先在上面填个数。';
    }
    $('#issueTitle').textContent = has ? buildIssueTitle(days) : '[report] words …';
    ...（后面的 extraDaysNote 那段不动）
  }
```

**(f) 改 `bind()` 里提交按钮的点击处理**：

```javascript
    const btn = $('#submitBtn');
    btn.addEventListener('click', function (e) {
      if (btn.getAttribute('aria-disabled') === 'true') {
        e.preventDefault();
        return;
      }
      // 她点了「交上去」⇒ 预填的数就此变成今天的记录
      if (state.draft && !(Number(state.days[state.draft.day]) > 0)) {
        const n = Math.max(0, Math.floor(Number(state.draft.value) || 0));
        if (n > 0) state.days[state.draft.day] = n;
        state.days = trimDays(state.days);
        state.draft = null;
        saveDays();
        render();
      }
      const { days } = reportDays();
      const report = buildReport(days);
      // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
      //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点提交只会报错。
      if (!ingestAvailable()) {
        window.open(buildIssueUrl(days), '_blank', 'noopener');
        $('#submitHint').textContent = '已打开提交页 —— 在 GitHub 那个页面上点一下 Submit';
        return;
      }
      postIngest(report).then(function () {
        $('#submitHint').textContent = '已记录 ✓';
      }).catch(function (err) {
        enqueueReport(report);
        $('#submitHint').textContent = '暂时交不上去（' +
          (err && err.message ? err.message : '未知原因') + '）—— 已存着，下次打开自动补发。';
      });
    });
```

**(g) `boot()` 里，注册表到位后补发**（在 `loadRegistry().then(reg => {...})` 块的末尾）：

```javascript
      state.registryLoaded = true;
      flushPending();
    });
```

- [ ] **Step 5: 跑测试，确认通过**

Run: `python3 -B -m tests.test_worker_client`
Expected: 9 tests OK

- [ ] **Step 6: 跑整套测试**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK（661 + 5 + 7 + 9）

- [ ] **Step 7: 提交**

```bash
git add site/words/app.js site/words/index.html tests/test_worker_client.py
git commit -m "feat: 背单词页的提交改成直传免登录通道（老办法留作兜底）"
```

---

### Task 7: 客户端 · KOReader 落地页

**Files:**
- Modify: `site/koreader/app.js`
- Modify: `site/koreader/index.html`
- Modify: `tests/test_worker_client.py`

**Interfaces:**
- Consumes: Task 5/6 那套同名函数
- Produces: KOReader 落地页的 `postIngest` / 待发队列 / `flushPending`；`state.ingest`

- [ ] **Step 1: 写失败的契约测试**

在 `tests/test_worker_client.py` 里加：

```python
KO_DIR = ROOT / "site" / "koreader"
KO_JS = KO_DIR / "app.js"
KO_HTML = KO_DIR / "index.html"


class KoreaderIngestTest(unittest.TestCase):
    """KOReader 落地页：点「交上去」直接交。"""

    def test_the_page_posts_to_the_ingest_endpoint(self):
        js = KO_JS.read_text(encoding="utf-8")
        self.assertIn("postIngest", js, "要有 postIngest")
        self.assertIn("X-Dex-Token", js, "要带 ingest token 头")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        js = KO_JS.read_text(encoding="utf-8")
        self.assertTrue(re.search(r"d\.ingest|\['ingest'\]", js) is not None,
                        "要从 sources.json 顶层读 ingest 段")
        self.assertLessEqual(len(re.findall(r"fetch\(", js)), 2,
                             "KOReader 落地页的网络请求不该变多")

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        js = KO_JS.read_text(encoding="utf-8")
        self.assertIn("buildIssueUrl", js, "老办法的 URL 构造要留着")
        html = KO_HTML.read_text(encoding="utf-8")
        self.assertIn("oldWayBtn", html, "页面里要有老办法链接")

    def test_the_submit_button_is_a_button_not_a_link(self):
        html = KO_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'<button[^>]*id="submitBtn"')
        self.assertNotRegex(html, r'<a[^>]*id="submitBtn"')

    def test_the_page_queues_failed_reports(self):
        js = KO_JS.read_text(encoding="utf-8")
        self.assertIn("pendingIngest", js)
        self.assertIn("flushPending", js)
```

- [ ] **Step 2: 跑测试，确认它失败**

Run: `python3 -B -m tests.test_worker_client`
Expected: FAIL —— `AssertionError: 要有 postIngest`

- [ ] **Step 3: 改 `site/koreader/index.html`**

把

```html
    <a class="btn" id="submitBtn" href="#" target="_blank" rel="noopener noreferrer">去 GitHub 提交</a>
    <p class="hint center">点开是 GitHub 的新建 issue 页，标题和内容都填好了 —— 在那个页面上点一下 <b>Submit</b> 才算交上去。</p>
```

换成

```html
    <button class="btn" id="submitBtn" type="button">交上去</button>
    <p class="hint center" id="submitHint">点「交上去」直接交（不用登录 GitHub）。</p>
    <p class="hint center"><a id="oldWayBtn" href="#" target="_blank" rel="noopener noreferrer">用 GitHub 提交（老办法）</a></p>
```

同时把下面那块说明的标题与第一条改成老路的措辞：

```html
    <section class="card">
      <h2>交上去之后</h2>
      <ol class="steps">
        <li>老办法会把这条报告发到 <b>lilia-shound/pokemon-dex</b> 这个仓库的 issue，标题长这样：<code id="issueTitle">[report] koreader …</code>。</li>
        <li>服务端的定时任务读它 → 校验（源、日期、数值）→ 并进逐日账本（同源同一天取最大值，重复交不吃亏）。</li>
        <li>当天额度凑够了，就从还没收服的池子里掉一只宝可梦，进你的图鉴。</li>
```

- [ ] **Step 4: 改 `site/koreader/app.js`**

**(a) 顶部加常量**（放在 `const ISSUE_NEW = ...` 下面）：

```javascript
  /* ---------- 免登录投递通道 ---------- */
  /** 待发队列的 localStorage 键 —— 交不上去的先存这儿，下次打开页面自动补发。 */
  const PENDING_KEY = 'pendingIngest';
  /** 单次投递的超时（毫秒） */
  const INGEST_TIMEOUT_MS = 15000;
```

**(b) `state` 加一个字段**：

```javascript
  const state = {
    days: {}, reportDays: {}, clamped: [],
    per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY,
    credits: 0, issueTitle: '', issueUrl: '',
    registryLoaded: false,
    ingest: null,      // 免登录通道的配置（从 sources.json 顶层读）
  };
```

**(c) `loadRegistry()` 里读 ingest**：

```javascript
        state.ingest = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
```

`.catch` 里加 `state.ingest = null;`。

**(d) 新增投递函数**（放在 `buildIssueUrl()` 后面）—— 与背单词页那份**逐字相同**：

```javascript
  /** 读待发队列（坏数据当空队列）。 */
  function pendingQueue() {
    try {
      const raw = window.localStorage.getItem(PENDING_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  /** 写待发队列（只留最近 50 条）。 */
  function savePending(list) {
    try {
      window.localStorage.setItem(PENDING_KEY, JSON.stringify((list || []).slice(-50)));
    } catch (e) { /* 存不住就算了 */ }
  }
  /** 把一份报告排进待发队列。 */
  function enqueueReport(report) {
    const list = pendingQueue();
    list.push(report);
    savePending(list);
  }
  /** 取出队首（没有就 null）。 */
  function shiftPending() {
    const list = pendingQueue();
    if (!list.length) return null;
    const head = list.shift();
    savePending(list);
    return head;
  }

  /** 把一份报告 POST 到免登录通道。成功返回回包，失败**抛异常**。 */
  async function postIngest(report) {
    if (!ingestAvailable()) throw new Error('免登录通道没开');
    const ctrl = new AbortController();
    const timer = setTimeout(function () { ctrl.abort(); }, INGEST_TIMEOUT_MS);
    let resp = null;
    try {
      resp = await fetch(state.ingest.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dex-Token': state.ingest.token || '' },
        body: JSON.stringify(report),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    const text = await resp.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { data = null; }
    if (!resp.ok || !data || data.ok !== true) {
      throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
    }
    return data;
  }

  /** 免登录通道现在能不能用？（配置在、开着、有地址） */
  function ingestAvailable() {
    return !!(state.ingest && state.ingest.enabled === true && state.ingest.url);
  }

  /** 把上次没交上去的补发掉（页面打开时调一次）。 */
  async function flushPending() {
    if (!ingestAvailable()) return;      // 通道没开 ⇒ 没有可补的（也别越攒越多）
    let sent = 0;
    let report = shiftPending();
    while (report) {
      try {
        await postIngest(report);
        sent++;
      } catch (e) {
        enqueueReport(report);
        break;
      }
      report = shiftPending();
    }
    if (sent) {
      const hint = $('#submitHint');
      if (hint) hint.textContent = '补发了 ' + sent + ' 条';
    }
  }
```

**(e) `renderDays()` 里改提交按钮与老办法链接**（现在那里是 `$('#submitBtn').href = state.issueUrl;`）：

```javascript
    const submit = $('#submitBtn');
    if (submit) {
      submit.classList.toggle('off', !Object.keys(state.reportDays || {}).length);
      if (!Object.keys(state.reportDays || {}).length) submit.setAttribute('aria-disabled', 'true');
      else submit.removeAttribute('aria-disabled');
    }
    const old = $('#oldWayBtn');
    if (old) old.href = state.issueUrl || '#';
```

**(f) `boot()` 里绑定提交按钮 + 注册表到位后补发**：

```javascript
  function bindSubmit() {
    var btn = document.getElementById('submitBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (btn.getAttribute('aria-disabled') === 'true') return;
      var report = buildReport(state.reportDays);
      // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
      //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点提交只会报错。
      if (!ingestAvailable()) {
        window.open(state.issueUrl || '#', '_blank', 'noopener');
        var h0 = document.getElementById('submitHint');
        if (h0) h0.textContent = '已打开提交页 —— 在 GitHub 那个页面上点一下 Submit';
        return;
      }
      postIngest(report).then(function () {
        var hint = document.getElementById('submitHint');
        if (hint) hint.textContent = '已记录 ✓';
      }).catch(function (err) {
        enqueueReport(report);
        var hint = document.getElementById('submitHint');
        if (hint) hint.textContent = '暂时交不上去（' +
          (err && err.message ? err.message : '未知原因') + '）—— 已存着，下次打开自动补发。';
      });
    });
  }
```

在 `boot()` 里 `bindManualDay();` 后面加 `bindSubmit();`，
并在 `loadRegistry().then(reg => {...})` 块末尾（`state.registryLoaded = true;` 之后）加 `flushPending();`。

- [ ] **Step 5: 跑测试，确认通过**

Run: `python3 -B -m tests.test_worker_client`
Expected: 14 tests OK

- [ ] **Step 6: 跑整套测试**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK（661 + 5 + 7 + 14）

- [ ] **Step 7: 提交**

```bash
git add site/koreader/app.js site/koreader/index.html tests/test_worker_client.py
git commit -m "feat: KOReader 落地页的提交改成直传免登录通道（老办法留作兜底）"
```

---

### Task 8: 部署 Worker（**需要姜来的 Cloudflare API Token**）

**Files:**
- Create: `worker/wrangler.toml`
- Modify: `data/sources.json`（填上真的 url / token，`enabled` 改 true）

**Interfaces:**
- Consumes: Task 1/2 的 Worker 代码
- Produces: 一个公网地址 `https://pokemon-dex-ingest.<账号子域>.workers.dev`

> ⚠️ **这个任务卡在姜来身上**：需要她注册 Cloudflare 账号并把一个 API Token 发我。
> **在拿到 token 之前**：Task 1~7 全部可以做完（客户端靠 `sources.json` 里的
> `ingest.enabled=false` 照旧走 GitHub 老路，**线上行为不变**）⇒ 不阻塞。

- [ ] **Step 1: 拿到 token 后，装 wrangler**

```bash
export CLOUDFLARE_API_TOKEN='<姜来给的 token>'
cd /workspace/pokemon-reading-dex/worker
# npm registry 在这个沙箱很慢 —— 给足时间，必要时换 npx
npm i -g wrangler@3 --prefer-offline || npx --yes wrangler@3 --version
npx wrangler --version
```

Expected: 打出 wrangler 版本号

- [ ] **Step 2: 建 KV 命名空间**

```bash
cd /workspace/pokemon-reading-dex/worker
npx wrangler kv namespace create INGEST_KV
```

Expected: 打印一段 `[[kv_namespaces]]` 配置，里面有 `id = "..."` ⇒ 抄下来。

- [ ] **Step 3: 写 `worker/wrangler.toml`**

```toml
name = "pokemon-dex-ingest"
main = "index.js"
compatibility_date = "2026-11-25"

# KV 命名空间（Task 8 Step 2 拿到的 id）
[[kv_namespaces]]
binding = "INGEST_KV"
id = "<上一步拿到的 id>"
```

- [ ] **Step 4: 生成并写入两个 secret**

```bash
cd /workspace/pokemon-reading-dex/worker
INGEST_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
ADMIN_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
printf '%s' "$INGEST_TOKEN" | npx wrangler secret put INGEST_TOKEN
printf '%s' "$ADMIN_TOKEN" | npx wrangler secret put ADMIN_TOKEN
npx wrangler secret list
```

Expected: `secret list` 里能看到 `INGEST_TOKEN` 与 `ADMIN_TOKEN`（**看不到值，只看名字** ✓）

⚠️ 若 `wrangler secret put` 不吃管道（它默认要交互），改用：
`npx wrangler secret put INGEST_TOKEN --text "$INGEST_TOKEN"`（较新的 wrangler 支持 `--text`）。

- [ ] **Step 5: 部署**

```bash
cd /workspace/pokemon-reading-dex/worker
npx wrangler deploy
```

Expected: 打印 `https://pokemon-dex-ingest.<子域>.workers.dev` ⇒ **把它记下来**。

- [ ] **Step 6: 线上冒烟**

```bash
URL='https://pokemon-dex-ingest.<子域>.workers.dev'
# ① 健康检查（不要凭证）
curl -sS -m 20 "$URL/health"; echo
# ② 没凭证的 ingest ⇒ 401
curl -sS -m 20 -o /dev/null -w "http=%{http_code}\n" -X POST "$URL/ingest" \
  -H 'Content-Type: application/json' -d '{"v":1,"source":"pom","days":{"2026-11-21":37}}'
# ③ 没凭证的 pull ⇒ 401
curl -sS -m 20 -o /dev/null -w "http=%{http_code}\n" "$URL/pull"
# ④ 预检
curl -sS -m 20 -o /dev/null -w "http=%{http_code}\n" -X OPTIONS "$URL/ingest" \
  -H 'Origin: https://lilia-shound.github.io' -H 'Access-Control-Request-Method: POST'
```

Expected: ① `{"ok":true,"pending":0,...}` ② `http=401` ③ `http=401` ④ `http=204`

- [ ] **Step 7: 把真值填进注册表**

编辑 `data/sources.json` 的 `ingest` 段：

```json
  "ingest": {
    "url": "https://pokemon-dex-ingest.<子域>.workers.dev/ingest",
    "token": "<上面生成的 INGEST_TOKEN>",
    "enabled": true
  },
```

⚠️ **`url` 要写完整的 `/ingest` 路径**（客户端是直接 POST 过去，不拼路径）。
⚠️ **admin token 不许出现在这个文件里** —— 它只进 Mavis secret。

- [ ] **Step 8: 端到端真打一次（用真凭证）**

```bash
URL='https://pokemon-dex-ingest.<子域>.workers.dev'
curl -sS -m 20 -X POST "$URL/ingest" \
  -H 'Content-Type: application/json' -H "X-Dex-Token: <INGEST_TOKEN>" \
  -d '{"v":1,"source":"pom","days":{"2026-11-21":37}}'; echo
curl -sS -m 20 "$URL/health"; echo
```

Expected: 第一条 `{"ok":true,"id":"q:..."}`，第二条 `"pending":1`

⚠️ 这一条会**真的进账**（37 分钟番茄钟 ⇒ 11-21 那天的番茄钟量）⇒
**测完记得让服务端 ack 掉它**（或者接受它 —— 11-21 她本来就有 25 分钟，取 max 后是 37，
会多算 12 分钟 = 0 个额度，不影响结果）。更稳的做法：测的时候用一个**过去的、没意义的量**
（比如 `{"v":1,"source":"pom","days":{"2026-11-02":1}}`），验完在 Worker 侧把那条删掉。

- [ ] **Step 9: 登记 Mavis secret**

```bash
# 用 mcode-tools / secret 工具登记（不要把 token 写进仓库）
# WORKER_ADMIN_TOKEN = <ADMIN_TOKEN>
# WORKER_INGEST_TOKEN = <INGEST_TOKEN>
# WORKER_URL = https://pokemon-dex-ingest.<子域>.workers.dev
```

- [ ] **Step 10: 提交**

```bash
git add worker/wrangler.toml data/sources.json
git commit -m "feat: 部署免登录接收端并把通道配置填进注册表"
```

---

### Task 9: cron 提示词与端到端

**Files:**
- Modify: cron `463191742394721` 的提示词（用 `cron_update`；**不在仓库里**）
- （可选）`README.md` 的一段

**Interfaces:**
- Consumes: Task 4 的 `scripts/collect_worker.py`、Task 8 的 secret
- Produces: cron 每 3 小时多跑一步「收 Worker 队列」

- [ ] **Step 1: 改 cron 提示词**

用 `cron_update`（task_id `463191742394721`）把提示词改成下面这样 —— **在第 1 步之后插入 1.5 步，并把第 4 步的部署条件补上 1.5**：

**第 1 步后插入：**

```
1.5 `cd /workspace/pokemon-reading-dex && python3 scripts/collect_worker.py` —— 收**免登录投递通道**的队列（S8 上线于 2026-11-25）
   - 这一步让三个 push 源（番茄钟 / 背单词 / KOReader）**不用登录 GitHub 也能交**：
     页面点一下 → 报告进 Worker 的队列 → 这一步把它并进逐日账本。
   - 输出含「[投递] 采纳」= 有新报告并进账本；输出含「[投递] 队列是空的」= 没有（正常）。
   - 输出含「[投递] 未配置」= 还没配 WORKER_URL / WORKER_ADMIN_TOKEN ⇒ **如实说一句「免登录通道没开」**。
   - 输出含「[投递] 拉取失败」= Worker 不通 ⇒ **不影响发奖**（这是旁路），如实说一句即可。
   - 输出含「[投递] 拒收」= 有人提交了非法报告，**不要自己动手修**，如实提一句就行。
```

**第 4 步的部署条件补一条**（在「第 1 步采纳了报告…」那条后面）：

```
   - **或者** 第 1.5 步采纳了报告（输出含「[投递] 采纳」或「账本已更新」）——
     同样是**不能漏**：账本里的量没推上去，这轮番茄钟/KOReader/背单词就白干了。
```

**并在「★ 当前口径」那一节补一条：**

```
- ✅ **免登录投递通道（S8 上线了）**：三个 push 源现在**点一下就交上去**（不用登录 GitHub）。
  ★ 但 **GitHub issue 老路还在** —— 已装好的旧页面走的是老路，照样收。
  ★ 客户端交不上去（网络不通 / Worker 抽风）会**先存到本机**，下次打开页面自动补发。
  ★ `collect_worker.py` 是**旁路**：拉取失败只打印警告，**不影响发奖**。
```

- [ ] **Step 2: 在沙箱里手动跑一遍整条链，确认能走通**

```bash
cd /workspace/pokemon-reading-dex
export WORKER_URL='https://pokemon-dex-ingest.<子域>.workers.dev'
export WORKER_ADMIN_TOKEN='<ADMIN_TOKEN>'
python3 scripts/collect_worker.py
```

Expected: 打印「[投递] 队列是空的」或「[投递] 采纳 …」；**exit 0**

- [ ] **Step 3: 造一条报告走完整链路**

```bash
cd /workspace/pokemon-reading-dex
export WORKER_URL='https://pokemon-dex-ingest.<子域>.workers.dev'
export WORKER_ADMIN_TOKEN='<ADMIN_TOKEN>'
curl -sS -m 20 -X POST "$WORKER_URL/ingest" \
  -H 'Content-Type: application/json' -H "X-Dex-Token: <INGEST_TOKEN>" \
  -d '{"v":1,"source":"pom","days":{"2026-11-02":1}}'
python3 scripts/collect_worker.py
python3 scripts/check_today.py
```

Expected: `collect_worker.py` 打印「[投递] 采纳 q:… pom {2026-11-02: 1}」且账本多了那天 ⇒ 队列清空（ack 成功）

- [ ] **Step 4: 把测试数据抹掉（别留在她的账本里）**

```bash
cd /workspace/pokemon-reading-dex
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('data/dex.json')
state = json.loads(p.read_text(encoding='utf-8'))
led = state.get('ledger') or {}
row = led.get('2026-11-02') or {}
if row.get('pom') == 1:
    row.pop('pom')
    if row:
        led['2026-11-02'] = row
    else:
        led.pop('2026-11-02')
    state['ledger'] = led
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已抹掉 2026-11-02 的测试数据')
else:
    print('账本里没有那条测试数据，不用抹')
PY
```

- [ ] **Step 5: 部署一次，把新的 sources.json 推上去**

```bash
cd /workspace/pokemon-reading-dex
bash scripts/deploy.sh
```

Expected: `site/sources.json` 里有 `ingest` 段 ⇒ 线上页面能读到

- [ ] **Step 6: 线上抽查**

```bash
curl -sS -m 20 'https://lilia-shound.github.io/pokemon-dex/sources.json' | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ingest'))"
curl -sS -m 20 -o /dev/null -w "app.js http=%{http_code}\n" 'https://lilia-shound.github.io/pokemon-dex/app.js?v=10'
```

Expected: `{'url': 'https://pokemon-dex-ingest..../ingest', 'token': '...', 'enabled': True}`；app.js 200

⚠️ **GitHub Pages 的 CDN 会缓存 `sources.json`** ⇒ 线上没立刻变的话，**等几分钟或加 `?cb=$RANDOM` 再试**。

- [ ] **Step 7: 更新 README**

在 `README.md` 里加一节「免登录投递通道」：

```markdown
## 免登录投递通道（S8）

三个 push 源（番茄钟 / 背单词 / KOReader）的报告先落到 Cloudflare Worker 的队列，
cron 每 3 小时拉回来并进逐日账本。

- 客户端：从 `sources.json` 顶层 `ingest` 段读 URL 与 token ⇒ **零新增网络请求**
- 服务端：`python3 scripts/collect_worker.py`（**旁路**，拉不到就跳过，不影响发奖）
- 回滚：把 `data/sources.json` 的 `ingest.enabled` 改成 `false` ⇒ 客户端立刻退回 GitHub 老路
- **GitHub issue 老路永远保留** —— 已装好的旧页面照常用
```

- [ ] **Step 8: 提交**

```bash
git add README.md
git commit -m "docs: README 写上免登录投递通道与回滚方式"
```

---

### Task 10: 收尾（观感断言 + 回归）

**Files:**
- Modify: `tests/test_worker_client.py`（加浏览器观感断言）
- （若发现 bug）Modify: `site/pomodoro/app.js` / `site/words/app.js` / `site/koreader/app.js`

**Interfaces:**
- Consumes: Task 5/6/7 的客户端实现
- Produces: 三个页面的浏览器观感断言（点同步 → 原地显示「已记录」；断网 → 提示 + 排队）

- [ ] **Step 1: 写浏览器观感断言**

在 `tests/test_worker_client.py` 里加（**这个文件要自带全部辅助代码** ——
别从别的测试文件 import，工程师可能是单独执行这个任务的）：

```python
# ---- 文件顶部的 import 段要补这些（其余照旧）----
import http.server
import json
import pathlib
import re
import shutil
import socketserver
import threading
import unittest

from tests import browser_path

CHROME = browser_path.chromium_path()   # 别写死版本号 —— 沙箱刷新会换 Playwright 版本

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:  # noqa: BLE001
    HAVE_PLAYWRIGHT = False


def _make_server(directory: pathlib.Path):
    """在 site/ 目录上起一个静态服务器（端口 0 = 随机端口，免得跟别的测试撞车）。"""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):  # 别把日志糊满终端
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class IngestBrowserTest(unittest.TestCase):
    """真实浏览器里点一次同步 ⇒ 原地显示「已记录」；断网 ⇒ 提示 + 排队。

    浏览器自己起一个本地静态服务器（site/ 目录）—— 不依赖外网，也不占固定端口。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过投递通道的浏览器测试")
        if not (shutil.which("chromium") or pathlib.Path(CHROME).exists()):
            raise unittest.SkipTest("找不到 chromium，跳过投递通道的浏览器测试")
        cls.httpd = _make_server(ROOT / "site")
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def _registry(self):
        """注册表：带一个开着的 ingest 段。"""
        return {
            "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3, "ledgerDays": 30,
            "ingest": {"url": "https://ingest.test/ingest", "token": "tok", "enabled": True},
            "sources": {
                "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                        "mode": "push", "enabled": True, "maxPerDay": 1440},
            },
        }

    def test_clicking_sync_posts_the_report_and_says_it_is_recorded(self):
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        posted = []
        page.route("**/sources.json", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(self._registry())))
        page.route("**/ingest", lambda route: (
            posted.append(route.request.post_data),
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "id": "q:1"}))))

        page.goto(self.origin + "/pomodoro/index.html", wait_until="commit")
        page.wait_for_function(
            "() => typeof INGEST === 'object' && INGEST && INGEST.url", timeout=30000)
        today = page.evaluate("() => todayStr()")
        # ⚠️ 改完数据**必须调 renderSync()** —— 按钮的 disabled 状态是它设的，
        #    不重画的话按钮还是禁用的，page.click 会一直等到超时。
        page.evaluate("() => { S().days[todayStr()] = {minutes: 25, poms: 1}; "
                      "Store.write(); renderSync(); }")
        page.click("#btnSync")
        page.wait_for_timeout(800)

        self.assertEqual(len(posted), 1, "应该发出去了一个请求")
        body = json.loads(posted[0])
        self.assertEqual(body.get("v"), 1)
        self.assertEqual(body.get("source"), "pom")
        self.assertIn(today, body.get("days", {}))
        self.assertEqual(body["days"][today], 25)
        page.close()

    def test_a_failing_post_is_queued_and_the_old_way_is_offered(self):
        """交不上去 ⇒ 提示 + 排队（下次打开自动补发）。"""
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        page.route("**/sources.json", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(self._registry())))
        page.route("**/ingest", lambda route: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"ok": False, "error": "队列满了"})))

        page.goto(self.origin + "/pomodoro/index.html", wait_until="commit")
        page.wait_for_function(
            "() => typeof INGEST === 'object' && INGEST && INGEST.url", timeout=30000)
        page.evaluate("() => { S().days[todayStr()] = {minutes: 25, poms: 1}; "
                      "Store.write(); renderSync(); }")
        page.click("#btnSync")
        page.wait_for_timeout(800)

        queued = page.evaluate("() => JSON.parse(localStorage.getItem('pendingIngest') || '[]')")
        self.assertEqual(len(queued), 1, "交不上去的报告要进待发队列")
        self.assertEqual(queued[0].get("source"), "pom")
        page.close()
```

- [ ] **Step 2: 跑这个测试，确认它通过（或暴露真问题）**

Run: `python3 -B -m tests.test_worker_client`
Expected: 通过。若失败 ⇒ **是实现有 bug，照报错修**，别改测试迁就实现。

- [ ] **Step 3: 跑整套测试**

Run: `python3 -B run_tests.py 2>&1 | tail -5`
Expected: 全部 OK

- [ ] **Step 4: 线上三个页面各点一次（真机闭环）**

在手机或浏览器上：
1. 番茄钟页 → 造 25 分钟 → 点「同步到图鉴」⇒ 原地显示「已记录 1 天」
2. 背单词页 → 填 150 → 点「交上去」⇒ 提示「已记录」
3. KOReader 落地页（`?days=2026-11-21:25`）→ 点「交上去」⇒ 提示「已记录」

然后看 `https://lilia-shound.github.io/pokemon-dex/` 的「今日额度」面板 ——
**番茄钟那一行应该多出额度**。

- [ ] **Step 5: 提交**

```bash
git add tests/test_worker_client.py
git commit -m "test: 三个页面的投递通道浏览器观感断言"
```

---

## 验收清单（全部做完才算 S8 落地）

- [ ] `python3 -B run_tests.py` 全绿（基线 661 + 新增用例）
- [ ] `node tests/worker_validate_spec.js` 通过
- [ ] `node tests/worker_handler_spec.mjs` 通过
- [ ] Worker 已部署，`/health` 返回 `pending`
- [ ] 番茄钟页点同步 → 原地显示「已记录」
- [ ] 背单词页点提交 → 原地显示「已记录」
- [ ] KOReader 落地页点提交 → 原地显示「已记录」
- [ ] 断网点同步 → 提示连不上 + 老办法入口出现 → 恢复网络后重开页面自动补发
- [ ] cron 里出现「[投递] 采纳」，账本进账，图鉴页「今日额度」多出来
- [ ] `data/sources.json` 的 `ingest.enabled` 改 `false` ⇒ 三个页面退回 GitHub 老路（**回滚演练**）
- [ ] GitHub issue 老路仍能收报告（老页面没坏）

## 不做什么（YAGNI）

- **不做 Turnstile / 人机验证** —— 要引外部脚本，破坏「子页只许一个 script」的纪律
- **不做账号体系 / OAuth / IP 白名单 / Origin 白名单** —— 姜来已明确不加
- **不改 KOReader 插件（Kindle 端直连）** —— HTTPS 证书、离线重试、Kindle 的网络栈都麻烦；落地页改了插件就自动受益
- **不改百词斩油猴脚本** —— 提交那一下本来就在落地页
- **不删 GitHub issue 老路**
- **不改结算核心**（`dex_core.py` / `award.py` / `ledger.py` 的口径）
- **不做每日额度微调**（背单词记「新词」还是「新词+复习」另议）
