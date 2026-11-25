# 训练家图鉴 S8 · 免登录投递接收端（Cloudflare Worker）设计文档

> 日期：2026-11-25
> 上游：`docs/superpowers/specs/2026-11-18-pokemon-dex-hub-design.md`（中枢设计）、S1~S7 已上线
> 状态：设计已由姜来拍板（2026-11-25）

## 1. 目标

S1~S7 把「四个源汇进同一个图鉴」和「日常闭环」都做完了，但**push 源的投递门槛还卡着**。

**现状（2026-11-25 实测）**：

| 源 | 模式 | 实际使用情况 |
|---|---|---|
| 微信读书 | pull | 主力，几乎每天 |
| 番茄钟 | push | 只提交过 3 次（11-21 / 11-22 / 11-23 各 25 分钟），之后停了 |
| 背单词 | push | **0 次** |
| KOReader | push | **0 次** |

**根因不是她不想用，是门槛**：三个 push 源的投递路径都是
「打开页面 → 点同步 → **打开 GitHub 新建 issue 页 → 点 Submit**」——
要登录 GitHub、手机上可能还要过 2FA、要点两次。

S8 把这条路径换成「**点一下，原地显示已记录**」。
**核心结算一行不改**，GitHub issue 老路保留当兜底。

## 2. 已确认的产品决策（姜来拍板）

| 项 | 决定 | 拍板时问的原话 |
|---|---|---|
| 接收端形态 | **Cloudflare Worker（免登录公网端点）** | 三个选项里选了 A |
| 防刷强度 | **不加 Origin 白名单**，靠限幅 + 服务端结算规则兜底 | 「就这样，按这个走」 |
| 客户端范围 | **只改三个页面**；KOReader 插件与百词斩油猴脚本不动 | 「就这样，按这个走」 |
| 老路 | **GitHub issue 通道保留**，页面里留「老办法」入口 | 设计里写明，她认可 |

**为什么选 Worker 而不是别的**（备选与取舍）：

| 方案 | 取舍 |
|---|---|
| **Cloudflare Worker（选中）** | 我能在沙箱里全程部署（已验证 `api.cloudflare.com` 可达）；一键体验；以后能往里塞更细的量 |
| GitHub fine-grained PAT 粘一次 | 服务端零改动、但 token 散在设备上；已验证 `api.github.com` 允许浏览器跨域（`access-control-allow-origin: *`），技术上可行 |
| 邮件通道 | 不开账号也不部署，但仓库要开「通过邮件建 issue」（该功能是否还在**未验证**），且每次仍要跳邮件 App 点发送 ⇒ 仍是两次点击 |

**Worker 的已知风险**：默认域名 `*.workers.dev` 在国内的连通性不稳（可能慢、偶发超时）。
对策：① 客户端本地待发队列 + 自动重发 ② 页面保留 GitHub 老办法入口 ③ 真测不通再考虑绑自有域名。

## 3. 架构与数据流

```
番茄钟页 / 背单词页 / KOReader 手机落地页
   │  POST {v:1, source:"pom", days:{...}}   Header: X-Dex-Token
   ▼
Cloudflare Worker（免登录）
   │  校验：source 白名单 · days≤30 · 日期不晚于今天 · 单日上限 · 队列≤200 条
   ▼
Workers KV（key = q:<时间戳>-<随机>，TTL 14 天）
   │
cron 每 3 小时（Mavis sandbox）
   │  GET /pull    Header: X-Dex-Admin
   ▼
scripts/collect_worker.py
   │  复用 scripts/collect_reports.py 的 parse_report 校验 + ledger.merge_amounts 合并
   ▼
scripts/award.py（结算核心，一行不改）
   │
   ▼
POST /ack {ids:[...]} → Worker 删掉已并账的条目
```

### 3.1 三条铁律

1. **结算核心零改动** —— 新通道只负责「把远端数据变成账本里的量」，
   走的是和 Issue 通道**完全同一套**校验（`collect_reports.parse_report`）与合并函数（`ledger.merge_amounts`）。
