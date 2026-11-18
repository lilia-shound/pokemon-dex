# 训练家图鉴中枢 —— 多行为收集中枢设计文档

- 版本：v1.0（2026-11-18）
- 状态：设计已获姜来确认
- 上游设计：[2026-11-14-pokemon-reading-dex-design.md](./2026-11-14-pokemon-reading-dex-design.md)
- 项目目录：`/workspace/pokemon-reading-dex/`

## 1. 目标

把「读满 N 分钟 → 随机获得一只宝可梦」这个内核，从**微信读书专用**升级成**多行为通用**：

> 任何能报出「我投入了多少」的行为，都汇进**同一个图鉴**。
> 阅读、番茄钟、备考…… 全都是同一只图鉴的供给来源。

起因是小红书那个 KOReader 宝可梦插件。姜来指出：**读书的途径本来有很多**（微信读书 / KOReader / 本地 epub / 其他 App），
如果奖励绑死在某一个 App 上，等于把机制做窄了。真正值钱的是内核，不是外壳。

现状是 `scripts/dex_core.py` 已经把内核切得很干净（纯逻辑、不碰网络不碰文件），
微信读书只占「信号源」这一个位置。**所以「拿它做些别的」不是移植，是换一个信号源插进去。**

## 2. 已确认的产品决策

| 决策项 | 结论 |
|---|---|
| 总方向 | **全行为收集中枢**：阅读 + 番茄钟 + 备考 汇进同一个图鉴，宝可梦保留 |
| 掉落规则 | **各源折算成自己的额度，攒够 N 个额度掉 1 只，一天最多 1 只** |
| 额度跨天 | 按天分桶，**跨天清零**（一天最多 1 只，当天多攒的没地方花） |
| 番茄钟接入 | **`pokepomo` 原件一点不动**，中枢**另做一个网页版番茄钟** |
| 新番茄钟功能 | **探头 + 摸鱼监督都保留**，宝可梦图走图鉴页 CDN |
| 落地范围 | **全做**（六个子项目，见 §8），但**第一刀只切 S1 内核** |
| 内核存储 | **单文件扩展**：`dex.json` 加 `ledger` 字段，源注册表单独放 `sources.json` |
| push 源接收端 | **GitHub Issue 投递箱**（零注册、零基建、零费用） |
| 抽卡权 | **只在服务端**。客户端只报「我投入了多少」，绝不自己抽 |
| pokepomo 的产出物 | 单文件离线番茄钟，211 KB，零外链。**保持原样，不并入中枢** |
| 进化链 | 链级 `care` 计数：养到一定天数推进形态，满 30 天给一次保底闪光 |
| 共读小屋 | **不搭载**（沿用上游决策） |

## 3. 概念模型：额度（credit）

不同行为的单位不可通约（秒 / 分钟 / 个词），但**每天掉一只宝可梦的名额是统一的**。
解法是引入一层中间货币「额度」：每个源自己声明「多少原始单位 = 1 额度」。

```
某源当天额度 = floor(该源当天原始量 / 该源 per)
当天总额度   = 各启用源当天额度之和
```

发奖条件：`当天总额度 >= perAward` 且 `当天尚未发奖` → 掉 1 只。

例（`perAward = 2`）：

| 源 | 原始量 | per | 当天额度 |
|---|---|---|---|
| 微信读书 | 900 秒 | 900 | 1 |
| 番茄钟 | 50 分钟 | 25 | 2 |
| **合计** | | | **3 ≥ 2 → 掉 1 只** |

零头不计（`floor`）：番茄钟 49 分钟 = 1 个额度，不是 2 个。

## 4. 架构

