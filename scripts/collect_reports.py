#!/usr/bin/env python3
"""Issue 投递箱接收端：把客户端报上来的行为量并进账本。

    python3 scripts/collect_reports.py            # 读 issue → 校验 → 并账本
    python3 scripts/collect_reports.py --dry-run  # 只打印，不写盘不评论不关闭

**为什么是 Issue**：GitHub Pages 是纯静态站，只能读不能收 POST；
而仓库 `lilia-shound/pokemon-dex` 是 public、issues 已开，cron 里本来就有
`GITHUB_TOKEN` —— 零注册、零基建、零费用的一条「客户端能匿名投递、服务端能读回」
的通道。客户端只负责打开一个预填好的 New Issue 页面并让人点 Submit。

**信任边界**（公开仓库里任何人都能创建 issue，所以这条通道天然「任何人可写」）：

1. `author_association` 白名单 —— 只有仓库管理员的 issue 被采纳
2. 数值上限 —— 单源单日的量不超过该源的 `maxPerDay`（番茄钟 ≤ 1440 分钟）
3. 取 max —— 同一源同一天重复投递取最大值，不叠加

残余风险：仓库管理员本人可以伪造数据让图鉴多掉几只宝可梦。
这属于自伤范围（dex.json 本来就是公开的），可接受。

**语义**：`days[day]` = 该源**当天累计总量**（不是增量）。
服务端取 max，所以重复投递是幂等的 —— 客户端报总量才安全，
报增量的话第二次同步会被 max 吃掉。
"""

from __future__ import annotations

import datetime
import json

# issue 标题前缀：只有带它的 issue 才会被当成报告
REPORT_TITLE_PREFIX = "[report]"
# 报告格式版本
REPORT_VERSION = 1
# 可以被采纳的 issue 作者身份
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
# 单次投递最多带多少天（与注册表的 ledgerDays 对齐）
MAX_REPORT_DAYS = 30


def _is_int(v) -> bool:
    """是不是整数。**bool 不算** —— Python 里 True 是 int 的子类。"""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_date(s) -> bool:
    """是不是合法的 YYYY-MM-DD 日期字符串。

    `date.fromisoformat` 在 Python 3.11+ 接受「20261118」这种基本格式，
    所以额外比一次 `isoformat()` 的结果，保证是带横杠的十进制写法。
    """
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return d.isoformat() == s


def parse_report(body: str, cfg: dict, today: str) -> tuple[dict[str, int], str | None]:
    """解析一条报告的 body。

    成功返回 `(days, None)`，失败返回 `({}, 原因)`。
    **任何一条校验不过就丢弃整个报告**（不部分采纳）——
    一条坏数据不该只污染一半，另一半照常处理。

    校验清单：
      1. body 是 JSON 对象
      2. `v` == 1
      3. `source` 在注册表里、`mode == "push"`、`enabled` 为真
      4. `days` 是对象，条数 ≤ 30
      5. 每个 key 是合法 YYYY-MM-DD，且不晚于今天
      6. 每个 value 是非负整数
      7. 每个 value ≤ 该源的 `maxPerDay`
    """
    if not isinstance(body, str) or not body.strip():
        return {}, "报告内容为空"
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        return {}, f"body 不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return {}, "body 不是 JSON 对象"

    version = data.get("v")
    if version != REPORT_VERSION:
        return {}, f"版本不对：期望 v={REPORT_VERSION}，收到 {version!r}"

    source_id = data.get("source")
    if not isinstance(source_id, str) or not source_id:
        return {}, f"source 不是源 id：{source_id!r}"
    source = (cfg.get("sources") or {}).get(source_id)
    if source is None:
        return {}, f"注册表里没有这个源：{source_id}"
    if source.get("mode") != "push":
        return {}, f"源 {source_id} 不是 push 源，不能靠 issue 上报"
    if not source.get("enabled"):
        return {}, f"源 {source_id} 当前是停用的"

    days = data.get("days")
    if not isinstance(days, dict):
        return {}, f"days 不是对象：{days!r}"
    if not days:
        return {}, "报告里没有天数"
    if len(days) > MAX_REPORT_DAYS:
        return {}, f"一次最多带 {MAX_REPORT_DAYS} 天，收到 {len(days)} 天"

    cap = source.get("maxPerDay")
    out: dict[str, int] = {}
    for day, amount in days.items():
        if not _is_date(day):
            return {}, f"日期格式不对：{day!r}（要 YYYY-MM-DD）"
        if day > today:
            return {}, f"日期在未来：{day}"
        if not _is_int(amount):
            return {}, f"{day} 的量不是整数：{amount!r}"
        if amount < 0:
            return {}, f"{day} 的量是负数：{amount}"
        if cap is not None and amount > int(cap):
            return {}, f"{day} 的量 {amount} 超过 {source_id} 的上限 {cap}"
        out[day] = int(amount)
    return out, None
