"""抽卡与状态机。纯逻辑，不碰网络、不碰文件。"""

from __future__ import annotations

import datetime
import random

# streak 阈值 -> 闪光概率。达到该阈值即生效（取最高档）
SHINY_ODDS: dict[int, float] = {
    0: 1 / 96,
    3: 1 / 48,
    7: 1 / 32,
    30: 1 / 16,
    100: 1.0,
}

THRESHOLD_SECONDS = 1800  # 30 分钟


def shiny_probability(streak: int) -> float:
    """按连读天数返回闪光概率。streak>=100 保底必出。"""
    prob = SHINY_ODDS[0]
    for threshold, p in sorted(SHINY_ODDS.items()):
        if streak >= threshold:
            prob = p
    return prob


def compute_streak(award_dates: list[str], today: str) -> int:
    """连续达标天数。

    今天已达标：从今天往前数。
    今天还没达标：从昨天往前数（显示"已经连读 N 天"，今天读满就变 N+1）。
    中间断过就停下。
    """
    days = set(award_dates)
    if not days:
        return 0
    cur = datetime.date.fromisoformat(today)
    if cur.isoformat() not in days:
        cur -= datetime.timedelta(days=1)
    streak = 0
    while cur.isoformat() in days:
        streak += 1
        cur -= datetime.timedelta(days=1)
    return streak


def pick(owned_ids: set[str], pool: list[dict], streak: int, rng: random.Random) -> dict | None:
    """从未收集的池子里抽 1 只。池空返回 None。

    闪光概率由 streak 决定；rng 注入以便测试可复现。
    """
    candidates = [p for p in pool if p.get("id") not in owned_ids]
    if not candidates:
        return None
    chosen = dict(rng.choice(candidates))
    prob = shiny_probability(streak)
    chosen["shiny"] = rng.random() < prob
    return chosen


def new_state(old: dict, award: dict | None, today: str, today_secs: int) -> dict:
    """算出新的 dex.json。

    幂等：若 old['lastAwardDate'] == today，不再追加。
    """
    collected = list(old.get("collected") or [])
    last = old.get("lastAwardDate")
    if award is not None and last != today:
        collected.append(
            {
                "id": award.get("id"),
                "nameZh": award.get("nameZh", ""),
                "shiny": bool(award.get("shiny")),
                "at": award.get("at"),
                "date": today,
            }
        )
        last = today

    award_dates = [c.get("date") for c in collected if c.get("date")]
    streak = compute_streak(award_dates, today)

    return {
        "version": 1,
        "collected": collected,
        "streak": streak,
        "lastAwardDate": last,
        "today": {
            "date": today,
            "seconds": int(today_secs),
            "threshold": THRESHOLD_SECONDS,
        },
        "updatedAt": award.get("at") if award else old.get("updatedAt"),
    }
