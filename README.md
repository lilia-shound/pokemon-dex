# 训练家图鉴 · 微信读书 × 宝可梦收集

每天在微信读书读满一定时长（在 KOReader 上读也一样），就随机收服一只宝可梦。
集齐 **1028 只**（含闪光）就是通关（九世代 1025 只 + 第 10 世代预告 3 只）。

- 图鉴页：<https://lilia-shound.github.io/pokemon-dex/>
- 网页版番茄钟：<https://lilia-shound.github.io/pokemon-dex/pomodoro/>
- 插件（油猴脚本）：`plugin/weread-dex.user.js`
- KOReader 插件：`plugin/koreader-dex.koplugin/`（Kindle / Android 上读的时长也能算数）

## 它怎么运转

```
微信读书官方 Agent API
  └─ /readdata/detail  mode=monthly + mode=weekly（两个周期取并集）
       →  readTimes[今天] = 当日阅读秒数（跨周/跨月补发时不漏掉周期最后一天）
            └─ 服务端判定（额度够不够）
                 └─ 从「还没收服」的池子里抽 1 只（闪光按连读天数加权）
                      └─ 写 data/dex.json
                           └─ 图鉴页 / 插件 读它 → 图鉴墙 + 进度胶囊 + 今日开箱卡

网页版番茄钟
  └─ 本机 localStorage 攒「当天专注分钟数」
       └─ 点「同步」→ 打开 GitHub 预填好的 New Issue 页（人点 Submit）
            └─ 服务端 collect_reports.py 读 issue → 校验 → 并进逐日账本
                 └─ 和微信读书的秒数折算成同一份额度

KOReader（Kindle / Android）
  └─ 插件读内置 statistics 的库 → 屏幕上弹二维码（落地页短链）
       └─ 手机扫码 → 落地页显示分钟数与额度 → 打开 GitHub 预填好的 New Issue 页（人点 Submit）
            └─ 服务端 collect_reports.py 读 issue → 校验 → 并进逐日账本
                 └─ 和微信读书的秒数折算成同一份额度
```

**服务端是唯一权威**：只有服务端抽出的宝可梦才进图鉴。
插件只读、只展示，不抽卡、不写状态、也不持有微信读书 API Key。

## 谁在跑结算

**真正的执行者是 Mavis 的 cron 任务**，不是 GitHub Actions。

- `WEREAD_API_KEY` 配在 **Mavis cron 的环境里**，**不在** Actions secrets
  （本仓库的 Actions secrets 实测是 0 个）。
- `.github/workflows/daily-award.yml` 只是**备用**执行器：只有你把 `WEREAD_API_KEY`
  加进 Actions secrets 后它才会跑。**启用前必须先停掉 Mavis cron** —— 两个写者会打架；
  而且这个 workflow 用 `git subtree push` 发布 gh-pages，与 `scripts/deploy.sh` 的
  orphan + force 重建方式冲突，大概率在第一次发布就挂。
- **取数失败 ≠ 今天没读够**：monthly 与 weekly 两个周期都取数失败时，
  `scripts/award.py` 会 **exit 1** 并打印「[失败] 取数失败…」，不会安静地当成「未达标」。
  cron 日志里看到 `[失败]` 就是取数出了问题（Key 失效 / 微信读书 skill 版本过期 / 网关抖动），
  该修的是取数，不是「今天没读够」。

## 玩法规则

| 项 | 值 |
|---|---|
| 达标线 | 每日 **2 个额度**。微信读书 900 秒 = 1 额度、番茄钟 25 分钟 = 1 额度、KOReader 15 分钟 = 1 额度，凑满 2 个额度掉 1 只。只靠阅读的话就是 1800 秒官方口径（约 40~43 分钟体感）；「读 15 分钟 + 一轮番茄钟」也够 |
| 每次达标 | 1 只（**一天最多 1 只**，额度跨天清零） |
| 抽池 | 1028 只里**还没收服**的 |
| 闪光概率 | 见下表 |
| 未达标 | 没有宝可梦，也**没有惩罚**（只是连读断了） |
| 连读断了 | streak 归零 |

