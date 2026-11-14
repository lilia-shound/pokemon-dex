"""微信读书官方 Agent API 客户端。

只做两件事：调网关、取出按周期分桶的阅读时长。
不做任何抽卡逻辑（那在 dex_core）。
"""

from __future__ import annotations

import datetime
import json
import os
import urllib.error
import urllib.request
import zoneinfo

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"
SH = zoneinfo.ZoneInfo("Asia/Shanghai")


class WeReadError(RuntimeError):
    """网关调用失败。调用方必须原样抛出，不得静默降级。"""


def call(api_name: str, **params) -> dict:
    key = os.environ.get("WEREAD_API_KEY")
    if not key:
        raise WeReadError("环境变量 WEREAD_API_KEY 未设置")
    body = {"api_name": api_name, "skill_version": SKILL_VERSION}
    body.update(params)
    req = urllib.request.Request(
        GATEWAY,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise WeReadError(f"网关 HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise WeReadError(f"网关请求失败: {exc}") from exc

    upgrade = payload.get("upgrade_info")
    if upgrade:
        raise WeReadError(f"微信读书 Skill 需要升级: {upgrade.get('message')}")

    errcode = payload.get("errcode")
    if errcode not in (None, 0):
        raise WeReadError(f"网关返回错误 errcode={errcode}")

    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def read_times(mode: str = "monthly") -> dict[int, int]:
    """返回 {桶起始时间戳: 该桶的阅读秒数}。桶粒度由 mode 决定：

    monthly/weekly -> 按天；annually -> 按月；overall -> 按年。
    """
    data = call("/readdata/detail", mode=mode)
    raw = data.get("readTimes") or {}
    out: dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def today_seconds(rt: dict[int, int], now: datetime.datetime | None = None) -> tuple[int | None, int]:
    """返回 (今日桶 key 或 None, 今日阅读秒数)。

    判定规则：取所有不晚于今天的桶里最大的那个；若它的北京日期等于今天，
    则它的值就是今日秒数；否则（最新的桶是昨天或更早）今天还没读，返回 0。

    桶 key 用的就是北京时间周期起点（已用月桶/年桶反推验证），所以不猜时区。
    """
    now = now or datetime.datetime.now(SH)
    today = now.astimezone(SH).date()
    if not rt:
        return None, 0

    cutoff = int(now.astimezone(SH).replace(hour=23, minute=59, second=59, microsecond=0).timestamp())
    past = [k for k in rt if k <= cutoff]
    if not past:
        return None, 0
    latest = max(past)
    day = datetime.datetime.fromtimestamp(latest, SH).date()
    if day != today:
        return None, 0
    return latest, int(rt[latest])
