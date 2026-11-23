"""最近 30 天的历史（**只展示，不结算**）。

为什么要单独存一个文件：账本（`data/dex.json` 的 `ledger`）是**结算用**的 ——
`award.py` 的 `pending_days()` 会把「账本里有量、但还没发过奖」的日子当成**待补发日**。
所以往账本里回填历史 = 一次性补发十几只宝可梦。

⇒ 历史单独放 `data/history.json`，**永远不碰 `data/dex.json`**。
回填历史不影响任何发奖判定（有测试守着）。

微信读书的部分从**月桶**回填（`read_times("monthly")` 给的是本月每一天的秒数），
三个 push 源的部分从账本里抄。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

# 直接跑 `python3 scripts/history.py` 时仓库根不在 path 上 ⇒ 先补上，
# 否则 `from scripts import weread_client` 会 ModuleNotFoundError（README 里就是这么写的）
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TZ_SH = datetime.timezone(datetime.timedelta(hours=8))
HISTORY_PATH = "data/history.json"
DEFAULT_KEEP_DAYS = 30


def history_day(ts: int) -> str:
    """桶起始时间戳 → `YYYY-MM-DD`（北京时间）。

    桶 key 就是北京时间的当天零点（上游按天分桶，已用月桶/年桶反推验证过）。
    """
    return datetime.datetime.fromtimestamp(int(ts), tz=TZ_SH).strftime("%Y-%m-%d")


def merge_history(old: dict, days: dict) -> dict:
    """把新的一天并进历史。同源同一天取 max（跟账本一个规矩 ⇒ 幂等）。"""
    out = {day: dict(amounts) for day, amounts in (old or {}).items()}
    for day, amounts in (days or {}).items():
        for source, amount in (amounts or {}).items():
            amount = int(amount)
            if amount < 0:
                continue
            # ★ 先判负数再建行 —— 与 `scripts/ledger.py` 的 merge_amounts 逐字一致，
            #   不然一个全是负数的天会留下一个空行。
            row = out.setdefault(day, {})
            row[source] = max(int(row.get(source, 0)), amount)
    return out


def prune_history(history: dict, keep_days: int, today: str) -> dict:
    """只保留最近 keep_days 天（含今天）；未来的日子丢掉。"""
    today_d = datetime.date.fromisoformat(today)
    keep = {(today_d - datetime.timedelta(days=i)).isoformat()
            for i in range(int(keep_days))}
    return {day: dict(amounts)
            for day, amounts in (history or {}).items() if day in keep}


def build_history(dex: dict, buckets: dict, today: str,
                  keep_days: int = DEFAULT_KEEP_DAYS) -> dict:
    """组装历史：账本里的 push 源 + 月桶里的微信读书。"""
    days = {day: dict(amounts)
            for day, amounts in ((dex.get("ledger") or {})).items()}
    for ts, seconds in (buckets or {}).items():
        day = history_day(ts)
        row = days.setdefault(day, {})
        row["weread"] = max(int(row.get("weread", 0)), int(seconds))
    days = prune_history(days, keep_days, today)
    return {"version": 1, "updatedAt": today, "days": days}


def load_history(path: str = HISTORY_PATH) -> dict:
    """读历史。文件不存在或坏了都当空的。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "updatedAt": "", "days": {}}
    if not isinstance(data, dict):
        return {"version": 1, "updatedAt": "", "days": {}}
    data.setdefault("days", {})
    return data


def write_history(history: dict, path: str = HISTORY_PATH) -> None:
    """写历史。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main(dex_path: str = "data/dex.json", history_path: str = HISTORY_PATH,
         buckets=None, today: str | None = None) -> dict:
    """跑一轮历史维护。**只写 history_path**，绝不碰 dex_path。"""
    if today is None:
        today = datetime.datetime.now(TZ_SH).strftime("%Y-%m-%d")
    try:
        with open(dex_path, encoding="utf-8") as f:
            dex = json.load(f)
    except (OSError, ValueError):
        dex = {}
    if buckets is None:
        try:
            from scripts import weread_client as wc
            buckets = wc.read_times("monthly")
        except Exception as exc:  # noqa: BLE001 —— 取不到就只留账本里那些
            print("[历史] 月桶取数失败: %s" % exc)
            buckets = {}
    old = load_history(history_path)
    merged = merge_history(old.get("days") or {},
                            build_history(dex, buckets, today)["days"])
    # ★ merge 之后再剪一次 —— 老文件里可能有早就超出窗口的天
    merged = prune_history(merged, DEFAULT_KEEP_DAYS, today)
    history = {"version": 1, "updatedAt": today, "days": merged}
    write_history(history, history_path)
    print("[历史] %d 天" % len(history["days"]))
    return history


if __name__ == "__main__":
    main()