2. **GitHub issue 老路保留** —— 已装好的旧页面照常能用；新页面里也留「老办法」入口。
3. **抽卡权还在服务端** —— 客户端只报「我投入了多少」，不参与任何判定。

### 3.2 报告体：两个通道共用同一份

老路把 `buildReport()` 的 JSON 塞进 issue body；新路把**同一个 JSON** POST 上来。
⇒ 客户端的报告构造逻辑一行不改，两条通道的语义天然一致（同源同日取 max ⇒ 幂等）。

```json
{"v":1,"source":"pom","days":{"2026-11-21":37}}
```

## 4. 接口契约

### 4.1 `POST /ingest` —— 客户端用

```
POST https://<worker>.workers.dev/ingest
Content-Type: application/json
X-Dex-Token: <INGEST_TOKEN>

{"v":1,"source":"pom","days":{"2026-11-21":37}}
```

成功 `200`：

```json
{"ok":true,"id":"q:1732500000000-a1b2","receivedAt":"2026-11-25T06:30:00Z"}
```

失败 `4xx`（客户端据此提示「这次没交上去」）：

```json
{"ok":false,"error":"单日量 9999 超过上限 1440"}
```

### 4.2 `GET /pull` —— cron 用

```
GET https://<worker>.workers.dev/pull
X-Dex-Admin: <ADMIN_TOKEN>
```

```json
{"items":[
  {"id":"q:1732500000000-a1b2","body":{"v":1,"source":"pom","days":{"2026-11-21":37}},
   "receivedAt":"2026-11-25T06:30:00Z"}
]}
```

### 4.3 `POST /ack` —— cron 用

```
POST https://<worker>.workers.dev/ack
X-Dex-Admin: <ADMIN_TOKEN>
Content-Type: application/json

{"ids":["q:1732500000000-a1b2"]}
```

`200` `{"ok":true,"removed":1}`。**只 ack 已经成功并进账本的条目。**

### 4.4 `GET /health` —— 排查用（不要鉴权）

```json
{"ok":true,"pending":3,"source":"pokemon-dex-ingest"}
```

### 4.5 CORS（必须处理，否则浏览器直接拦）

`Content-Type: application/json` 的 POST 会触发**预检** `OPTIONS`。
Worker 必须：

- 对 `OPTIONS` 返回 `204`，带
  `Access-Control-Allow-Origin: *`、`Access-Control-Allow-Methods: POST,GET,OPTIONS`、
  `Access-Control-Allow-Headers: Content-Type,X-Dex-Token,X-Dex-Admin`、`Access-Control-Max-Age: 86400`
- 对实际响应也带 `Access-Control-Allow-Origin: *`

用 `*` 而不是固定她的域名：ingest token 本来就是公开的（见 §6），限定域名只会在她换域名时炸一次。

## 5. 鉴权与防刷（说实话版）

| 凭证 | 存哪 | 能干什么 |
|---|---|---|
| `INGEST_TOKEN` | **公开**（`data/sources.json` 顶层 `ingest` 段 ⇒ 随 `site/sources.json` 公开） | 提交报告 |
| `ADMIN_TOKEN` | **只进 Mavis secret**（不进 git） | 拉取队列、ack 删除 |
| `CLOUDFLARE_API_TOKEN` | **只进 Mavis secret**（不进 git） | 部署 Worker、创建 KV |

**ingest token 在公开的 JS 里 ⇒ 这条通道天生「任何人可写」。**
防刷靠**限幅**，不靠保密：

**Worker 侧硬校验**（`worker/lib/validate.cjs`）：

1. `v` 必须 == 1
2. `source` 必须在 `{pom, koreader, words}` 里
3. `days` 必须是对象、条数 ≤ 30
4. 每个 key 是合法 `YYYY-MM-DD` 且**不晚于今天**（**今天按 UTC+8 算**，与账本口径一致，见 §9）
5. 每个 value 是非负整数，且 ≤ 该源的单日上限
6. 队列长度 ≤ 200 条（超出直接拒收）

