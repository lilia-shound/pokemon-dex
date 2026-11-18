#!/usr/bin/env python3
"""自检：把微信读书的今日状态摊开打印。

用法：
    python3 scripts/check_today.py                # 默认 monthly 分桶
    python3 scripts/check_today.py --mode weekly
    python3 scripts/check_today.py --json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import sources as src  # noqa: E402
from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")
SOURCES_PATH = pathlib.Path(os.environ.get(
    "SOURCES_PATH",
    pathlib.Path(__file__).resolve().parent.parent / "data" / "sources.json"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="monthly",
                    choices=["monthly", "weekly", "annually", "overall"])
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    # 先校验配置，别让一个坏配置白白打一次 API
    try:
        cfg = src.load_config(SOURCES_PATH)
    except src.SourceConfigError as exc:
        print(f"[失败] 源注册表有问题：{exc}", file=sys.stderr)
        return 1
    # 注册表里没有微信读书源：给出脚本自己的提示，别让调用方看到裸 KeyError traceback
    weread_per = cfg.get("sources", {}).get("weread", {}).get("per")
    if weread_per is None:
        print("[失败] 源注册表有问题：缺少微信读书源（sources.weread）",
              file=sys.stderr)
        return 1
    weread_per = int(weread_per)
    per_award = int(cfg["perAward"])

    try:
        rt = wc.read_times(args.mode)
    except wc.WeReadError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    key, secs = wc.today_seconds(rt)
    now = datetime.datetime.now(SH)
    credit = src.day_credit(cfg, {"weread": secs})
    out = {
        "checkedAt": now.isoformat(),
        "mode": args.mode,
        "bucketCount": len(rt),
        "todayBucketKey": key,
        "todaySeconds": secs,
        "todayMinutes": round(secs / 60, 1),
        # 掉 1 只实际需要的微信读书秒数（与 dex.json 的 today.threshold 同义）
        "thresholdSeconds": per_award * weread_per,
        # 1 个额度 = 多少微信读书秒数
        "creditSeconds": weread_per,
        "reached": credit >= per_award,
        "credit": credit,
        "perAward": per_award,
        "buckets": [
            {
                "key": k,
                "date": datetime.datetime.fromtimestamp(k, SH).strftime("%Y-%m-%d %a"),
                "seconds": v,
            }
            for k, v in sorted(rt.items())
        ],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"检查时间   {now:%Y-%m-%d %H:%M:%S} (Asia/Shanghai)")
    print(f"分桶模式   {args.mode}   桶数 {len(rt)}")
    print(f"今日秒数   {secs} 秒 ({round(secs / 60, 1)} 分)   "
          f"额度 {credit}/{per_award} → "
          f"{'达标' if credit >= per_award else '未达标'}")
    print(f"今日桶 key {key}")
    print("--- 全部桶 ---")
    for b in out["buckets"]:
        print(f"  {b['date']}  key={b['key']:<12} {b['seconds']:>7} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
