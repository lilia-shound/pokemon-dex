#!/usr/bin/env python3
"""发放器：查今日阅读时长 → 判定 → 抽卡 → 写 dex.json → 推送。

    python3 scripts/award.py             # 正常跑一轮
    python3 scripts/award.py --dry-run   # 只看判定结果，不写文件
    python3 scripts/award.py --force     # 无视达标线强制发奖（用于联调）

**补发机制**：微信读书的阅读时长是延迟上报的（实测滞后 3~10 分钟，且持续回补）。
当天读满的数据可能到次日凌晨才到，那时"今天"的桶已经翻页。
所以判定不只看今天，而是扫当前周期内所有「达标但还没发奖」的日期，补发最早的那一天。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import random
import sys
import zoneinfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import dex_core as dc  # noqa: E402
from scripts import push_notify  # noqa: E402
from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")
DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
POKEDEX_PATH = ROOT / "data" / "pokedex.json"
TOTAL_POKEMON = 1025


def load_pokedex() -> list[dict]:
    data = json.loads(POKEDEX_PATH.read_text(encoding="utf-8"))
    return data["pokemon"]


def load_state() -> dict:
    if not DEX_PATH.exists():
        return {"version": 1, "collected": [], "streak": 0, "lastAwardDate": None, "today": {}}
    return json.loads(DEX_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    DEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEX_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_buckets() -> dict[int, int]:
    """合并 monthly 与 weekly 两个周期，取并集。

    monthly 覆盖本月至今，weekly 覆盖本周至今。两者合并能覆盖跨周/跨月的边界，
    补发时不会漏掉周期最后一天。
    """
    merged: dict[int, int] = {}
    for mode in ("monthly", "weekly"):
        try:
            for k, v in wc.read_times(mode).items():
                merged[k] = max(merged.get(k, 0), v)
        except wc.WeReadError as exc:
            print(f"[警告] {mode} 分桶取数失败：{exc}", file=sys.stderr)
    return merged


def pending_award_days(buckets: dict[int, int], awarded_dates: set[str],
                       threshold: int) -> list[tuple[str, int]]:
    """所有「达标但还没发奖」的日期，按时间正序。"""
    out = []
    for k, v in sorted(buckets.items()):
        if v < threshold:
            continue
        day = datetime.datetime.fromtimestamp(k, SH).date().isoformat()
        if day not in awarded_dates:
            out.append((day, v))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只判定不写文件")
    ap.add_argument("--force", action="store_true", help="无视达标线强制发奖")
    ap.add_argument("--no-push", action="store_true", help="不发微信推送")
    ap.add_argument("--date", default=None, help="指定发奖日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args(argv)

    now = datetime.datetime.now(SH)
    today = args.date or now.strftime("%Y-%m-%d")

    try:
        buckets = fetch_buckets()
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 取数失败，本轮不发奖：{exc}", file=sys.stderr)
        return 1

    old = load_state()
    owned = {c["id"] for c in old.get("collected") or []}
    awarded_dates = {c.get("date") for c in old.get("collected") or [] if c.get("date")}
    _, today_secs = wc.today_seconds(buckets, now=now)

    print(f"今天 {today}   官方已记 {today_secs} 秒 ({round(today_secs / 60, 1)} 分)   "
          f"达标线 {dc.THRESHOLD_SECONDS} 秒")
    print(f"已收集 {len(owned)}/{TOTAL_POKEMON}   周期内桶数 {len(buckets)}")

    pending = pending_award_days(buckets, awarded_dates, dc.THRESHOLD_SECONDS)
    if pending:
        print(f"待补发：{', '.join(f'{d}({v}s)' for d, v in pending)}")

    # 选定发奖日：补发优先取最早那天，否则取今天
    if pending and not args.force:
        award_day, award_secs = pending[0]
    else:
        award_day, award_secs = today, today_secs

    if args.force:
        award_day = today
        award_secs = today_secs
        print("[force] 无视达标线")
    elif award_day != today:
        print(f"→ 补发 {award_day}（{award_secs} 秒）")

    reached = award_secs >= dc.THRESHOLD_SECONDS or args.force
    if not reached:
        streak = dc.compute_streak(sorted(awarded_dates), today)
        print(f"未达标，今天没有宝可梦。（连读 {streak} 天）")
        if not args.dry_run:
            save_state(dc.new_state(old, None, today, today_secs))
        return 0

    pool = load_pokedex()
    # 发奖日的连读天数决定闪光概率
    streak_after = dc.compute_streak(sorted(awarded_dates | {award_day}), award_day)
    print(f"连读 {streak_after} 天   闪光概率 {dc.shiny_probability(streak_after):.4f}")
    award = dc.pick(owned, pool, streak_after, random.Random())
    if award is None:
        print(f"池子空了 —— {TOTAL_POKEMON} 只全部收集完成！")
        return 0

    award_full = {
        "id": award["id"],
        "nameZh": award["nameZh"],
        "shiny": award["shiny"],
        "at": now.isoformat(),
    }
    tag = "✦ 闪光" if award["shiny"] else "●"
    print(f"★ 获得 {tag} {award['nameZh']}（No.{award['id']}）")

    if args.dry_run:
        print("[dry-run] 未写入文件")
        return 0

    state = dc.new_state(old, award_full, today, today_secs, award_date=award_day)
    save_state(state)
    print(f"已写入 {DEX_PATH}   图鉴 {len(state['collected'])}/{TOTAL_POKEMON}   连读 {state['streak']} 天")

    if not args.no_push:
        try:
            push_notify.send(
                f"{tag} 捕获 {'闪光' if award['shiny'] else ''}{award['nameZh']}",
                f"今日已读 {round(today_secs / 60)} 分钟 · 连读 {state['streak']} 天 · "
                f"图鉴 {len(state['collected'])}/{TOTAL_POKEMON}",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[推送] 跳过: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
