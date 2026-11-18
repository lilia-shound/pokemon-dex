"""抽卡与状态机。纯逻辑，不碰网络、不碰文件。"""

from __future__ import annotations

import datetime
import os
import random

from scripts import sources as src

# streak 阈值 -> 闪光概率。达到该阈值即生效（取最高档）
SHINY_ODDS: dict[int, float] = {
    0: 1 / 96,
    3: 1 / 48,
    7: 1 / 32,
    30: 1 / 16,
    100: 1.0,
}

# 达标线：默认 30 分钟。微信读书的官方计数会漏掉一部分（实测读 25 分钟只记 17 分钟），
# 所以这个数字是「官方口径」的实际成本。改这里或设 DEX_THRESHOLD 环境变量都能调。
THRESHOLD_SECONDS = int(os.environ.get("DEX_THRESHOLD", "900"))


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


def pending_days(cfg: dict, ledger: dict, awarded_dates) -> list[str]:
    """所有「额度够了但还没发奖」的日期，按时间正序。

    一天最多掉 maxPerDay 只 —— 现在恒为 1 —— 所以每个日期最多出现一次。
    """
    awarded = set(awarded_dates or ())
    out = []
    for day in sorted(ledger or {}):
        if day in awarded:
            continue
        if src.day_credit(cfg, ledger[day]) >= int(cfg["perAward"]):
            out.append(day)
    return out


def build_state(old: dict, cfg: dict, today: str, today_sources: dict,
                ledger_data: dict, chains: dict, new_awards: list) -> dict:
    """组装 v2 状态。

    new_awards 是本次要并入的发奖列表（调用方已经排好序、判过幂等）。
    **这里绝不按日期去重** —— v1 的 new_state 曾静默丢掉重复的卡，
    导致调用方打印了「★ 获得」而状态里没有（旧版的「假消息」坑）。
    幂等由调用方保证。
    """
    collected = [dict(c) for c in (old.get("collected") or [])]
    for a in new_awards or []:
        collected.append(dict(a))
    awarded_dates = sorted({c["date"] for c in collected if c.get("date")})
    # 只保留启用源的原始量，和 credit 的口径保持一致
    enabled = src.enabled_sources(cfg)
    today_map = {
        k: int(v) for k, v in (today_sources or {}).items()
        if k in enabled and int(v) > 0
    }
    weread_per = int(cfg.get("sources", {}).get("weread", {}).get("per", 0))
    per_award = int(cfg["perAward"])
    return {
        "version": 2,
        "collected": collected,
        # 连读天数锚在「今天」，会衰减：断签后归零、闪光概率回到 1/96。
        # 这是相对 v1 的有意变更 —— v1 锚在发奖日，会一直挂着一个不衰减的旧值。
        "streak": compute_streak(awarded_dates, today),
        "lastAwardDate": awarded_dates[-1] if awarded_dates else None,
        "today": {
            "date": today,
            "seconds": int(today_map.get("weread", 0)),
            # 读方（site/app.js、plugin）拿 threshold 当「掉一只要读多久」用，
            # 所以这里发的是「掉一只实际需要的微信读书秒数」，不是 1 个额度的量。
            "threshold": per_award * weread_per,
            "sources": today_map,
            "credit": src.day_credit(cfg, today_map),
            "perAward": per_award,
        },
        "ledger": dict(ledger_data or {}),
        "chains": dict(chains or {}),
    }


def new_state(old: dict, award: dict | None, today: str, today_secs: int,
              award_date: str | None = None) -> dict:
    """算出新的 dex.json。

    幂等：按**日期**判重——同一个日期只发一次。
    award_date 用于「补发」：微信读书延迟上报时，昨天读满的数据可能今天才到，
    这时要发的是昨天那一份，对 streak 也按昨天算。
    """
    ad = award_date or today
    collected = list(old.get("collected") or [])
    awarded = {c.get("date") for c in collected if c.get("date")}

    if award is not None and ad not in awarded:
        collected.append(
            {
                "id": award.get("id"),
                "nameZh": award.get("nameZh", ""),
                "shiny": bool(award.get("shiny")),
                "at": award.get("at"),
                "date": ad,
            }
        )
        awarded = {c.get("date") for c in collected if c.get("date")}

    last = max(awarded) if awarded else None
    # 发过奖就用发奖日算连续，没发过就按今天算（显示截至昨天的连续天数）
    streak = compute_streak(sorted(awarded), ad if award is not None else today)

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
