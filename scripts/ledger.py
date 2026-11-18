"""账本：每天各源贡献了多少原始量。

账本是「过程」，图鉴（collected）是「结果」。
合并规则是取 max 而不是求和 —— 客户端上报的语义是「该源当天累计总量」，
所以重复投递同一天必须幂等。

本模块只处理 dict，不碰网络也不碰文件。
"""

from __future__ import annotations

import datetime


def empty_ledger() -> dict:
    """空账本 —— 键是日期，值是 {源 id: 原始量}。"""
    return {}


def merge_amounts(ledger: dict, source_id: str, days: dict[str, int]) -> dict:
    """把某个源某几天的量并进账本。同源同一天取 max。"""
    out = {day: dict(amounts) for day, amounts in (ledger or {}).items()}
    for day, amount in (days or {}).items():
        if amount is None:
            continue
        amount = int(amount)
        if amount < 0:
            continue
        row = out.setdefault(day, {})
        row[source_id] = max(int(row.get(source_id, 0)), amount)
    return out


def prune(ledger: dict, keep_days: int, today: str) -> dict:
    """只保留最近 keep_days 天（含 today），未来日期直接丢掉。"""
    today_d = datetime.date.fromisoformat(today)
    keep = {(today_d - datetime.timedelta(days=i)).isoformat() for i in range(keep_days)}
    return {day: dict(amounts) for day, amounts in (ledger or {}).items() if day in keep}


def migrate_state(state: dict) -> dict:
    """v1 → v2：补 sources / form / date，version 改成 2，初始化 ledger 与 chains。

    对已经是 v2 的文件是幂等的：已有字段一律保留，缺的才补。
    """
    st = dict(state or {})
    collected = []
    for c in st.get("collected") or []:
        c = dict(c)
        c.setdefault("sources", ["weread"])
        c.setdefault("form", c.get("id"))
        collected.append(c)
    for c in collected:
        # 幂等护栏（pending_days 排除已发奖日）完全依赖 collected[].date。
        # 缺 date 的条目会让那天重新进 pending → 同一天两张卡。
        # 实测 v1 的真实数据每条都有 date，这里只是给手改过的状态兜底。
        if not c.get("date"):
            c["date"] = (c.get("at") or "")[:10] or None
    st["collected"] = collected
    st["version"] = 2
    # 两层拷贝：账本与链状态是共享状态，调用方就地改内层 dict 时不能污染入参
    st["ledger"] = {d: dict(v) for d, v in (st.get("ledger") or {}).items()}
    st["chains"] = {k: dict(v) for k, v in (st.get("chains") or {}).items()}
    st.setdefault("streak", 0)
    st.setdefault("lastAwardDate", None)
    return st
