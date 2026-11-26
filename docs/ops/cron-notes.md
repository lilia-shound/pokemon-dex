# 运维笔记：发奖 cron（每日判定）

> 这份是**运维口径的活文档** —— 跟「口径」相关的改动必须同时改 cron 的提示词（那份提示词才是权威副本）。
> 详细设计在 `docs/superpowers/specs/` 与 `docs/superpowers/plans/`；SDD 账本在 `.superpowers/sdd/`（**不入库**）。

## 这个 cron 是干什么的

- **它才是真正的结算执行者**（不是 GitHub Actions —— 那个 workflow 只有手动触发）。
- 任务：`463191742394721`，名字「训练家图鉴 · 每日判定」，时区 `Asia/Shanghai`。
- 跑的会话：常驻会话 `463516903985550`（姜来 11-14 明确要「放在一个对话框里」）。
- 每轮做六件事（详见它的提示词）：
  1. `scripts/collect_worker.py` —— 收**免登录投递通道**（S8：网页版番茄钟 / KOReader 落地页 / 背单词页直接 POST 到公网接收端）
  1.5 `scripts/collect_reports.py` —— 再收**老办法**的 GitHub Issue 投递箱（两条通道都要跑）
  2. `scripts/check_today.py` —— 今日各源的量与额度
  3. `scripts/award.py` —— 判定并抽卡
  3.5 `scripts/notify.py` —— 邮件通知（旁路）
  3.6 `scripts/history.py` —— 回填最近 30 天历史（只写 `data/history.json`）
  4. `bash scripts/deploy.sh` —— **有改动就部署**（读数变了 / 采纳了报告 / 历史变了）

## ✅ 当前 schedule：每 3 小时一次（`17 */3 * * *`）

```
17 */3 * * *              # 00:17 / 03:17 / 06:17 / 09:17 / 12:17 / 15:17 / 18:17 / 21:17
```

**2026-11-26 中午临时降成过每天 4 次（`17 8,13,18,23 * * *`）**，当天 17:47 她充了额度、
手工跑通一轮之后，**已恢复成每 3 小时**（因为会话瘦身后额度撑得住，见下）。

## ★ 为什么曾经降频：常驻会话会把额度烧光

2026-11-26 上午，cron 从 11-25 12:17 起**连续 5 次全失败**（12:17 / 15:17 / 18:17 / 21:17 / 次日 00:17），
错误全是「已达到 Token Plan 用量上限（2056）」，之后**平台自动把任务暂停了**
（`enabled` 变 false，`updated_at` = 最后一次失败的时间）。

### ★ 2026-11-26 13:17 那轮（降频后的第一次）：又失败，而且**额度不是按天重置的**

| | |
|---|---|
| run_id | `467422006854383`，13:17 准点触发 |
| 失败方式 | **83 毫秒就报错**（`total_tokens: 0`）⇒ **请求压根没发出去**，不是跑半路撞墙 |
| enabled | **仍是 true**（这次是**恢复后第一次**失败 ⇒ 平台的自动暂停计数还没到） |
| 会话上下文 | **677,281 / 1,000,000**（历史消息 650,260） |

★ **常驻会话怎么烧的**（这是「烧得快」，不是「这次报错的直接原因」）：

| | |
|---|---|
| 每轮要重带的量 | 约 **67 万 token**（整段对话历史） |
| 一天 4 轮（现在的频率） | 约 **270 万 token/天** |
| 一天 8 轮（以前的频率） | 约 **540 万 token/天** |
| 换新会话后每轮 | 约 **2 万**（提示词 + 工具定义）⇒ 一天 8 轮才 16 万 |

★ **关键推论**：失败从 **11-25 12:17 一路连到 11-26 13:17**，**跨了一天多、连着 7 次全败**
⇒ **Token Plan 额度不是每天重置的** ⇒ **光降频解决不了，充值是必须的**。

⇒ 所以三件事的分工是：
- **充值** = 让 cron 能重新跑起来（前提，绕不过去）；
- **手动压缩那个对话框 / 改成每轮新会话** = 把「每轮重带 67 万」这个烧法修掉
  （日耗 270 万 → 8 万），**充值的那点积分才撑得住**；
- 降频只是临时的省法，不是解法。

### ★ cron 坏了也能手动补跑（重要）

cron 跑不了的那套流程，**在一个有 secret 的沙箱里就能跑完**：
`collect_worker.py` → `collect_reports.py` → `check_today.py` → `award.py` → `notify.py`
→ `history.py` → （有改动才）`deploy.sh`。

2026-11-26 15:06 就手动补跑过一轮：投递通道队列空、issue 0 条、当天 0 秒 0/2 → 没抽到、
通知「无」、历史 12 天、**无数据改动 ⇒ 不用部署**。
⇒ **cron 挂着不动的时候，判定不会因为 cron 坏而漏掉**（`award.py` 幂等，重复跑不会多发）。

