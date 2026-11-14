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
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import dex_core as dc  # noqa: E402
from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="monthly",
                    choices=["monthly", "weekly", "annually", "overall"])
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    try:
        rt = wc.read_times(args.mode)
    except wc.WeReadError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    key, secs = wc.today_seconds(rt)
    now = datetime.datetime.now(SH)
    out = {
        "checkedAt": now.isoformat(),
        "mode": args.mode,
        "bucketCount": len(rt),
        "todayBucketKey": key,
        "todaySeconds": secs,
        "todayMinutes": round(secs / 60, 1),
        "thresholdSeconds": dc.THRESHOLD_SECONDS,
        "reached": secs >= dc.THRESHOLD_SECONDS,
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
          f"达标线 {dc.THRESHOLD_SECONDS} 秒 → "
          f"{'达标' if secs >= dc.THRESHOLD_SECONDS else '未达标'}")
    print(f"今日桶 key {key}")
    print("--- 全部桶 ---")
    for b in out["buckets"]:
        print(f"  {b['date']}  key={b['key']:<12} {b['seconds']:>7} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
