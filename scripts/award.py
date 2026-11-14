#!/usr/bin/env python3
"""发放器：查今日阅读时长 → 判定 → 抽卡 → 写 dex.json → 推送。

    python3 scripts/award.py             # 正常跑一轮
    python3 scripts/award.py --dry-run   # 只看判定结果，不写文件
    python3 scripts/award.py --force     # 无视达标线强制发奖（用于联调）
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只判定不写文件")
    ap.add_argument("--force", action="store_true", help="无视达标线直接发奖")
    ap.add_argument("--no-push", action="store_true", help="不发微信推送")
    ap.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args(argv)

    now = datetime.datetime.now(SH)
    today = args.date or now.strftime("%Y-%m-%d")

    try:
        rt = wc.read_times("monthly")
    except wc.WeReadError as exc:
        print(f"[失败] 取数失败，本轮不发奖：{exc}", file=sys.stderr)
        return 1

    _, secs = wc.today_seconds(rt, now=now)
    old = load_state()
    owned = {c["id"] for c in old.get("collected") or []}
    award_dates = [c.get("date") for c in old.get("collected") or [] if c.get("date")]
    streak_before = dc.compute_streak(award_dates, today)
    already = old.get("lastAwardDate") == today

    reached = secs >= dc.THRESHOLD_SECONDS or args.force
    print(f"日期 {today}   今日已读 {secs} 秒 ({round(secs / 60, 1)} 分)   达标线 {dc.THRESHOLD_SECONDS} 秒")
    print(f"已收集 {len(owned)}/{TOTAL_POKEMON}   连读 {streak_before} 天"
          f"   闪光概率 {dc.shiny_probability(streak_before + 1):.4f}"
          f"{'（今日达标后）' if reached else ''}")

    if already:
        print("今天已经发过奖了，跳过。")
        if not args.dry_run:
            save_state(dc.new_state(old, None, today, secs))
        return 0

    if not reached:
        print("未达标，今天没有宝可梦。")
        if not args.dry_run:
            save_state(dc.new_state(old, None, today, secs))
        return 0

    pool = load_pokedex()
    # 今天达标后连读天数 +1，用它决定闪光概率
    streak_after = dc.compute_streak(award_dates + [today], today)
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

    state = dc.new_state(old, award_full, today, secs)
    save_state(state)
    print(f"已写入 {DEX_PATH}   图鉴 {len(state['collected'])}/{TOTAL_POKEMON}   连读 {state['streak']} 天")

    if not args.no_push:
        try:
            push_notify.send(
                f"{tag} 捕获 {'闪光' if award['shiny'] else ''}{award['nameZh']}",
                f"今日已读 {round(secs / 60)} 分钟 · 连读 {state['streak']} 天 · "
                f"图鉴 {len(state['collected'])}/{TOTAL_POKEMON}",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[推送] 跳过: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
