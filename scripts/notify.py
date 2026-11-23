"""邮件通知：收服了新的宝可梦 / 连读快断了。

**旁路脚本** —— 它只读 `data/dex.json`、只写 `data/notify_state.json`，
**绝不碰结算**（`dex_core` / `award` / `ledger` / `sources` 一行不改）。
任何失败都不能挡住发奖。

为什么状态不放 `dex.json`：`scripts/dex_core.py` 的 `build_state()` 返回的是
**重新构造的 dict**（只含固定字段），`award.py` 写回时会冲掉任何没被它
显式带上的字段 ⇒ 「发过没发过」只能放独立文件。

没配 `RESEND_API_KEY` 时静默跳过。
"""

from __future__ import annotations

import datetime
import json
import os

STATE_PATH = "data/notify_state.json"
# 「连读快断了」的提醒时刻（北京时间）
WARN_HOUR = 21
# 微信读书 1 额度 = 900 秒官方口径，但官方计数漏计约三成 ⇒ 体感约 ×1.4
WARN_MINUTES = 20

# 北京时间（UTC+8，无夏令时）
TZ_SH = datetime.timezone(datetime.timedelta(hours=8))


def shanghai_now(now: datetime.datetime | None = None) -> datetime.datetime:
    """当前北京时间。测试可以传 now 进来。"""
    if now is not None:
        return now
    return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_SH)


def load_state(path: str = STATE_PATH) -> dict:
    """读幂等标记。文件不存在或坏了都当空的。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"awarded": [], "streak": {}}
    if not isinstance(data, dict):
        return {"awarded": [], "streak": {}}
    data.setdefault("awarded", [])
    data.setdefault("streak", {})
    return data


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """写幂等标记。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def award_key(entry: dict) -> str:
    """条目身份：`日期#编号` —— 稳定、可重复算。"""
    return "%s#%s" % (entry.get("date", ""), entry.get("id", ""))


def pending_awards(collected: list, notified: list) -> list[dict]:
    """还没通知过的条目。

    ★ **不看日期** —— `award.py` 会一次结清所有待发奖日，
    所以「今天」这轮可能收服的是前天补发的那只 ⇒ 凡是没通知过的都要算。
    """
    seen = set(notified or [])
    out = []
    for entry in collected or []:
        key = award_key(entry)
        if not key or key == "#":
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def should_warn_streak(streak, credit, per_award, now: datetime.datetime,
                       warned_today: bool) -> bool:
    """今天要不要发「连读快断了」的提醒。

    四个条件全满足才发：
    - 连读 ≥ 2 天（连 1 天就断无所谓，不打扰）
    - 今天还没达标
    - 北京时间已经 ≥ 21:00（早上别催）
    - 今天还没发过（一天最多一封）
    """
    if warned_today:
        return False
    if int(streak or 0) < 2:
        return False
    if int(credit or 0) >= int(per_award or 1):
        return False
    if now.hour < WARN_HOUR:
        return False
    return True