### 多源额度模型

图鉴不只认微信读书。每个「源」自己声明「多少原始单位 = 1 额度」，存在 `data/sources.json`：

| 源 | 单位 | 1 额度 = | 怎么来的 |
|---|---|---|---|
| 微信读书 | 秒 | 900 秒 | 服务端定时拉官方 API |
| 番茄钟 | 分钟 | 25 分钟 | 客户端投递（GitHub Issue 投递箱） |
| KOReader | 分钟 | 15 分钟 | 客户端投递（插件 → 二维码 → 手机落地页 → GitHub Issue 投递箱） |
| 背单词 | 个 | 20 个 | 客户端投递 |

规则：

- 某源当天额度 = `floor(该源当天原始量 / 该源 per)`，零头不计
- 当天总额度 = 各启用源额度之和（`enabled: false` 的源不参与结算，当前启用微信读书、番茄钟与 KOReader）——
  番茄钟的投递通道**已经接上**（网页版番茄钟 → GitHub Issue 投递箱 → `scripts/collect_reports.py`）；
  KOReader 那条也接上了（插件 → 二维码 → 手机落地页 → 同一个投递箱）；
  背单词还是占位，`enabled: false`
- 总额度 ≥ `perAward`（**当前 2**）且当天还没发过奖 → 掉 1 只
- **一天最多 1 只**，额度跨天清零
- 每个源的原始量先夹到该源的 `maxPerDay`（番茄钟 ≤ 1440 分钟）—— 客户端报什么都不能超

> **微信读书和 KOReader 是两个独立的源**，不是同一个东西的两份统计。
> 各自按天取整算出自己的额度，再**相加**进同一份额度 ——
> 「微信读书读 15 分钟 + KOReader 读 15 分钟」也是当天 2 个额度，够掉 1 只。
> 分成两个源是刻意的：账本对「同源同一天」是**取 max**（重复投递要幂等），
> 合到一个源里的话，微信读书 1800 秒与 KOReader 600 分钟取 max = 1800，**KOReader 那份会被吃掉**。

> **`perAward` 从 1 改到 2 是 S2 番茄钟上线后的裁决**（2026-11-18）：
> 「读 15 分钟 + 一轮番茄钟」= 2 个额度，够掉 1 只，多源玩法才有意义。
> 换档时只动 `data/sources.json` 的 `perAward` 这一个字段，代码不用改 ——
> 想回到「读满就掉」的老体验，把它改成 1 即可。

⚠️ **图鉴页顶部那个「今日 X/30 分」只算微信读书的秒数**，番茄钟、KOReader 贡献的额度不显示在里面
（多源额度进度条是 S5 的活）。所以「读 20 分钟 + 一轮番茄钟」已经够 2 个额度了，
但那一栏还是显示 20/30 分 —— 别以为它坏了。

额度不是新增的负担，是把「读多少」和「掉多少」解耦：番茄钟满 25 分钟也是 1 个额度，
和微信读书的 15 分钟等价 —— 谁先凑够谁说了算。

### 闪光概率（连读天数 → 概率）

| 连读 | 1–2 天 | ≥3 天 | ≥7 天 | ≥30 天 | ≥100 天 |
|---|---|---|---|---|---|
| 概率 | 1/96 | 1/48 | 1/32 | 1/16 | **保底必出** |

## 网页版番茄钟

<https://lilia-shound.github.io/pokemon-dex/pomodoro/>

手机上打开就能用（可「添加到主屏幕」当 App 装）。**专注时宝可梦会探头，点一下就收下**，
它不打断计时；出现的那只有 1/96 的概率是「监督宝可梦」，它盯着你，中途切走页面它就记一次摸鱼，
一轮摸鱼 3 次这一轮作废（不计入统计）。

专注分钟数只存在**本机浏览器**里。要让它变成图鉴里的宝可梦，得点「同步到图鉴」——
它会打开 GitHub 预填好的新建 issue 页面，**在那个页面上点一下 Submit** 才算交上去。