```
 ┌──────────────────── 行为源（客户端 / 服务端拉取）────────────────────┐
 │                                                                        │
 │  微信读书官方 API        KOReader 插件        备考 App                │
 │  (mode: pull)          (mode: push)        (mode: push)             │
 │  服务端定时拉             客户端投递           客户端投递              │
 └───────────────┬────────────────────────────────────┬───────────────────┘
                 │ pull: cron 直接取数                 │ push: Issue 投递箱
                 │                                    │ (GitHub New Issue 预填)
                 │                    ┌───────────────▼───────────────────┐
                 │                    │  scripts/collect_reports.py        │
                 │                    │  · 只接受 repo 管理员创建的 issue    │
                 │                    │  · 解析 → 合并进 ledger（取 max）    │
                 │                    └───────────────┬───────────────────┘
                 │                                    │
                 └──────────────┬─────────────────────┘
                                ▼
              ┌───────────────────────────────────────┐
              │  结算器（唯一权威 · 唯一抽卡权）         │
              │  scripts/dex_core.py + sources.py     │
              │  · 额度折算 → 达标判定 → 抽卡          │
              │  · 多日补发（升序，每发一张重算 streak）  │
              │  · 进化链 care / stage / 保底闪光        │
              └───────────────┬───────────────────────┘
                              │ commit
                              ▼
        ┌──────────────────────────────────────────────┐
        │  data/dex.json（公开 repo）                    │
        │  collected + streak + today + ledger + chains │
        └───────────────┬───────────────────┬──────────┘
                        ▼                   ▼
              site/app.js 图鉴页        plugin/ 油猴脚本
              （只读）                   （只读）
```

**唯一权威这条不能破。** 之前踩过的坑很明确：两端都抽就会重复发奖 + 状态分叉。
所以客户端**只报量**，抽卡权（收哪一只、是不是闪光）永远在服务端。

## 5. 数据模型

### 5.1 `data/sources.json` —— 源注册表

```json
{
  "version": 1,
  "perAward": 2,
  "maxPerDay": 1,
  "maxBackfill": 3,
  "ledgerDays": 30,
  "sources": {
    "weread": {
      "name": "微信读书", "icon": "📖", "unit": "秒",   "per": 900,
      "mode": "pull", "enabled": true, "maxPerDay": 86400
    },
    "pom": {
      "name": "番茄钟",   "icon": "🍅", "unit": "分钟", "per": 25,
      "mode": "push", "enabled": true, "maxPerDay": 1440
    },
    "words": {
      "name": "背单词",   "icon": "🔤", "unit": "个",   "per": 20,
      "mode": "push", "enabled": false, "maxPerDay": 500
    }
  }
}
```

| 字段 | 含义 |
|---|---|
| `per` | 多少原始单位 = 1 额度 |
| `mode` | `pull`（服务端主动拉）/ `push`（客户端投递） |
| `enabled` | 是否参与结算。`words` 先占位，S4 定了工具再开 |
| `perAward` | 攒够几个额度掉 1 只 |
| `maxPerDay`（根级） | 每天最多掉几只（当前恒为 1） |
| `maxBackfill`（根级） | 一次结算最多补发几天（当前 3） |
| `ledgerDays` | 账本保留天数（滚动裁剪） |
| `maxPerDay`（源级） | **该源单日的物理上限**，用于校验投递数据（如番茄钟 ≤ 1440 分钟） |

### 5.2 `data/dex.json` v2

```json
{
  "version": 2,
  "collected": [
    {
      "id": "0563", "nameZh": "迭失棺", "shiny": false,
      "at": "2026-11-14T14:43:44.097585+08:00", "date": "2026-11-14",
      "sources": ["weread"], "form": "0563"
    },
    {
      "id": "1012", "nameZh": "斯魔茶", "shiny": false,
      "at": "2026-11-15T15:50:16.858315+08:00", "date": "2026-11-15",
      "sources": ["weread"], "form": "1012"
    },
    {
      "id": "0025", "nameZh": "皮卡丘", "shiny": false,
      "at": "2026-11-16T10:45:34.244553+08:00", "date": "2026-11-16",
      "sources": ["weread", "pom"], "form": "0025"
    }
  ],
  "streak": 3,
  "lastAwardDate": "2026-11-16",
  "today": {
    "date": "2026-11-16",
    "seconds": 1064,
    "threshold": 1800,
    "sources": { "weread": 1064, "pom": 50 },
    "credit": 3,
    "perAward": 2
  },
  "ledger": {
    "2026-11-14": { "weread": 900 },
    "2026-11-15": { "weread": 1800 },
    "2026-11-16": { "weread": 1064, "pom": 50 }
  },
  "chains": {
    "妙蛙种子→妙蛙草→妙蛙花": { "care": 5, "stage": 0, "guaranteed": false }
  }
}
```