**服务端侧的真正底线**（`collect_reports.parse_report` + `ledger.merge_amounts` + `award.py`）：
同源同日取 max、`maxPerDay` 夹上限、`perAward=2` ⇒ **一天最多掉 1 只宝可梦**。
被人刷，最多让她的图鉴走得快一点，不会多掉宝可梦、不会把账本搞乱。

**单日上限表**放在 `worker/lib/rules.cjs`（源 id → maxPerDay），
**必须与 `data/sources.json` 的 `sources.<id>.maxPerDay` 一致** ⇒ 有测试盯着（照 KOReader 插件那个常量的老套路）。

## 6. 配置放哪：`data/sources.json` 顶层 `ingest` 段

三个子页（番茄钟 / 背单词 / KOReader 落地页）**本来就在 fetch `sources.json`**。
把 ingest 配置塞进注册表顶层 ⇒ **零新增网络请求** ⇒ 不破「子页只许发一个网络请求」的纪律
⇒ 换 Worker URL / 换 token 只改一个文件。

```json
{
  "version": 1,
  "perAward": 2,
  ...
  "ingest": {
    "url": "https://pokemon-dex-ingest.<sub>.workers.dev",
    "token": "<INGEST_TOKEN>",
    "enabled": true
  }
}
```

- `scripts/sources.py:validate_config` **不拦未知的顶层键** ⇒ 加 `ingest` 段安全
  （本次会补一条对 `ingest` 段的显式校验：enabled 必须是布尔、url/token 必须是字符串）。
- `scripts/deploy.sh` 第 25 行 `cp data/sources.json site/sources.json` ⇒ 照旧同步，不用改部署脚本。
- **回滚**：`ingest.enabled` 改 `false` ⇒ 客户端立刻退回 GitHub 老办法（一行回滚）。
- `ADMIN_TOKEN` **绝不进 repo**。

## 7. 服务端改动

### 7.1 新脚本 `scripts/collect_worker.py`

薄网络层 + 复用现有逻辑，**不碰结算核心**。

```
python3 scripts/collect_worker.py            # 拉 → 校验 → 并账本 → ack
python3 scripts/collect_worker.py --dry-run  # 只打印，不写盘不 ack
```

流程：

1. 读 `WORKER_URL` / `WORKER_ADMIN_TOKEN`；**没配置就打印跳过并 exit 0**（跟 `push_notify.py` 的行为一致）
2. `GET /pull`；**拉不到就打印警告并 exit 0** ⇒ 投递通道故障**绝不阻塞结算**
3. 对每条 item：`body = json.dumps(item["body"])` ⇒ 交给 `collect_reports.parse_report(body, cfg, today)`
   - 校验不过 ⇒ **打印警告并 ack 掉这条**（Worker 已经限过幅，这里失败说明两边规则漂移，
     要修的是 bug 而不是留着数据反复拉）⇒ 有测试覆盖
   - 校验通过 ⇒ `ledger.merge_amounts(new_ledger, source, days)` ⇒ 并进账本
4. 账本有变 ⇒ 写回 `data/dex.json`（与 `collect_reports.py` 同款写法）
5. `POST /ack` 把**已并账 + 校验失败**的条目 id 一起 ack 掉

**幂等**：`merge_amounts` 同源同日取 max ⇒ ack 失败导致下轮重拉 ⇒ 不会多算。

### 7.2 cron 步骤顺序（cron 提示词是口径的权威副本）

```
1. scripts/collect_reports.py    收 Issue 投递箱（老路，保留）
1.5 scripts/collect_worker.py    收 Worker 队列（新路）
2. scripts/check_today.py
3. scripts/award.py              判定 + 抽卡（结算核心）
3.5 scripts/notify.py            发邮件（旁路）
3.6 scripts/history.py           回填历史（旁路）
4. scripts/deploy.sh             同步 site/ + 推 main + 重建 gh-pages
```

### 7.3 Mavis secret

