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

# 已废弃的兼容常量：权威值在 `data/sources.json` 的 `sources.weread.per`。
# 全仓没有第二个引用点，改这个数字（或 DEX_THRESHOLD 环境变量）**没有任何效果** ——
# 真实阈值由注册表决定，别再照着老注释调这个。
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
        # 两层拷贝：advance_chain 原地改内层链状态 dict，浅拷贝会污染传进来的旧 state
        "ledger": {d: dict(v) for d, v in (ledger_data or {}).items()},
        "chains": {k: dict(v) for k, v in (chains or {}).items()},
        # v1 有这个字段（v2 别静默丢掉）；语义与 v1 一致：
        # 有新发奖就更新成发奖时间，否则沿用旧的
        "updatedAt": (new_awards[-1].get("at") if new_awards
                      else old.get("updatedAt")),
    }


def _chain_names(pokemon: dict) -> list[str]:
    """进化链上的名字，去重并保留顺序。

    原始数据里皮卡丘那条是 ["皮丘","皮卡丘","雷丘","皮丘","皮卡丘","雷丘"]，
    去重后才是真正的链。
    """
    names: list[str] = []
    for n in pokemon.get("evolution") or []:
        if n and n not in names:
            names.append(n)
    return names


def chain_key(pokemon: dict) -> str:
    """进化链的唯一标识 —— **基础形态名**（evolution 的第一个名字）。

    为什么不用整条数组拼起来当 key：真实数据（42arch）把分叉的进化关系拍平成了直线，
    而且同一条链的成员拿到的 evolution 数组并不一致（走路草 6 个名字，霸王花只有 3 个）。
    用整条数组当 key 会把同一条链拆成多份（实测 543 条生物链被拆成 588 个 key，
    19 条链被拆开，care 被摊薄到不同 key 上）。

    基础形态名是同链所有成员共有的，本地就能算，不需要全局上下文。
    实测：用基础形态名分组 → 543 组，正好等于生物链数。
    """
    names = _chain_names(pokemon)
    return names[0] if names else ""


def advance_chain(chains: dict, pokemon: dict) -> dict:
    """给这条链加一天培养。原地修改并返回 chains。

    care 每多 30 天给一次保底闪光（pendingShiny += 1）——
    下次抽到这个链的宝可梦时可以强制闪光。把「一次性运气」换成「持续投入」。

    保底判定用 while 而不是 if：state 是导入/手改出来的时 care 可能跳变，
    跳过的保底要一次补齐（care=95、nextShinyAt=30 → 给 3 次，而不是 1 次）。
    nextShinyAt 严格递增，不会死循环。

    注：形态改写（进化阶数 → collected[].form）不在本层做 ——
    分叉的进化关系在拍平的数据里没有唯一的「这一阶对应哪只」，那是 S6 的活。
    """
    key = chain_key(pokemon)
    if not key:
        return chains
    st = chains.setdefault(key, {"care": 0, "pendingShiny": 0, "nextShinyAt": 30})
    st["care"] = int(st.get("care", 0)) + 1
    while st["care"] >= int(st.get("nextShinyAt", 30)):
        st["pendingShiny"] = int(st.get("pendingShiny", 0)) + 1
        # 节奏固定：每次 +30，而不是 care + 30 —— care 跳变时不该整体后移
        st["nextShinyAt"] = int(st.get("nextShinyAt", 30)) + 30
    return chains
