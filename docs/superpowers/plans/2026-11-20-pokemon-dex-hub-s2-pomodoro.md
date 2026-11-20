# 训练家图鉴中枢 S2（网页版番茄钟 + Issue 投递箱） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让番茄钟成为第二个行为源。网页版番茄钟（沿用 pokepomo 的探头 / 摸鱼监督 / 1/96 监督宝可梦）把每天的专注分钟数攒在 localStorage，点「同步」打开 GitHub 预填好的 Issue 提交页；服务端 `scripts/collect_reports.py` 读回这些 issue、校验、并进账本，于是番茄钟与微信读书的秒数汇进**同一份额度**。番茄钟上线后把 `perAward` 改回 2（阈值 1800 秒）。

**Architecture:** 前端 `site/pomodoro/` 是零依赖 vanilla JS（`index.html` + `app.js` + `style.css` + `manifest.webmanifest` + `sw.js`）。宝可梦立绘走图鉴页 CDN（`site/img/<id>.webp`），已收集清单走 `site/dex.json`（公开文件），**都不内嵌** —— 1028 张图 base64 ≈ 49 MB，双击会卡死。上报走 **GitHub Issue 投递箱**（零注册、零基建、零费用）：客户端只负责打开一个预填链接并让人点 Submit，服务端读 issue 校验后并账。结算仍然只有 `scripts/award.py` 一个执行者（它读账本里各源的量），`collect_reports.py` 只负责「把 push 源的量搬进账本」。

**Tech Stack:** Python 3.11（标准库 only，零第三方依赖）+ unittest；前端零依赖 vanilla JS + WebAudio 合成音效（不引音频文件）；GitHub Issue 投递箱；GitHub Pages。

**上游设计：** [2026-11-18-pokemon-dex-hub-design.md](../specs/2026-11-18-pokemon-dex-hub-design.md) §3 / §6.1 / §7 / §8.2

**参考实现：** `/workspace/pokepomo/template.html`（954 行单文件番茄钟）—— 探头公式、摸鱼检测、探头角色、WebAudio 音效都从这里移植。**原件一点不动**，中枢另做网页版。

---

## Global Constraints

这些约束适用于**每一个** Task，不需要在 Task 里重复。