上例自洽：11-14/15/16 连续三天发奖 → `streak: 3`；
11-16 当天 `floor(1064/900)=1` + `floor(50/25)=2` = 3 额度 ≥ `perAward: 2` → 掉 1 只；
那一只的 `sources` 记 `["weread","pom"]`（当天两个源都出了额度）。

`collected[].sources` = **发奖那天所有额度 > 0 的源**，按源注册表里的顺序排列。
不是「只有把额度推过阈值的那一个」—— 这一栏要回答的是「这一天是谁供的力」，所以全都记上。

**向后兼容（关键约束）**
`site/app.js` 与 `plugin/weread-dex.user.js` 现在读的是
`collected[].id / .nameZh / .shiny / .at`、`streak`、`today.seconds`、`today.threshold`。
这些字段**原样保留**，`today.seconds` 继续是微信读书口径的秒数。

**`today.threshold` = `perAward × weread.per`（当前 1800）**，不是 900。
读方拿它当「掉一只宝可梦要读多久」用 —— 进度条和文案都靠它。
发 900 会让进度条在 15 分钟就满、文案说「读满 15 分钟」，而实际要 30 分钟（`perAward = 2`）。
发 1800 才与真实门槛一致。**现有两个读方一行都不用改**，多源信息全是**新增**字段。

### 5.3 v1 → v2 迁移

| v1 | v2 | 规则 |
|---|---|---|
| `version: 1` | `version: 2` | 直接改写 |
| `collected[].sources` 缺失 | `["weread"]` | v1 只有微信读书一个源 |
| `collected[].form` 缺失 | 等于 `id` | 未进化即本级形态 |
| `ledger` 缺失 | `{}` | **从迁移日起开始记** |
| `today.sources` 缺失 | `{"weread": today.seconds}` | 同上 |
| `today.credit` 缺失 | 按 `sources.json` 算 | |
| `chains` 缺失 | `{}` | |

**历史量补不回来**：微信读书 `/readdata/detail` 传历史 `baseTime` 查不到旧周期（上游设计 §4.1 已实测），
所以 ledger 只能从迁移之日起累积。这是数据源的硬限制，不是实现取舍。

### 5.4 `collected[].form` —— 进化形态

进化**不改变** `collected[].id`（否则「已收服 N/1028」会倒退）。
`form` 记录该只当前所处的形态 id，图鉴页用 `form` 取图。
`form` 对应的宝可梦本身不必被单独收服。

## 6. 结算算法

### 6.1 额度折算

```python
def credit_for_day(sources_cfg, ledger_day) -> dict[str, int]:
    out = {}
    for sid, cfg in sources_cfg.items():
        if not cfg.get("enabled"):
            continue
        per = int(cfg["per"])
        out[sid] = int(ledger_day.get(sid, 0) or 0) // per
    return out

def day_credit(sources_cfg, ledger_day) -> int:
    return sum(credit_for_day(sources_cfg, ledger_day).values())
```

### 6.2 一次结清所有待发奖日

现状：`award.py` 每 3 小时只发**最早**那天，把补发摊到了好几轮 cron 里。
新版：一次跑完所有待发奖日，**按日期升序**，最多补 `maxBackfill`（默认 3）天。

```python
def pending_days(sources_cfg, ledger, awarded_dates, per_award) -> list[str]:
    """所有「额度够了但还没发奖」的日期，按时间正序。"""
    out = []
    for day in sorted(ledger):
        if day in awarded_dates:
            continue
        if day_credit(sources_cfg, ledger[day]) >= per_award:
            out.append(day)
    return out
```

### 6.3 streak 与闪光概率

`streak` = **连续发过奖的天数**。

两处锚点，缺一不可：
- **闪光概率**按**发奖那一天**算 —— 多日补发时按日期升序逐张发，
  每发一张后用 `compute_streak(awarded_dates | {day}, day)` 重算，
  保证那张牌的闪光概率用的是那一天的真实连读天数。
- **状态里的 `streak` 字段**锚在**最后一次发奖日**（不是今天）。
  否则补发几天前的奖励时，锚在今天会把补发日算成断签，显示成「连读 0 天」——
  用户明明连着达标三天。

闪光概率阶梯沿用现行表（不改）：

