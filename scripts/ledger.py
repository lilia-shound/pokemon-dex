"""账本：每天各源贡献了多少原始量。

账本是「过程」，图鉴（collected）是「结果」。
合并规则是取 max 而不是求和 —— 客户端上报的语义是「该源当天累计总量」，
所以重复投递同一天必须幂等。

本模块只处理 dict，不碰网络也不碰文件。
"""

from __future__ import annotations

import datetime


def empty_ledger() -> dict:
    return {}


def merge_amounts(ledger: dict, source_id: str, days: dict) -> dict:
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
    """v1 → v2：补 sources / form，version 改成 2，初始化 ledger 与 chains。

    对已经是 v2 的文件是幂等的：已有字段一律保留，缺的才补。
    """
    st = dict(state or {})
    collected = []
    for c in st.get("collected") or []:
        c = dict(c)
        c.setdefault("sources", ["weread"])
        c.setdefault("form", c.get("id"))
        collected.append(c)
    st["collected"] = collected
    st["version"] = 2
    st.setdefault("ledger", {})
    st.setdefault("chains", {})
    st.setdefault("streak", 0)
    st.setdefault("lastAwardDate", None)
    return st