```bash
cd /workspace/pokemon-reading-dex
python3 scripts/collect_worker.py     # 投递通道（Worker）
python3 scripts/collect_reports.py    # issue 老通道
python3 scripts/check_today.py        # 今天各源的量
python3 scripts/award.py              # 判定抽卡（幂等）
python3 scripts/notify.py             # 邮件（旁路）
python3 scripts/history.py            # 回填 30 天历史（只写 history.json）
git status --short                    # 有改动才 bash scripts/deploy.sh
```

★ 跑这一套需要沙箱的环境里有 `WEREAD_API_KEY` / `WORKER_URL` / `WORKER_ADMIN_TOKEN` /
`GITHUB_TOKEN` / `RESEND_*` —— 也就是**和 cron 一样的环境** ⇒ 沙箱里手动跑是等价的。
★ 别用 `&&` 把 `collect_worker.py` 和 `award.py` 串起来（前者失败会把后者一起带崩）。

### ✅ 已定：2026-11-26 17:50，姜来选了「换新会话 + 频率调回每 3 小时」

| | 改前 | 改后 |
|---|---|---|
| `session` | `sessionId`（常驻会话 `463516903985550`） | **`mode: "new"`**（每轮一个新会话） |
| `schedule` | `17 8,13,18,23 * * *`（每天 4 次） | **`17 */3 * * *`（每 3 小时，一天 8 轮）** |
| 提示词 | 「跑在一个固定的常驻会话里…不要新开会话」 | **「每轮跑在一个全新的会话里」+「口径在提示词和仓库里，去读仓库别翻对话历史」** |

★ **实测数字（同一套脚本、同一天）**：常驻会话那一轮 `cache_read 667,619 + input 87` ⇒ **66.8 万 token**；
换成新会话后，**18:17 那轮实测 `used_tokens = 34,249`** ⇒ **省了 95%、约 19 倍**
（这 3.4 万 = 固定开销 ~1.9 万（提示词+工具+技能）+ 这轮自己的对话 1.5 万）。
⇒ 一天 8 轮 ≈ **27 万 token/天**（原来 540 万）⇒ 额度撑得住。

★ **口径没丢**：提示词里本来就写着完整的判定口径（达标线、各源的 `per`、汇报规则…），
`docs/ops/cron-notes.md` + `data/dex.json` + `git log` 也在仓库里 ⇒ 新会话照样跑得对。
新提示词的入库副本：`docs/ops/cron-prompt-new-session.txt`（**改 cron 提示词时以它为准**）。

★ **换法**（`cron_update`）：
```json
{"task_id":"463191742394721","schedule":"17 */3 * * *","session":{"mode":"new"},"prompt":"<docs/ops/cron-prompt-new-session.txt 的全文>"}
```

### ✅ 2026-11-26 18:17：新会话模式的第一次定时跑，**通了**

- run_id `467496245616997`，**没有 error_code**；`session_id` = `467490470429946`（**新会话**）
- `enabled` 仍是 true；下一轮 21:17
- **这一轮只带了 34,249 token**（对比常驻会话的 667,948）
- 跑完的结论：**没有新抽卡**（今天这只早些时候已抽过）⇒ 账本/站点无变化 ⇒ **没触发部署**（远端 main 未动）✓
- 两条通道都跑了：投递通道队列空、issue 投递箱 0 条；通知「无」可发内容；history 13 天无变化
- 报告说得对：「今天已经发过奖了」—— `award.py` 幂等，没多发 ✓

★ 顺带：`MEMORY` 分量在那个新会话里是 **0**（常驻会话里是 7,195）⇒ 新会话更干净。

### 📌 2026-11-26 17:47：额度恢复后的第一轮，跑通了

- 姜来充了额度 ⇒ 17:47 手工触发一轮 ⇒ **成功**（没再报 2056）。
- 抽到 **沙河马 No.0449**（非闪光）：微信读书 1508 秒（1 额度）+ 番茄钟 25 分钟（1 额度）= **2/2 达标**，
  **连读 7 天，图鉴 12/1028**，邮件发了 1 封，线上已确认（`site/dex.json` 里 collected 12 条）。
- **Issue #6 被采纳**：一份番茄钟报告一次覆盖 11-21 / 11-22 / 11-23 / 11-26 四天 ⇒ 账本 13 天。
- ⚠️ **她实机交的那一下走的是老通道（GitHub Issue）** —— 新的免登录直传通道**队列还是空的**
  ⇒ **新通道还没在真机上跑过**（沙箱里我手工验过是通的）。

### 排查「cron 是不是坏了」

```bash
# 1) 看任务本身（★ 别只看运行记录里的 error_code，要看 enabled）
mcp_invoke cron_read --args '{"view":"detail","task_id":"463191742394721"}'
#    enabled=false ⇒ 被自动暂停了 ⇒ cron_update enabled=true 恢复
#    last_error / status_message ⇒ 失败原因

# 2) 看运行记录（最近几次的 error_code）
mcp_invoke cron_read --args '{"view":"runs","task_id":"463191742394721","limit":10}'

# 3) 看那个常驻会话烧了多少上下文（★ 判断会不会再烧光额度就看它）
mcp_invoke session_messages --args '{"session_id":"463516903985550","limit":1}'
#    → context_usage.used_tokens
```

