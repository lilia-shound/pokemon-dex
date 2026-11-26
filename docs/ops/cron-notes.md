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

## ⚠️ 当前 schedule：每天 4 次

```
17 8,13,18,23 * * *        # 8:17 / 13:17 / 18:17 / 23:17
```

**以前是 `17 */3 * * *`（每 3 小时，一天 8 轮）** —— 2026-11-26 降频了，原因见下。

## ★ 为什么降频：常驻会话会把额度烧光

2026-11-26 上午，cron 从 11-25 12:37 起**连续 5 次全失败**，错误全是
「已达到 Token Plan 用量上限（2056）」，之后**平台自动把任务暂停了**
（`enabled` 变 false，`updated_at` = 最后一次失败的时间）。

**根因不是偶然超支**：

| | |
|---|---|
| 常驻会话的上下文 | **676,551 / 1,000,000 token** |
| 其中历史消息 | **645,186 token** |
| 每轮要重带的量 | 约 64 万 |
| 一天 8 轮 | 约 540 万 token/天 |

⇒ 每轮 cron 都要**把整个对话历史重发一遍** ⇒ 额度必然被烧光。

### 两条真正的解法（还没定，等姜来选）

1. **她在客户端那个对话框里手动压缩一下** ——
   session 的 features 里有 `manual_compaction`，客户端应当有对应的操作。
   压缩后成本回到正常水位，**而且保住「一个对话框」**。
2. **把 cron 改成 `session: {mode: "new"}`** ——
   每轮新会话，上下文只剩 prompt + 工具定义（约 8k）⇒ 成本降 60 倍。
   ⚠️ 但这违背姜来 11-14 说的「把这个每日判定放在一个对话框里」。
   ⚠️ **改的时候，提示词里「这个任务跑在一个固定的常驻会话里…不要新开会话」那句要一起改**，否则会误导。

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
- 降频只是止损 ⇒ 真正的解法是上面两条之一（压缩上下文 或 改 new 会话）。

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

## 别自己动的东西

- `data/sources.json` 的 `perAward`（现在 2）、各源的 `per`
  （weread 900 秒 / pom 25 分钟 / koreader 15 分钟 / words 100 个）
  ⇒ **没得到姜来明确同意不要改**。
- `data/dex.json` 的 `form` 字段（形态推进由 `award.py` 自己算，幂等）。
- 不要再跑 `--migrate-only`（只该在迁移当天跑一次，跑多了会把当天的读数清零）。
