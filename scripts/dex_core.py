"""抽卡与状态机。纯逻辑，不碰网络、不碰文件。

**达标线（阈值）在本模块里没有副本**：权威值在 `data/sources.json` 的
`sources.weread.per`（「掉一只」的实际秒数 = `perAward × sources.weread.per`）。
想临时调阈值就用 `DEX_THRESHOLD` 环境变量 —— 它由 `sources.load_config`
覆盖 `weread.per`，不是在这里读的（以前那个 `THRESHOLD_SECONDS` 常量既没有
引用点、注释又和事实相反，已删除）。
"""

from __future__ import annotations

import datetime
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

# 这条链要养多少天才能兑现一次保底闪光（按日历天算，见 advance_chain 的说明）
SHINY_CARE_DAYS = 30


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


def advance_chain(chains: dict, pokemon: dict, day: str) -> dict:
    """登记「这个发奖日抽到了这条链的宝可梦」。原地修改 chains 并返回链状态。

    链状态：
        firstDate     —— 该链首次被收服的日期（care 的计时起点）
        lastShinyDate —— 上次兑现保底的日期（None = 还没兑现过）

    注意是**原地修改**，且返回的是链状态本身（不是链状态的拷贝）。

    **★ care 按日历天算，不按抽卡次数累加**（2026-11-18 人类裁决）：
    旧口径是「每次抽到就 care += 1」，但 `pick` 只从**未收服**池里抽
    ⇒ 同一条链最多被抽「链大小」次，而最大链只有 9 只（伊布族）
    ⇒ care 的上限是 9，永远够不到 30 ⇒ **保底闪光数学上不可能触发**。
    改成按日历天（首次被收服那天算起）后，链的大小不再影响可达性。

    **★ care 必须是从日期算出来的，不能是累加计数器**：cron 的纪律是「只有真抽到才推状态」
    ⇒ 平时不推 ⇒ 累加计数器会整天数丢失。日期锚点不受影响。

    **旧结构的链条目没有 firstDate**（旧的是 care / pendingShiny / nextShinyAt）
    ⇒ 计时起点缺失，chain_care 会一直返回 0，这条链永远养不起来
    ⇒ 这里补上 firstDate = day（并确保 lastShinyDate 存在）。
    """
    key = chain_key(pokemon)
    if not key:
        return {}
    st = chains.get(key)
    if st is None:
        st = {"firstDate": day, "lastShinyDate": None}
        chains[key] = st
        return st
    # 已有条目：补齐新结构缺的字段，但**不重置**已有的计时起点
    if not st.get("firstDate"):
        st["firstDate"] = day
    st.setdefault("lastShinyDate", None)
    return st


def chain_care(chain: dict, day: str) -> int:
    """这条链养了多少天（从首次被收服那天算起）。

    按**日历天**算，不是按抽卡次数 —— 后者数学上不可能够到保底门槛
    （pick 只从未收服池抽 ⇒ 一条链最多被抽「链大小」次，最大链只有 9 只）。
    """
    first = (chain or {}).get("firstDate")
    if not first:
        return 0
    try:
        start = datetime.date.fromisoformat(str(first))
        now = datetime.date.fromisoformat(str(day))
    except ValueError:
        return 0
    return (now - start).days


def chain_shiny_ready(chain: dict, day: str) -> bool:
    """这条链的保底闪光能不能兑现。

    规则：养满 SHINY_CARE_DAYS 天 → 可以兑现；
    兑现过一次后，要再养满 SHINY_CARE_DAYS 天才能再兑现。
    """
    if chain_care(chain, day) < SHINY_CARE_DAYS:
        return False
    last = (chain or {}).get("lastShinyDate")
    if not last:
        return True
    try:
        last_day = datetime.date.fromisoformat(str(last))
        now = datetime.date.fromisoformat(str(day))
    except ValueError:
        return True
    return (now - last_day).days >= SHINY_CARE_DAYS