### 两条恢复要点

- **平台连续失败后会自动暂停任务** ⇒ 恢复时必须显式 `enabled=true`，
  而且要**同时查额度**，不然它会再失败、再被暂停、再来一轮失败提醒邮件。
- 降频只是止损 ⇒ 真正的解法已经做了：**换 new 会话**（见上面「已定」那节）。
  ⇒ 以后再看到「Token Plan 用量上限」，先想「是不是又回到常驻会话了 / 额度是不是又见底了」。

## ★ 判定「有没有抽到」

**只看 `data/dex.json` 的 `collected` 真多了一条**。
`award.py` 只更新 `today` 滚动块（date/seconds）时，`git status` 会显示 `M data/dex.json`，
那是正常状态、**不算抽到**。

## 两条通道都要跑

S8 起有两条上报通道，**cron 的第 1 步和第 1.5 步都要跑**：

- **投递通道（新的，免登录）**：网页版番茄钟 / KOReader 落地页 / 背单词页把数据直接 POST 到
  `https://pokemon-dex-ingest.pages.dev` ⇒ cron 从那儿拉队列、校验、并账本。
  需要环境里的 `WORKER_URL` 与 `WORKER_ADMIN_TOKEN`（admin token 只在 secret 里）。
- **Issue 通道（老的，还在）**：已装好的旧页面照常用。

⚠️ **第 1 步是旁路** —— 它失败**绝不能**因此不发奖。
⚠️ **别用 `&&` 把 `collect_worker.py` 和 `award.py` 串起来** —— 前者失败会把后者一起带崩。

## 投递通道实测（2026-11-26 上午，手工自检）

在沙箱里手工把整条通道走了一遍（投一条合成报告 → 拉 → 并账 → ack），三个结论：

1. **通道本身是通的**：`POST /ingest` 2~5 秒返回；`/health`、`/pull`、`/ack` 都正常。
2. **失败形态不是「慢」，是「响应丢了」** ——
   ★ **服务端把活干完了，响应没回到沙箱**：ack 的请求在客户端看着是「挂死」，
   但事后用 wrangler 直接看 KV，那三个 key **确实被删掉了**（队列从 3 变 0）。
   ⇒ 所以 `pull` 超时**不等于数据丢了**：条目还在队列里，下一轮会重拉。
3. **重试次数不够**：连着两次拉取，第一次挂满超时、第二次 15 秒就成了；
   另一次两次都挂满 ⇒ 已把 `PULL_ATTEMPTS` 从 2 提到 3、单次超时 120 → 60 秒
   （ack 也收紧成 45 秒 × 2 次，别把一整轮 cron 的时间耗在 ack 上）。

### 症状对照

| cron 输出 | 含义 | 要不要管 |
|---|---|---|
| `[投递] 队列是空的` | 通道正常，这轮没人交东西 | 不用管 |
| `[投递] 采纳 q:… pom {…} → 额度 {…}` | 有新报告进账本了 | 不用管（deploy 会推上去） |
| `[投递] 拒收 q:… <原因>` | 服务端拒收了一条（如实说一句，别自己动手修） | 提一句 |
| `[投递] 拉取失败：…，跳过` | **这一轮的量没进账本**（但数据还在队列里，下轮会补上） | 提一句「投递通道这一轮没拉到」 |

### 手工自检（沙箱里，不碰她的账本）

```bash
cd /workspace/pokemon-reading-dex
TOKEN=$(python3 -c "import json;print(json.load(open('data/sources.json'))['ingest']['token'])")
# 1) 投一条合成报告（1 分钟番茄钟 = 0 个额度，不会触发发奖）
curl -sS -m 90 -A 'Mozilla/5.0' -X POST "${WORKER_URL}/ingest" \
  -H "Content-Type: application/json" -H "X-Dex-Token: $TOKEN" \
  -d '{"v":1,"source":"pom","days":{"2026-11-26":1}}'
# 2) 看队列
curl -sS -m 90 -A 'Mozilla/5.0' "${WORKER_URL}/health"
# 3) 只看不写（不并账、不 ack）
python3 scripts/collect_worker.py --dry-run
# 4) 跑真的，但账本写副本 ⇒ 队列清掉、她的真账本一个字都不动
cp data/dex.json /tmp/dex-copy.json
DEX_PATH=/tmp/dex-copy.json python3 scripts/collect_worker.py
```

★ **别用真账本跑第 4 步**（那会把合成数据写进她的图鉴）。
★ 队列卡住清不掉时，可以直接用 wrangler 删键：
`wrangler kv key list --namespace-id 43e6d8148b6a49c3999a5d8a7ee96738`。



- `data/sources.json` 的 `perAward`（现在 2）、各源的 `per`
  （weread 900 秒 / pom 25 分钟 / koreader 15 分钟 / words 100 个）
  ⇒ **没得到姜来明确同意不要改**。
- `data/dex.json` 的 `form` 字段（形态推进由 `award.py` 自己算，幂等）。
- 不要再跑 `--migrate-only`（只该在迁移当天跑一次，跑多了会把当天的读数清零）。