| 名字 | 用途 |
|---|---|
| `WORKER_URL` | Worker 公开地址 |
| `WORKER_ADMIN_TOKEN` | 拉取 + ack（**只在这里**） |
| `WORKER_INGEST_TOKEN` | 与 `data/sources.json` 里的同一份（客户端要用） |
| `CLOUDFLARE_API_TOKEN` | 部署 Worker + 建 KV（**只在这里**） |

## 8. 客户端改动

### 8.1 范围

| 位置 | 改不改 | 怎么改 |
|---|---|---|
| `site/pomodoro/app.js` | **改** | 同步按钮改为 POST 到 Worker |
| `site/words/app.js` | **改** | 同上 |
| `site/koreader/app.js` | **改** | 同上 |
| `plugin/koreader-dex.koplugin/*` | **不动** | 它只把天数编码进短链（`?days=`），提交那一下本来就在落地页 |
| `plugin/bcz-dex.user.js` | **不动** | 它只把词数带进落地页（`?words=N`），提交那一下本来也在落地页 |

### 8.2 同步按钮的新行为

1. 点「同步」⇒ `fetch(ingest.url, {method:'POST', headers:{'X-Dex-Token':…,'Content-Type':'application/json'}, body: JSON.stringify(buildReport())})`，超时 15s
2. 成功 ⇒ 原地 toast「**已记录 3 天 · 47 分钟**」⇒ 写 `lastSyncAt` / `syncedDays`（沿用现在的字段）
3. 失败 ⇒ 存进 localStorage 待发队列 ⇒ **下次打开页面自动重发**
4. 连续失败 3 次 ⇒ 提示「暂时连不上」并显示「**用 GitHub 提交（老办法）**」入口
5. 老办法入口永久保留（issue URL 构造逻辑一行不删）

**待发队列**的 localStorage 键：`pendingIngest`（数组，最老在前，上限 50 条）。

### 8.3 三个页面的重复代码

三个子页各有一份 `app.js`，**只能各写一份**（子页只许引一个 `<script>`，多引一个就破测试）。
⇒ 用测试断言三处行为一致（都调 `postIngest`、都读 `ingest.url`、都保留老办法入口）。

## 9. 错误处理

| 场景 | 行为 |
|---|---|
| 客户端网络失败 / Worker 不通 | 进待发队列 → 下次打开页面自动重发 |
| 客户端连续失败 3 次 | 提示 + 显示老办法入口 |
| Worker 收到非法 body | `400` + `{"ok":false,"error":"…"}`，客户端 toast 出错 |
| Worker 队列已满（>200） | `429`，客户端进待发队列 |
| Worker KV 写失败 | `503`，同上 |
| `GET /pull` 失败（cron 侧） | 打印 `[投递] 拉取失败：…，跳过` ⇒ **exit 0** ⇒ award 照跑 |
| `POST /ack` 失败 | 条目留在 KV ⇒ 下轮重拉 ⇒ 并账取 max ⇒ 幂等不多算 |
| item 校验不过（cron 侧） | 打印警告 ⇒ **ack 掉**（避免反复拉取噪音） |
| `WORKER_URL` / `WORKER_ADMIN_TOKEN` 没配 | 打印跳过 ⇒ exit 0 |

**时区：Worker 判定「今天」用 UTC+8，不是 UTC。**
账本、图鉴页、`scripts/sources.py` 全部按北京时间（Asia/Shanghai）分天，
Worker 若用 UTC 就会在每天 0:00~8:00（北京时间）判错一天 ⇒ **把 8 小时偏移写死**：
`new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10)`。
⇒ 两端口径逐字一致，客户端与 Worker 交错投递也不会被拒。有测试盯着这条。

## 10. 测试策略