| 连读 | 1–2 天 | ≥3 天 | ≥7 天 | ≥30 天 | ≥100 天 |
|---|---|---|---|---|---|
| 概率 | 1/96 | 1/48 | 1/32 | 1/16 | **保底必出** |

### 6.4 进化链（S6）

链的定义来自 `data/pokedex.json` 的 `evolution` 字段（1028 只全有，格式是链上名字的数组）。
**归一化**：`dedup` 保留顺序 → 用 `→` 连接成 `chainKey`；
某只的 `stage` = 它的 `nameZh` 在归一化链里的下标；`finalStage` = 链长 - 1。

例：`["妙蛙种子","妙蛙草","妙蛙花"]` → `妙蛙种子→妙蛙草→妙蛙花`，
妙蛙种子 stage 0、妙蛙草 stage 1、妙蛙花 stage 2（最终形态）。
皮卡丘那条原始数据是 `["皮丘","皮卡丘","雷丘","皮丘","皮卡丘","雷丘"]`，去重后为 `皮丘→皮卡丘→雷丘`。

**养成规则**

| 规则 | 值 |
|---|---|
| 触发 | 每次发奖日，给**当天抽到的那只所属的链** `care += 1` |
| 进化 | `care >= (stage + 1) * 3` 且未达最终形态 → `stage += 1`，该链所有已收服条目的 `form` 更新为新 stage 形态 |
| 保底闪光 | `care >= 30` → 该链置 `guaranteed = true`；**该链下一次发奖必定闪光**（用掉后置回 false） |
| 普通闪光 | 仍由 streak 阶梯决定，与养成互不影响 |

进化链把「一次性运气」换成「持续投入」：光靠抽很难出闪光，但养一条链 30 天一定能拿到一次。

### 6.5 账本裁剪

`ledgerDays = 30`：只保留最近 30 天的账本条目，更早的删除。
`collected[].date` 不受影响（streak 与历史发奖记录靠它）。

## 7. 上报协议：GitHub Issue 投递箱

GitHub Pages 是纯静态站，只能读不能收 POST，所以 push 源需要一条「客户端能匿名投递、服务端能读回」的通道。

**选定方案：GitHub Issue。** 仓库 `lilia-shound/pokemon-dex` 是 public、issues 已开，
cron 里本来就有 `GITHUB_TOKEN`，零注册、零基建、零费用。

### 7.1 客户端

番茄钟把每天的累计分钟数存 localStorage，点「同步」时打开 GitHub 预填好的 New Issue 页：

```
https://github.com/lilia-shound/pokemon-dex/issues/new?title=<urlencoded>&body=<urlencoded>
```

- `title`：`[report] pom 2026-11-18`
- `body`：一行 JSON

```json
{"v":1,"source":"pom","days":{"2026-11-17":50,"2026-11-18":25}}
```

语义：`days[day]` = 该源**当天累计总量**（不是增量）。
用户点 Submit 提交，页面随即回到番茄钟。

### 7.2 服务端 `scripts/collect_reports.py`

GitHub Actions 里用 `${{ github.token }}`（workflow 已有 `permissions: contents: write`）读 issue、关 issue。

1. 拉 open issues，`title` 以 `[report]` 开头才看
2. **只接受 `author_association` ∈ {OWNER, MEMBER, COLLABORATOR} 的 issue**
3. 解析 body JSON，校验：
   - `source` 在注册表里且 `mode == "push"` 且 `enabled`
   - `days` 的 key 是合法 `YYYY-MM-DD`（且不晚于今天），value 是非负整数
   - **单源单日的值 ≤ 该源的 `maxPerDay`**（番茄钟 ≤ 1440，微信读书 ≤ 86400 秒）
   - 单次投递最多 30 天（与 `ledgerDays` 对齐）
4. 合并进 ledger：**同一源同一天取 max**（重复投递幂等）
5. 结算完成后在 issue 下留一条评论（写明采纳了哪几天的量、算出了多少额度），然后关闭该 issue

**任何一条校验不过**：丢弃整个 issue、打印日志、**不关闭它**（留给人工看），但其余 issue 照常处理。

### 7.3 信任边界（明说）

