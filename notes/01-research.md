# 微信读书 × 宝可梦收集 —— 可行性调研

> 2026-11-13 · 起因：小红书刷到「KOReader 宝可梦插件」帖子

## 一、原帖截图判读（存档，之后用文字代替原图）

来源：小红书用户「服从性测试通知群」，2026-11-13 22:19，标题「训练家就这样看书也不忘要收服🧊」

原帖自述：
- 自己写了个 **KOReader 插件**（KOR = KOReader，跑在墨水屏阅读器上）
- 玩法：**每天满足阅读时长 → 随机给一只宝可梦**，阅读时长阈值可自己定
- 收录了**九个世代**共 **1025** 种（图鉴 5/1025 · 完成 0.5%）
- 有 **1/96 闪光概率**
- 「督促多看书」

截图细节（墨水屏阅读器实拍）：
- 顶部状态栏：`图鉴 5/1025 · 完成 0.5%`、`翻块于 2026-10-23 (成都)`（天气）
- 正文是小说内容，右上角露出另一栏文字，底部 `15 / 8565`（疑为页码/字数进度）
- 插件把「宝可梦收集」渲染在阅读界面里，和正文共存

## 二、核心问题：微信读书上拿不到阅读时长怎么办？

结论：**拿得到，而且有官方接口**。

微信读书 2026 年开放了 Agent API Gateway：

```
POST https://i.weread.qq.com/api/agent/gateway
Authorization: Bearer wrk-xxxxxxxx
{"api_name": "/readdata/detail", "mode": "monthly", "skill_version": "1.0.x"}
```

API Key 获取路径（姜来本地操作）：
- 微信读书 App → 设置 → 微信读书 Skill → 复制 Key
- 或网页端 `https://weread.qq.com/r/weread-skills` 登录后复制
- 格式 `wrk-xxx`，绑定用户身份 vid

### `/readdata/detail` 返回的关键字段

| 字段 | 含义 | 对本项目的价值 |
|---|---|---|
| `readTimes` | 分桶时长，**weekly/monthly 按天分桶**，key=当天起始时间戳，value=秒 | ★ 直接拿「今天读了多少秒」 |
| `totalReadTime` | 周期总时长（秒） | 校验用 |
| `readDays` | 有效阅读天数，**服务端规则：单日满 1 分钟** | 佐证服务端有「当天达标」概念 |
| `dailyReadTimes` | 年度模式下的按天明细 | 备用 |
| `readLongest[]` | 读得最多的书 + 时长 | 可做「读完一本 → 特殊奖励」 |

**注意**：所有时长字段单位是**秒**，文档明确警告不要当分钟/小时用。

判定逻辑因此可以做到：调用 `mode=monthly` → 取 `readTimes` 里今天那个桶 → 秒数 ≥ 阈值 → 达标。

## 三、宝可梦中文数据源

| 源 | 覆盖 | 中文图鉴描述 | 图像 | 备注 |
|---|---|---|---|---|
| **42arch/pokemon-dataset-zh** | 1~9 世代 1025 只 | ✅ `pokedex_entries[].versions[].text`（各版本官方中文文案） | ✅ `official/` 1320 + `home/` 3006 + `dream/` 1231 | MIT，抓取自神奇宝贝百科 52poke。**中文完整，第九代也有** |
| PokeAPI `pokemon-species` | 1025 | ⚠️ `zh-hans` 仅约半数有，第九代为 0 | ✅ | 中文名、属性、种族值齐全 |

### 42arch 数据集结构

```
data/pokemon/0025-皮卡丘.json     # 编号 + 中文名
data/images/official/            # 官方立绘
data/images/home/                # Pokémon Home 造型
data/images/home/*-shiny.png     # ★ 闪光变体（1/96 机制所需）
data/simple_pokedex.json         # 总列表
```

单个宝可梦 JSON 字段：
`name_zh` / `name_ja` / `name_en` / `pokedex_id` / `description` / `profile`（外观描述）/
`prototype`（设计原型）/`detail`（冷知识）/`names`（各语言命名由来）/
`forms`（形态·属性·分类·特性·身高体重·捕获率）/`stats`（种族值）/`type_effectiveness`（相克）/
**`pokedex_entries`（按世代的官方中文图鉴描述）**/`evolution_chains`（进化链）/`home_images`（含 shiny 文件名）

→ 素材完整度足够复刻原帖插件，且**中文文案质量比 PokeAPI 高**。

### 沙箱网络现状（影响构建方式）
- ✅ 可达：`pokeapi.co`、`weread.qq.com`、`i.weread.qq.com`、`cdn.jsdelivr.net`
- ❌ 不可达：`raw.githubusercontent.com`（沙箱）；`google.com`
- ⚠️ `api.github.com` 需 `GITHUB_TOKEN`（已有）

→ 构建时图片走 jsdelivr 或 GitHub API 代理下载。

## 四、形态取舍

| | A. 姜去当训练家（服务端） | B. 浏览器插件（本地） |
|---|---|---|
| 安装成本 | 零（只给我一次 API Key） | 装油猴脚本/扩展 |
| 手机可用 | ✅ 可以 | ❌ 只有电脑网页版 |
| 数据存放 | 我这边（可进 EverMe KB） | localStorage（换浏览器丢） |
| 仪式感 | 我主动发奖，像共读小屋的对话感 | 自己做自己做 |
| 风险 | API Key 给我 = 我能读她的书架/笔记 | 无 |

**C（混合）**：服务端判定发奖 + 插件/网页展示 —— A 的自然延伸。

## 五、与共读小屋的关系

姜来判断：「共读小屋已经功能完善了，不搭载」—— **认同**。

共读小屋的核心是「划线 ↔ 批注」的双向对话，宝可梦收集是「阅读时长 → 随机奖励」的个人养成，
两者逻辑同源但机制独立，硬塞进共读小屋会破坏它的完成度。
→ 做成**独立项目**，共享「我读了多少」这一个数据源，不动共读小屋本体。

## 六、待姜来拍板

1. 主要在哪儿读微信读书（手机 App / 电脑网页版 / 都读）→ 决定形态
2. 形态选 A / B / C
3. 达标阈值、连续天数加成等机制细节