| 测试 | 文件 | 守住什么 |
|---|---|---|
| Worker 校验逻辑（node） | `tests/worker_validate_spec.js` | `worker/lib/validate.cjs` 的每条规则 |
| Worker 入口与 node 单测**共用**同一份校验代码 | `worker/lib/validate.cjs`（CommonJS） | 不是两份实现 |
| Python 侧收集器 | `tests/test_worker_collect.py` | 复用 `parse_report`、合并取 max、ack 幂等、**拉不到就跳过不阻塞** |
| 跨语言对拍 | `tests/test_worker_differential.py` | 同一批用例跑 Worker 的 JS 与服务端的 Python，**两边结论一致** |
| 限幅表不漂移 | `tests/test_worker_rules.py` | `worker/lib/rules.cjs` vs `data/sources.json` 的 `maxPerDay` |
| 时区口径 | `tests/test_worker_rules.py` | Worker 的「今天」= UTC+8，与服务端 `parse_report` 的北京时间口径逐字一致 |
| 客户端契约 | `tests/test_worker_client.py` | 三个页面生成的 body 形状 == `parse_report` 接受的形状；都调 `postIngest`；老办法入口还在 |
| 回归 | 全部 | **现有 661 个测试仍全绿**（结算核心没动） |
| 观感断言 | 真机闭环 | 她在手机上点同步 → 页面显示「已记录」→ 从 Worker 拉到 → 账本进账 → 图鉴页多一只 |

**跨语言对拍的做法**（照已有的 3731 组对拍套路）：node 跑一遍 `worker/lib/validate.cjs`，
把每条用例的判定输出成 JSON；Python 读同一份 JSON 断言一致。

## 11. 交付拆成四条线

| 线 | 内容 | 依赖 |
|---|---|---|
| A | `worker/` 目录（`index.js`、`lib/validate.cjs`、`lib/rules.cjs`、`wrangler.toml`）+ 部署 | 姜来给 `CLOUDFLARE_API_TOKEN` |
| B | `scripts/collect_worker.py` + `data/sources.json` 的 `ingest` 段 + Mavis secret + cron 提示词 | A |
| C | 三个页面的 `postIngest` + 待发队列 + 老办法入口 | B（要知道 URL） |
| D | 测试（node 单测、Python 单测、跨语言对拍、限幅表、契约） | 跟着 A/B/C 一起 |

**回滚**：任一条线出问题 ⇒ `data/sources.json` 的 `ingest.enabled` 改 `false` ⇒ 客户端退回老办法，
服务端 `collect_worker.py` 直接跳过。**不需要回滚部署、不需要改代码。**

## 12. 不做什么（YAGNI）

- **不做 Turnstile / 人机验证** —— 要引外部脚本，破坏「子页只许一个 script」的纪律
- **不做账号体系、OAuth、IP 白名单、Origin 白名单** —— 姜来已明确不加
- **不改 KOReader 插件（Kindle 端直连）** —— 要处理 HTTPS 证书、离线重试、Kindle 的网络栈；落地页改了插件就自动受益
- **不改百词斩油猴脚本** —— 提交那一下本来就在落地页
- **不删 GitHub issue 老路** —— 已装好的旧页面还在用
- **不改结算核心**（`dex_core.py` / `award.py` / `ledger.py` / `sources.py` 的结算口径）
- **不做每日额度微调**（背单词记「新词」还是「新词+复习」另议，不阻塞 S8）

## 13. 需要姜来提供的

1. **注册 Cloudflare 账号**（免费）：https://dash.cloudflare.com/sign-up
2. **建一个 API Token**：My Profile → API Tokens → Create Token →
   模板选 **Edit Cloudflare Workers** → 复制发我（只用于部署，建 KV、部署 Worker）
3. 建 Worker / KV / 部署 / 写客户端 / 改 cron / 测试 —— **我全包**

（Worker 的 admin token 与 ingest token 由我在部署时生成：admin token 只进 Mavis secret，
ingest token 同时进 Mavis secret 与 `data/sources.json`。）

## 14. 上线后的验证清单

- [ ] 番茄钟页点同步 → 原地显示「已记录」
- [ ] 背单词页点同步 → 原地显示「已记录」
- [ ] KOReader 落地页带天数进来 → 点同步 → 原地显示「已记录」
- [ ] `GET /health` 返回 `pending` 增长
- [ ] cron 跑完后账本进账、图鉴页多一只
- [ ] 故意断网点同步 → 提示「连不上」+ 老办法入口出现 → 恢复网络后重开页面自动补发
- [ ] 现有 661 个测试仍全绿