公开仓库里**任何人都能创建 issue**，所以这条通道天然是「任何人可写」的。
三道防线：

1. `author_association` 白名单 —— 只有 repo 管理员的 issue 被采纳
2. 数值上限 —— 单源单日的量不可能超过一天的总时长
3. 取 max —— 重复投递不叠加

残余风险：repo 管理员本人（或有管理权限的账号）可以伪造数据，让图鉴多掉几只宝可梦。
**这属于自伤范围，不构成隐私泄露**（dex.json 本来就是公开的），可接受。

## 8. 六个子项目

| | 子项目 | 内容 | 依赖 |
|---|---|---|---|
| **S1** | **中枢内核** | `sources.json` + `dex.json` v2 + 额度折算 + 多日补发 + 链级养成 + 迁移 + 单元测试 | — |
| S2 | 网页版番茄钟 | 探头 / 摸鱼监督 / CDN 取图 / localStorage 攒量 / Issue 投递 | S1 |
| S3 | KOReader 插件 | 第二个阅读入口，分钟数并入 `weread` 源 | S1 |
| S4 | 备考源 | 第三个行为源（工具待定） | S1 |
| S5 | 图鉴页完整版 | 多源额度进度 + 各源贡献 + 进化形态展示 | S1 |
| S6 | 进化链 | 链级 `care` / `stage` / 保底闪光 | S1 |

**第一刀只做 S1。** 后续每个子项目各自一份 spec → plan → 实现。

### 8.1 S1 内核的文件划分

`dex_core.py` 现在混了阈值、streak、抽卡、状态机。拆成三层，各自职责单一：

| 文件 | 职责 | 依赖 |
|---|---|---|
| `scripts/sources.py` | 读 `sources.json`、额度折算、源校验 | 无 |
| `scripts/ledger.py` | ledger 合并 / 裁剪 / 迁移 | `sources.py` |
| `scripts/dex_core.py` | 抽卡、streak、状态机（保持纯逻辑） | 无 |
| `scripts/award.py` | 编排：取数 → 折算 → 判定 → 抽 → 写盘 → 推送 | 全部 |

`scripts/sources.py` / `scripts/ledger.py` / `scripts/dex_core.py` 里，**除了 `sources.load_config` 读注册表这一处，其余一律不碰网络、不碰文件**（便于单测）。状态文件的读写在 `scripts/award.py`。

### 8.2 S2 网页版番茄钟

- **随机时长**：沿用 pokepomo 的默认 25 分钟 + 5 分钟休息（可调）
- **探头次数**：沿用 pokepomo 的公式 —— ≤15 分钟 1~2 次、≤30 分钟 2~3 次、≤45 分钟 3~4 次、更长 3~6 次；
  探头时刻落在专注时长的 7%~93% 之间，相邻探头间隔 ≥ `时长/(n+2)`
- **优先派没收集过的**（服务端下发的已收集清单）
- **摸鱼监督**：1/96 出现监督宝可梦；切走页面记一次摸鱼，一轮 3 次作废整轮（沿用 pokepomo 规则）
- **图走 CDN**：从 `site/img/<id>.webp` 拉（平均 10.6 KB/只）；拉不到退回剪影，不阻塞计时
- **已收集清单**：从 `site/dex.json` 拉（公开文件，图鉴页已经在用同一份），用来决定「优先派没收集过的」
- **可安装（PWA）**：带 manifest + Service Worker，可装到手机桌面。**SW 只缓存应用外壳**（HTML/CSS/JS/manifest），**不缓存宝可梦图片**——图片走 CDN 自带的 HTTP 缓存，避免把 10 MB 图片塞进 SW 缓存
- **离线**：localStorage 攒量；「同步」按钮生成 Issue 链接

**为什么不内嵌全部图**：1028 张 PNG base64 ≈ 49 MB，双击打开会卡死。
图鉴页 CDN 上已有全部 webp，联网时直接拉、离线时用剪影，是唯一可行解。

### 8.3 S3 KOReader 插件

Lua 插件，读 KOReader 自己的阅读时长，转换成分钟数 push 到 Issue 投递箱，
与微信读书的秒数**合并进同一个 `weread` 源**（同一份额度）。