- **Python 3.11，标准库 only。** 不引入 pytest / requests / pydantic。GitHub API 用 `urllib.request`。
- **测试放 `tests/`，文件名 `test_*.py`，类名 `XxxTest`，方法名 `test_xxx`。**
- **中文注释与中文 docstring。**
- **`scripts/ledger.py` / `scripts/dex_core.py` / `scripts/sources.py` 一律不碰网络、不碰文件**（`sources.load_config` 读注册表这一处除外）。新脚本 `scripts/collect_reports.py` 里**网络与解析必须分开**：解析是纯函数，可单测；网络层只有薄薄一层。
- **结算只认北京时间（Asia/Shanghai）。** 所有日期是 `YYYY-MM-DD` 字符串。
- **一天最多 1 只**（注册表根级 `maxPerDay` = 1），额度按天分桶、跨天清零。
- **抽卡权只在服务端。** 客户端只报「我投入了多少」，绝不自己抽卡、绝不写状态。
- **`perAward` 的取值会直接影响她的日常**：现在是 1（阈值 900 秒）。**S2 的最后一步才改回 2（阈值 1800 秒）**，而且必须在番茄钟已经上线、她能用之后 —— 否则她没有番茄钟可用却要 30 分钟才掉一只，streak 会直接断掉。
- **现有读方读的字段一个都不能删**：`collected[].id / .nameZh / .shiny / .at`、`streak`、`today.seconds`、`today.threshold`。
- **提交前 `python3 -B run_tests.py` 必须全绿**（NAS 文件系统会缓存 `__pycache__`，跑 Python 一律加 `-B`）。
- **一次提交只做一件事**，commit message 用中文，动词开头。
- **沙箱 git**：`export GIT_SSL_NO_VERIFY=1`；push 用 `git -c http.expectContinue=false push origin HEAD:main`；remote URL 不放 token，用 credential helper；TLS 握手不稳 → 重试 3~6 次，间隔 7~12 秒。
- ⚠️ **这台机 `git add -A` 也会漏改动**（不只是 `git status`）：改完文件若没进提交，用 `touch <文件> && git update-index --really-refresh` 再 `git add`。
- ⚠️ **远端 main 会被 cron 推 commit**（cron 跑 `deploy.sh` 时会提交 `data/dex.json` + `site/dex.json`）：推送前先 `git fetch origin main` 并 rebase，否则 non-fast-forward 被拒。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `scripts/sources.py` | 修改 | `credit_for_day` 夹上源级 `maxPerDay`（把死旋钮拧上） |
| `scripts/collect_reports.py` | 新建 | Issue 投递箱接收端：拉 open issue → 校验 → 并进账本 → 评论 + 关闭 |
| `tests/test_sources.py` | 修改 | 补 `maxPerDay` 裁剪用例 |
| `tests/test_collect_reports.py` | 新建 | 报告解析器（纯函数）的校验用例 |
| `site/pomodoro/index.html` | 新建 | 番茄钟页面外壳 |
| `site/pomodoro/app.js` | 新建 | 计时环 / 探头计划 / 摸鱼检测 / 音效 / localStorage / Issue 同步 |
| `site/pomodoro/style.css` | 新建 | 果冻感样式（沿用 pokepomo 的配色与动效语言） |
| `site/pomodoro/manifest.webmanifest` | 新建 | PWA manifest（可装到手机桌面） |
| `site/pomodoro/sw.js` | 新建 | Service Worker：**只缓存应用外壳**，不缓存宝可梦图片 |
| `data/sources.json` | 修改 | `perAward` 1 → 2（最后一步） |
| `data/dex.json` + `site/dex.json` | 修改 | `today.threshold` 900 → 1800（最后一步） |
| `scripts/deploy.sh` | 修改 | gh-pages 构建时带上 `site/` 全部内容（pomodoro 目录自动包含） |
| `README.md` | 修改 | 番茄钟的用法、上报协议、perAward=2 的含义 |
| `.superpowers/sdd/2026-11-20-pokemon-dex-hub-s2-pomodoro/progress.md` | 新建 | 账本 |

---

## Task 1: 源级 `maxPerDay` 裁剪

**背景**：`sources.json` 里每个源都有 `maxPerDay`（番茄钟 ≤ 1440 分钟、微信读书 ≤ 86400 秒），但**代码里没人读它** —— 客户端报「今天专注了 99999 分钟」，服务端照单全收，`99999 // 25 = 3999` 个额度。这是 S1 留下的死旋钮，S2 有 push 源了，必须先拧上。

**改动**：`scripts/sources.py` 的 `credit_for_day` —— 折算前先把原始量夹到 `maxPerDay`。

```python
def credit_for_day(cfg: dict, ledger_day: dict) -> dict[str, int]:
    """每个启用的源当天折算出多少额度（向下取整，零头不计）。

    原始量先夹到该源的 maxPerDay —— 客户端可以随便报，
    一天最多就那么长，多出来的不是额度。
    """
    out: dict[str, int] = {}
    for sid, s in enabled_sources(cfg).items():
        amount = int((ledger_day or {}).get(sid, 0) or 0)
        cap = s.get("maxPerDay")
        if cap is not None:
            amount = min(amount, int(cap))
        out[sid] = amount // int(s["per"])
    return out
```

**测试**（`tests/test_sources.py`）：

- `test_credit_clipped_to_source_max_per_day`：番茄钟报 99999 分钟 → 额度 = 1440 // 25 = 57（不是 3999）
- `test_credit_not_clipped_when_below_cap`：报 50 分钟 → 2 额度
- `test_source_without_max_per_day_is_not_clipped`：没有 `maxPerDay` 的源不受影响
- `test_negative_amount_is_clipped_to_zero`：负数 → 0 额度（不是负数）

