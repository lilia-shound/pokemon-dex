# 训练家图鉴 · 微信读书 × 宝可梦收集

每天在微信读书读满一定时长，就随机收服一只宝可梦。
集齐 **1028 只**（含闪光）就是通关（九世代 1025 只 + 第 10 世代预告 3 只）。

- 图鉴页：<https://lilia-shound.github.io/pokemon-dex/>
- 插件（油猴脚本）：`plugin/weread-dex.user.js`

## 它怎么运转

```
微信读书官方 Agent API
  └─ /readdata/detail  mode=monthly  →  readTimes[今天] = 当日阅读秒数
       └─ 服务端判定（阈值 30 分钟）
            └─ 从「还没收服」的池子里抽 1 只（闪光按连读天数加权）
                 └─ 写 data/dex.json
                      └─ 图鉴页 / 插件 读它 → 图鉴墙 + 进度胶囊 + 今日开箱卡
```

**服务端是唯一权威**：只有服务端抽出的宝可梦才进图鉴。
插件只读、只展示，不抽卡、不写状态、也不持有微信读书 API Key。

## 玩法规则

| 项 | 值 |
|---|---|
| 达标线 | 每日 **30 分钟**（官方口径 1800 秒 = 2 个额度 × 900 秒）。官方计数漏掉约三成，实际体感约 40~43 分钟 |
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
| 背单词 | 个 | 20 个 | 客户端投递 |

规则：

- 某源当天额度 = `floor(该源当天原始量 / 该源 per)`，零头不计
- 当天总额度 = 各启用源额度之和（`enabled: false` 的源不参与结算，当前启用微信读书与番茄钟）
- 总额度 ≥ `perAward`（当前 2）且当天还没发过奖 → 掉 1 只
- **一天最多 1 只**，额度跨天清零

> `perAward = 2` 意味着微信读书单独要 1800 秒官方口径（约 40~43 分钟体感）才掉 1 只。
> 想恢复旧体验（15 分钟一只），把 `data/sources.json` 的 `perAward` 改成 1 即可，代码不用动。

额度不是新增的负担，是把「读多少」和「掉多少」解耦：番茄钟满 25 分钟也是 1 个额度，
和微信读书的 15 分钟等价 —— 谁先凑够谁说了算。

### 闪光概率（连读天数 → 概率）

| 连读 | 1–2 天 | ≥3 天 | ≥7 天 | ≥30 天 | ≥100 天 |
|---|---|---|---|---|---|
| 概率 | 1/96 | 1/48 | 1/32 | 1/16 | **保底必出** |

## 安装插件

1. 浏览器装 [Tampermonkey](https://www.tampermonkey.net/)
2. 打开 `plugin/weread-dex.user.js`，点「安装」
3. 打开微信读书网页版，右上角出现胶囊：`3 图鉴 ▓▓▓░░ 24/30`
4. 点胶囊 → 图鉴墙；当天达标后会有开箱卡

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
scripts/push_notify.py   微信推送（pushplus）
scripts/deploy.sh        推 main + gh-pages，发布图鉴页
site/                    图鉴页静态站
plugin/                  油猴脚本
tests/                   单元测试
```

## 开发

```bash
python3 run_tests.py            # 跑全部单元测试（零依赖，标准库 unittest）
python3 scripts/check_today.py  # 看今天的阅读时长与额度
python3 scripts/award.py --dry-run
python3 scripts/fetch_pokemon.py  # 重建 pokedex.json
bash scripts/deploy.sh            # 发布
```

### 环境变量

| 变量 | 必需 | 用途 |
|---|---|---|
| `WEREAD_API_KEY` | ✅ | 微信读书 Agent API Key（`wrk-` 开头）。App → 设置 → 微信读书 Skill 里复制 |
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
- 官方计数**漏计约三成**（实测真实读 25 分钟只记 17 分钟），达标线按官方口径算，体感成本约乘 1.4（1800 秒官方 ≈ 40~43 分钟体感）
- 所有时长字段单位是**秒**
- 桶 key 用北京时间周期起点（已用月桶/年桶反推验证）