**风险：沙箱里无法真机验证。** KOReader 的时长统计 API 只能靠文档推断，
需要单独特验方案（人工在设备上读一段 → 对 Issue 里的数字 → 调整）。

### 8.4 S4 备考源

工具待姜来指定（扇贝 / 百词斩 / 不背单词 / 自建……）。
`words` 源已占位，`per: 20 个词 = 1 额度`，`enabled: false`。
工具定下来后再写它的 spec —— 多数背单词 App 没有开放 API，
可能只能走「手动记数」或「网页端自建小工具」。

### 8.5 S5 图鉴页

- 顶部从「今日 X/30 分钟」改成**多源额度进度**：每个源一行，显示今日原始量 / per / 当天额度，总进度 = 合计 / perAward
- 图鉴墙：`collected[].form` 决定显示哪张图（进化后显示进化形态）
- 链级养成：显示每条链的 `care` / `stage` 进度条
- 保留现有的「读满 X 分钟就能遇上新宝可梦」文案，但阈值文案改成额度语义

### 8.6 S6 进化链

规则见 §6.4。实现放在 S1 的 `dex_core` 里（纯逻辑），图鉴页展示在 S5。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| `sources.json` 缺失或解析失败 | 结算中止，不发奖，不写盘（与现在一样，宁可不发不错发） |
| 某个源的 `per` ≤ 0 或缺失 | 该源跳过并在日志报警，其余源照常结算 |
| 未知 source id 出现在 Issue 里 | 丢弃该 issue，记录日志，**不**关闭 issue（留给人工看） |
| Issue body JSON 解析失败 | 同上 |
| 账本里有未来的日期 | 忽略 |
| 池子空了（1028 只集齐） | 不发奖，打印通关提示 |
| 微信读书取数失败 | 沿用现状：整轮跳过（push 源的账本照常结算，不受影响） |

## 10. 验收标准

### 数值断言（单元测试，`python3 run_tests.py`）

- 额度折算：`floor` 正确、零头不计、`enabled: false` 的源不参与
- 达标判定：总额度 = perAward 恰好达标；perAward - 1 不达标
- 一天最多 1 只：当天已发奖后再跑不再发
- 多日补发：升序、一次结清、最多 3 天、幂等（重跑不重复发）
- streak：补发时用当日的连读天数算闪光概率
- 迁移：v1 文件 → v2 的字段补全正确，旧 `collected` 条目 `sources = ["weread"]`
- 链级养成：`care` 累加、每满 3 天推进一级、满 30 天给且只给一次保底
- ledger 裁剪：只保留最近 30 天

### 观感断言（真实浏览器 + Playwright 截图）

- 图鉴页多源进度条正确渲染
- 番茄钟探头时机与次数符合公式
- 摸鱼 3 次作废整轮
- CDN 图加载失败时退回剪影且**不阻塞计时**

**数值断言全绿 ≠ 看起来对。** 两类断言都要有。

## 11. 明确不做（YAGNI）

- **不做** Service Worker 缓存宝可梦图片（只缓存应用外壳，图片走 CDN 的 HTTP 缓存）
- **不做** 多用户 / 排行榜 / 社交
- **不做** 宝可梦以外的收集物（观鸟图鉴、本地植物图鉴等另立项目）
- **不做** 进化形态的反向退化、跨链杂交等复杂养成
- **不动** `pokepomo`（离线单文件玩具，独立存在）
- **不动** 共读小屋
- **不动** Ink Atlas 站点

## 12. 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| Issue 通道被伪造 | 图鉴被多掉几只宝可梦 | `author_association` 白名单 + 数值上限 + 取 max（§7.3） |
| KOReader 时长统计 API 猜错 | S3 数字不准 | 单独特验方案，人工对照；不准就退回「手动记分钟」 |
| 备考 App 无开放 API | S4 做不成 | 兜底方案：自建一个小网页记数工具，同属 `words` 源 |
| 账本跨天清零被误当成 bug | 困惑 | 图鉴页与插件都明确写「额度按天计算」 |
| 49 MB 内嵌素材的诱惑 | 单文件卡死 | 明确走 CDN，不内嵌（§8.2） |
| `dex.json` 公开可被改 | 图鉴状态可被篡改 | 现状固有（v1 已是公开状态文件），本设计不扩大暴露面 |