**验收**：`python3 -B run_tests.py` 全绿。

---

## Task 2: Issue 报告解析器（纯函数）

**背景**：GitHub Issue 是「任何人可写」的通道，解析必须严格，且**任何一条校验不过就丢弃整个 issue**（不部分采纳），否则一条坏数据能污染账本。

**新增** `scripts/collect_reports.py` 里的纯函数：

```python
REPORT_TITLE_PREFIX = "[report]"
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
MAX_REPORT_DAYS = 30

def parse_report(body: str, cfg: dict, today: str) -> tuple[dict[str, int], str | None]:
    """解析一条报告的 body。成功返回 (days, None)，失败返回 ({}, 原因)。

    语义：days[day] = 该源「当天累计总量」（不是增量）。
    任何一条校验不过 → 整个报告丢弃，不部分采纳。
    """
```

校验清单（任一不过 → 整个 issue 丢弃）：

1. body 能解析成 JSON，且是对象
2. `v` == 1
3. `source` 是字符串，且在注册表里、`mode == "push"`、`enabled` 为真
4. `days` 是对象，条数 ≤ 30
5. 每个 key 是合法 `YYYY-MM-DD`，且**不晚于今天**
6. 每个 value 是**非负整数**（`bool` 不算整数）
7. 每个 value ≤ 该源的 `maxPerDay`

**测试**（`tests/test_collect_reports.py`）：

- `test_valid_report_returns_days`
- `test_rejects_unknown_source`
- `test_rejects_pull_source`
- `test_rejects_disabled_source`
- `test_rejects_wrong_version`
- `test_rejects_bad_json`
- `test_rejects_future_day`
- `test_rejects_bad_date_format`
- `test_rejects_negative_amount`
- `test_rejects_non_integer_amount`
- `test_rejects_bool_amount`（`True` 是 int 的子类，必须显式拒）
- `test_rejects_amount_over_source_max_per_day`
- `test_rejects_too_many_days`（31 天）
- `test_one_bad_day_rejects_whole_report`（不能部分采纳）

---

## Task 3: `scripts/collect_reports.py` 编排层

**改动**：在 Task 2 的纯函数之上加薄薄一层网络与 IO：

```python
def list_report_issues(token: str, repo: str) -> list[dict]:
    """拉 open issues，只留 title 以 [report] 开头的。"""

def issue_body(issue: dict) -> str: ...
def is_trusted(issue: dict) -> bool: ...      # author_association ∈ 白名单
def comment_and_close(issue: dict, token: str, repo: str, note: str) -> None: ...
```

主流程（`main()`）：

1. 拉 open issues → 按 title 前缀筛
2. 逐条：不可信 → 跳过并打印（**不关闭**，留给人工看）；可信 → `parse_report`
3. 解析成功 → `ledger.merge_amounts(state["ledger"], source, days)`（同源同日取 max ⇒ 幂等）
4. 全部处理完 → 写回 `data/dex.json`（只动 `ledger` 字段，其余原样）
5. 对每条成功采纳的 issue：评论「采纳了 X/Y 天的量，共 N 个额度」→ 关闭
6. 对每条失败的 issue：**打印原因，不关闭**
7. `--dry-run` 只打印不写盘不评论不关闭

**边界**：issue 的 `author_association` 为空（GitHub 对非登录用户返回 `NONE`）→ 不可信 → 跳过 ✓。

**测试**：`tests/test_collect_reports.py` 里给编排层补「不联网」的用例 —— 用假 issue 列表跑 `process_issues(issues, cfg, today, ledger)` 纯函数化的编排，返回 `(新账本, 处理结果列表)`；网络层单独。

**验收**：`python3 -B run_tests.py` 全绿。

---

## Task 4: 网页版番茄钟