交上去之后服务端 `scripts/collect_reports.py` 会：

1. 只认 `author_association ∈ {OWNER, MEMBER, COLLABORATOR}` 的 issue（别人伪造不了）
2. 校验源、日期、数值（单源单日 ≤ 该源的 `maxPerDay`），**任何一条不过就丢弃整个 issue**
3. 并进逐日账本（**同源同一天取 max** ⇒ 重复投递幂等，放心重交）
4. 回一条评论算给你看，然后关掉这个 issue

> 报告里的 `days[day]` 是**当天累计总量**（不是增量）—— 所以重复同步不会把数字吃掉。

## KOReader 源

<https://lilia-shound.github.io/pokemon-dex/koreader/>

在 Kindle（或 Android 版 KOReader）上读的书时长，也算进图鉴：

```
KOReader 插件读内置 statistics 的库 → 屏幕上弹一个二维码（落地页短链）
  └─ 手机扫码 → 落地页显示「哪天读了多久」与折算出的额度
       └─ 点「去 GitHub 提交」→ 打开预填好的 New Issue 页（人点 Submit）
            └─ 服务端 collect_reports.py 读 issue → 校验 → 并进逐日账本
```

- **怎么装、怎么报一次、怎么对账**：[`docs/koreader-setup.md`](docs/koreader-setup.md)
  （安装就是把 `koreader-dex.koplugin/` 整个目录复制到设备的 `plugins/` 目录，然后重启 KOReader）
- **插件自己的说明书**：[`plugin/koreader-dex.koplugin/README.md`](plugin/koreader-dex.koplugin/README.md)

额度口径：**15 分钟 = 1 个额度**（和微信读书的 900 秒同价）。
KOReader 读满 **30 分钟（得是同一天）** = 2 个额度 = 掉 1 只宝可梦。

> **微信读书和 KOReader 是两个独立的源** —— 不是同一个东西的两份统计，
> 各自按天取整算出额度，然后**相加**进同一份额度。
> 「微信读书读 15 分钟 + KOReader 读 15 分钟」= 当天 2 个额度，照样掉 1 只。
> 分成两个源是刻意的（账本对同源同一天取 max，合到一起会把 KOReader 那份吃掉）。

⚠️ KOReader 的内置 statistics 是**攒着写库**的（每 50 次翻页 / 关掉书 / 息屏才落库）——
刚读完的几页可能还没进库，**关掉书或息屏后再汇报**。插件每次都会提醒这一句。

## 安装插件