**背景**：`/workspace/pokepomo/template.html` 是已经跑通的单文件番茄钟（探头、摸鱼监督、1/96 监督宝可梦、WebAudio 音效、计时环）。S2 要做一个**中枢版**：图走 CDN、已收集清单走 `site/dex.json`、多源同步走 Issue 投递箱。**原件一点不动。**

**移植清单**（从 pokepomo 逐条搬）：

| 能力 | 规则 |
|---|---|
| 探头次数 | ≤15 分钟 1~2 次；≤30 分钟 2~3；≤45 分钟 3~4；更长 3~6 |
| 探头时刻 | 落在专注时长的 7%~93% 之间 |
| 探头间隔 | 相邻间隔 ≥ `时长 / (n + 2)` |
| 优先派没收集过的 | 用 `site/dex.json` 的 `collected[].id` 算已收集集合 |
| 监督宝可梦 | 1/96 概率出现；有它时切走页面 > 3 秒记一次摸鱼 |
| 摸鱼作废 | 一轮 3 次 ⇒ 整轮作废，**这一轮不计入统计** |
| 探头不打断计时 | 探头只是飘在角落，点一下收进图鉴 |
| 音效 | WebAudio 合成（不引音频文件） |
| 累计 | 一轮 focus 正常结束 ⇒ 当天 minutes += focusMin，poms += 1 |

**S2 新增**：

- **图走 CDN**：`https://lilia-shound.github.io/pokemon-dex/img/<id>.webp`；拉不到（离线 / 404）⇒ 退回灰色剪影，**不阻塞计时**。需要一份宝可梦清单（id / nameZh / art / artShiny / types）—— 从 `site/pokedex.json` 拉（1.8 MB，太大）⇒ **另出一份精简版 `site/pomodoro/pokedex-lite.json`**：只要 id / nameZh / art / artShiny，1028 只 ≈ 60 KB。
- **已收集清单**：`site/dex.json`（小文件，图鉴页已在用同一份）
- **同步**：按钮生成
  `https://github.com/lilia-shound/pokemon-dex/issues/new?title=<urlencoded>&body=<urlencoded>`
  - title：`[report] pom 2026-11-20`
  - body：`{"v":1,"source":"pom","days":{"2026-11-19":50,"2026-11-20":25}}`
  - `days[day]` = **当天累计总量**（不是增量）
  - 只带最近 30 天
  - 同步后**不立刻清 localStorage**（服务端取 max，重复投递幂等）；但要让用户知道「已同步过哪些天」
- **PWA**：`manifest.webmanifest`（name / icons / start_url / scope 都在 `./`）+ `sw.js`。**SW 只缓存应用外壳**（HTML/CSS/JS/manifest），**不缓存宝可梦图片** —— 图片走 HTTP 缓存，避免把几十 MB 塞进 SW 缓存。
- **离线可用**：计时、探头、摸鱼、音效全在本地；只有「同步」需要联网（要开 GitHub）。

**测试（观感断言，真实浏览器 + Playwright）**：

- `test_pomodoro_page_loads`：页面无控制台错误、计时环出现
- `test_peek_plan_respects_formula`：注入固定 rng，探头数落在公式区间、时刻在 7%~93%、间隔 ≥ 时长/(n+2)
- `test_image_falls_back_to_silhouette`：把图片请求断掉 → 显示剪影，计时照走
- `test_idle_detection_counts_slack`：模拟 `visibilitychange` → 摸鱼计数 +1
- `test_three_slacks_abort_round`：3 次摸鱼 ⇒ 整轮作废、当天 minutes 不增加
- `test_minutes_accumulate_on_complete`：正常完成一轮 ⇒ localStorage 的 minutes += focusMin
- `test_sync_link_contains_report_json`：点同步 ⇒ 生成的 URL 里 body 能 parse 出 `{"v":1,"source":"pom","days":{...}}`
- `test_sw_does_not_cache_images`：SW 的缓存名单里没有 `.webp`

**验收**：截图 + 数值断言全过；`python3 -B run_tests.py` 仍全绿。

---

## Task 5: `perAward` 改回 2 + 阈值 1800 + 文档 + cron

**前提**：Task 4 的番茄钟已经**发布上线**（她能用），否则这一刀会让她没番茄钟却要 30 分钟才掉一只 → streak 直接断。

**改动**：

- `data/sources.json`：`perAward` 1 → 2
- `data/dex.json` + `site/dex.json`：`today.threshold` 900 → 1800（= `perAward × weread.per`）
- `README.md`：写清「现在要 2 个额度才掉一只」的含义与三种凑法
- **Mavis cron 提示词**（`463191742394721`）：加一步「先跑 `scripts/collect_reports.py`」；阈值口径改成 1800 秒 / 2 额度；汇报口径改成「额度 X/2」
- `scripts/check_today.py`：确认打印的是额度口径（已是）

**注意**：`today.threshold` 是**微信读书秒数口径**（`perAward × weread.per`），读方拿它显示「掉一只要读多久」。perAward=2 ⇒ 1800 秒 ⇒ 图鉴页显示「30/30 分」。番茄钟那 1 个额度不体现在这个数字里 —— **多源额度进度是 S5 的活**，S2 期间这个数字只代表微信读书口径（要在 README 里写清楚，别让人以为读满 30 分钟就一定掉）。

**验收**：线上图鉴页显示 `30/30 分`；`python3 -B run_tests.py` 全绿。

---

## Task 6: 发布 + 收尾

1. `scripts/deploy.sh` 重建 gh-pages（**会带上 `site/pomodoro/`**）—— 确认 deploy.sh 的 `cp -r "$ROOT/site/." .` 已经覆盖子目录 ✓
2. 线上实测：
   - 图鉴页不受影响（阈值改成 1800 → 显示 30/30 分）
   - `https://lilia-shound.github.io/pokemon-dex/pomodoro/` 能开、PWA manifest 能取到
3. 端到端演练（**用真 Issue 走一遍**）：
   - 本地起一个假 issue 形状（body 是合法 JSON）→ 跑 `collect_reports.py` 的编排层 → 看账本是否并进去
   - `award.py --dry-run` 看额度是否算对
4. 账本 `progress.md` 记 S2 的全部结论
5. 记忆更新（`pokemon-dex-hub` topic）

---

## Self-Review

**已知的坑（动手前先想清楚）**：

1. **`perAward` 翻 2 会改她的日常节奏** —— 这是她 2026-11-18 亲自裁定的（「番茄钟和阅读平权」），但翻之前必须保证番茄钟能用。顺序不能反。
2. **客户端报的是「当天累计总量」不是增量** —— 服务端取 max 才是幂等的关键。客户端如果报增量，重复同步会丢数据（取 max 只会保留最大的增量）。
3. **Issue 通道是「任何人可写」** —— 三道防线（association 白名单 / 数值上限 / 取 max），残余风险是「管理员本人可以给自己加额度」，属于自伤，可接受（spec §7.3）。
4. **SW 缓存图片会把几十 MB 塞进手机缓存** —— 只缓存外壳。
5. **`pokedex.json` 1.8 MB 太大** —— 番茄钟要另出一份精简版，否则手机上打开会卡。
6. **摸鱼作废的那一轮不计入统计** —— 客户端和服务端都要一致（服务端只认客户端报的总分钟数，所以规则必须落在客户端）。
7. **11-19 的断签提醒**：她现在日读 ~1780 秒 ≈ 1 个额度，perAward=2 之后单靠阅读有几天会不够 —— 这是预期内的（番茄钟就是来补那 1 个额度的），但要跟她说清楚，别让她以为坏了。

**不做**（YAGNI，S5/S6 的活）：多源额度进度条、进化形态展示、链养成进度条、链形态改写。