1. 浏览器装 [Tampermonkey](https://www.tampermonkey.net/)
2. 打开 <https://lilia-shound.github.io/pokemon-dex/weread-dex.user.js>，点「安装」
3. 打开微信读书网页版，右上角出现胶囊：`3 图鉴 ▓▓▓░░ 8/15`
4. 点胶囊 → 图鉴墙；当天达标后会有开箱卡

脚本带 `@updateURL` / `@downloadURL`，**以后 Tampermonkey 会自己检查并更新**，
不用每次手动换文件（首次安装仍需要按上面第 2 步装一次）。

手机上也能用（Tampermonkey 有 App），但插件只是**展示端**，
真正的判定永远在服务端，手机上读的时长同样算数。

## 目录

```
data/pokedex.json        1028 只中文资料（编号/名字/属性/图鉴文案/进化链/立绘直链）
data/dex.json            唯一状态文件：已收集列表 + 连读天数 + 今日额度 + 逐日账本
data/sources.json        源注册表：每个源的单位 / per / mode
scripts/weread_client.py 微信读书网关封装
scripts/dex_core.py      抽卡与状态机（纯逻辑，可测）
scripts/sources.py       配置校验 + 额度折算
scripts/ledger.py        逐日账本：合并 / 裁剪 / 迁移
scripts/fetch_pokemon.py 素材管线：拉 42arch 数据 → pokedex.json
scripts/award.py         发放器：取数 → 判定 → 抽 → 写状态 → 推送
scripts/check_today.py   自检：把今日状态摊开打印
scripts/collect_reports.py Issue 投递箱接收端：读 issue → 校验 → 并账本
scripts/build_pokedex_lite.py 生成番茄钟用的精简宝可梦清单（1.8MB → 104KB）
scripts/push_notify.py   微信推送（pushplus）
scripts/deploy.sh        推 main + gh-pages，发布图鉴页
site/                    图鉴页静态站
site/pomodoro/           网页版番茄钟（PWA）
site/koreader/           KOReader 手机落地页（Kindle 短链扫开的那一页）
plugin/                  油猴脚本
plugin/koreader-dex.koplugin/  KOReader 插件（读内置 statistics → 弹二维码 → 自检）
docs/koreader-setup.md   KOReader 装到用起来的手册 + 四步对账方案
tests/                   单元测试
```

## 开发

```bash
python3 run_tests.py            # 跑全部单元测试（零依赖，标准库 unittest）
python3 scripts/check_today.py  # 看今天的阅读时长与额度
python3 scripts/award.py --dry-run
python3 scripts/fetch_pokemon.py  # 重建 pokedex.json
python3 scripts/build_pokedex_lite.py  # 重建番茄钟的精简清单
python3 scripts/collect_reports.py --dry-run  # 看 issue 投递箱里有什么
bash scripts/deploy.sh            # 发布
```

### 环境变量

| 变量 | 必需 | 用途 |
|---|---|---|
| `WEREAD_API_KEY` | ✅ | 微信读书 Agent API Key（`wrk-` 开头）。App → 设置 → 微信读书 Skill 里复制。**配在 Mavis cron 的环境里，不在 Actions secrets**（见「谁在跑结算」） |
| `GITHUB_TOKEN` | 部署时 | 推 repo |
| `PUSHPLUS_TOKEN` | 可选 | 微信推送。未配置则静默跳过 |

## 数据来源与授权

- 宝可梦资料：[42arch/pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh)（MIT，抓取自神奇宝贝百科）
- 立绘：`42arch` 的 Pokémon HOME 造型，少数无 HOME 造型的 12 只用 [PokeAPI](https://pokeapi.co/) 官方立绘兜底
- **宝可梦名称与形象版权归 Nintendo / Creatures Inc. / GAME FREAK inc.，仅供个人自用**
- 阅读数据来自微信读书官方 Agent API，本项目不修改、不转发微信读书的任何内容

`data/dex.json` 是公开的，但里面**只有**图鉴收集状态、逐日阅读秒数和额度结算结果——
没有书架、书名、笔记、划线，也没有 API Key。

## 已知的坑

- 微信读书 `/readdata/detail` **传历史 `baseTime` 查不到旧周期**（官方文档说可以，实际不行），判定只能用当前周期
- 微信读书的时长是**延迟上报**的（实测滞后 3~10 分钟，且之后持续回补），所以判定要扫当前周期内所有「达标但还没发奖」的日期做**补发**，不能只看今天
- 官方计数**漏计约三成**（实测真实读 25 分钟只记 17 分钟），达标线按官方口径算，体感成本约乘 1.4（900 秒官方 ≈ 20~21 分钟体感）
- 所有时长字段单位是**秒**
- 桶 key 用北京时间周期起点（已用月桶/年桶反推验证）
- KOReader 的内置 statistics **攒着写库**（每 50 次翻页 / 关掉书 / 息屏才落库）⇒
  刚读完的几页还没进库就汇报 ⇒ **数字偏小**（插件每次都会提醒「关掉书或息屏后再汇报」）
- KOReader 那边**分钟是整分钟、丢掉秒数**（内置统计显示 时:分:秒，插件与落地页显示整分钟、向下取整），
  对账时差 1 分钟以内属正常；页数必须一模一样
- KOReader 的统计**按设备本地时区分天**（SQL 里 `strftime(..., 'localtime')`）⇒
  设备时区不是 UTC+8 时，跨过零点的那几页会归到不同的一天
