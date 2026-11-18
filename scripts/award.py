#!/usr/bin/env python3
"""发放器：查各源今日数据 → 折算额度 → 结算 → 抽卡 → 写 dex.json → 推送。

    python3 scripts/award.py                 # 正常跑一轮
    python3 scripts/award.py --dry-run       # 只看判定结果，不写文件
    python3 scripts/award.py --force         # 无视达标线强制发奖（用于联调）
    python3 scripts/award.py --migrate-only  # 只把 dex.json 升到 v2，不抽卡

**额度模型**：每个源声明「多少原始单位 = 1 额度」（sources.json 的 per），
当天额度 = 各启用源折算后之和；满 perAward 个额度才掉一只（现在恒为 2）。

**补发机制**：微信读书的阅读时长是延迟上报的（实测滞后 3~10 分钟，且持续回补）。
当天读满的数据可能到次日凌晨才到，那时「今天」的桶已经翻页。
所以判定不只看今天，而是扫账本里所有「额度够了但还没发奖」的日期，
一次结清（升序，最多 maxBackfill 天）。

**取数失败 ≠ 今天没读够**：monthly 与 weekly 两个周期都取数失败时，脚本会 exit 1 并打印
「[失败] 取数失败…」，而不是「未达标，今天没有宝可梦。」+ rc=0 ——
两者在 cron 里同形的话，Key 失效 / skill 版本过期 / 网关抖动会被安静地当成「用户今天没读书」，
全站就停在「不更新」状态而没人知道。

**`--migrate-only` 只在迁移当天跑一次**：它直接覆盖 `data/dex.json`、**不带自动备份**。
备份在 git 里 —— 需要回退时用 `git show <commit>:data/dex.json` 取回旧版。
隔天再跑会把上一天的 `today.seconds` 清零
（`today_sources_from` 只在 `today.date == 今天` 时还原今天的读数）。
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
from scripts import ledger as lg  # noqa: E402
from scripts import push_notify  # noqa: E402
from scripts import sources as src  # noqa: E402
from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")
DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
SOURCES_PATH = pathlib.Path(os.environ.get("SOURCES_PATH", ROOT / "data" / "sources.json"))
POKEDEX_PATH = ROOT / "data" / "pokedex.json"


def load_pokedex() -> list[dict]:
    data = json.loads(POKEDEX_PATH.read_text(encoding="utf-8"))
    return data["pokemon"]


def pokedex_total() -> int:
    """总数从 pokedex.json 读（预告世代并进来后不再是 1025）。"""
    data = json.loads(POKEDEX_PATH.read_text(encoding="utf-8"))
    return int(data.get("count") or len(data.get("pokemon", [])))


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

    **两个模式都失败时抛错** —— 否则「取数失败」和「今天没读够」在 cron 里长得一模一样，
    全站会安静地停在「不更新」状态而没人知道（Key 失效、skill 版本过期、网关抖动
    都会被当成「用户今天没读书」）。单模式失败仍可降级（另一个模式的数据照样用）。
    """
    merged: dict[int, int] = {}
    failures: list[str] = []
    for mode in ("monthly", "weekly"):
        try:
            for k, v in wc.read_times(mode).items():
                merged[k] = max(merged.get(k, 0), v)
        except wc.WeReadError as exc:
            failures.append(f"{mode}: {exc}")
            print(f"[警告] {mode} 分桶取数失败：{exc}", file=sys.stderr)
    if not merged and failures:
        raise RuntimeError("微信读书取数全部失败：" + "；".join(failures))
    return merged


def load_config() -> dict:
    """读源注册表。失败抛 SourceConfigError。"""
    return src.load_config(SOURCES_PATH)


def weread_days(buckets: dict[int, int]) -> dict[str, int]:
    """把微信读书的时间桶换成 {日期: 秒数}，同一天取 max。"""
    out: dict[str, int] = {}
    for k, v in buckets.items():
        day = datetime.datetime.fromtimestamp(k, SH).date().isoformat()
        out[day] = max(out.get(day, 0), int(v))
    return out


def today_sources_from(state: dict, today: str) -> dict:
    """从旧状态里推出今天的各源量。

    v2 走 today.sources；v1 只有 today.seconds，按微信读书口径还原。

    **只在旧 `today.date` 就是今天时才还原** —— 否则会把几天前的读数
    写成「今天读了多少」（实测迁移后图鉴页会显示错误的今日进度，
    而且这是写进数据文件的事实错误，不只是显示问题）。
    """
    t = (state or {}).get("today") or {}
    if t.get("date") != today:
        return {}
    if t.get("sources"):
        return dict(t["sources"])
    secs = int(t.get("seconds") or 0)
    return {"weread": secs} if secs > 0 else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只判定不写文件")
    ap.add_argument("--force", action="store_true", help="无视达标线强制发奖")
    ap.add_argument("--no-push", action="store_true", help="不发微信推送")
    ap.add_argument("--migrate-only", action="store_true",
                    help="只把 dex.json 升到 v2，不抽卡（一次性迁移用）。"
                         "备份在 git 里（git show <commit>:data/dex.json 可取回旧版），"
                         "只该在迁移当天跑一次")
    ap.add_argument("--date", default=None, help="指定发奖日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args(argv)

    now = datetime.datetime.now(SH)
    today = args.date or now.strftime("%Y-%m-%d")

    try:
        cfg = load_config()
    except src.SourceConfigError as exc:
        print(f"[失败] 源注册表有问题，本轮不发奖：{exc}", file=sys.stderr)
        return 1

    old = lg.migrate_state(load_state())
    # 深拷贝：advance_chain 会原地改内层链状态，直接用 old 里的会污染旧 state
    chains = {k: dict(v) for k, v in (old.get("chains") or {}).items()}

    if args.migrate_only:
        state = dc.build_state(old, cfg, today, today_sources_from(old, today),
                               old.get("ledger") or {}, chains, [])
        if not args.dry_run:
            save_state(state)
        print(f"已迁移到 v2：{len(state['collected'])} 只已收服   "
              f"（未抽卡）")
        return 0

    try:
        buckets = fetch_buckets()
    except Exception as exc:  # noqa: BLE001
        # 这条分支必须存在：取数失败（Key 失效 / skill 版本过期 / 网关抖动）与
        # 「今天没读够」是两回事，不能在 cron 里长得一模一样。
        print(f"[失败] 取数失败，本轮不发奖（这不是「今天没读够」）：{exc}",
              file=sys.stderr)
        return 1

    ledger_data = lg.merge_amounts(old.get("ledger") or {}, "weread",
                                   weread_days(buckets))
    # 两级上限：maxBackfill 管「一次最多发几天」，ledgerDays 管「积压多久后彻底放弃」。
    # prune 会把 ledgerDays 天以外的账本条目直接丢掉 —— 那批永远不会被补发，
    # 也没有任何提示（静默上限），所以这两级不是一回事。
    ledger_data = lg.prune(ledger_data, int(cfg["ledgerDays"]), today)

    today_sources = dict(ledger_data.get(today) or {})
    owned = {c["id"] for c in old.get("collected") or []}
    awarded_dates = {c.get("date") for c in old.get("collected") or [] if c.get("date")}

    print(f"今天 {today}   各源原始量 {today_sources or '（无）'}   "
          f"额度 {src.day_credit(cfg, today_sources)}/{cfg['perAward']}")
    print(f"已收集 {len(owned)}/{pokedex_total()}   "
          f"连读 {dc.compute_streak(sorted(awarded_dates), today)} 天")

    pending = dc.pending_days(cfg, ledger_data, awarded_dates)
    if len(pending) > int(cfg["maxBackfill"]):
        skipped = pending[int(cfg["maxBackfill"]):]
        pending = pending[:int(cfg["maxBackfill"])]
        print(f"[跳过] 超过 maxBackfill，这些天先不发：{', '.join(skipped)}")

    if args.force and today not in awarded_dates and today not in pending:
        pending = pending + [today]
        pending.sort()

    if not pending:
        if today in awarded_dates:
            print(f"今天已经发过奖了   图鉴 {len(owned)}/{pokedex_total()}")
        else:
            print("未达标，今天没有宝可梦。")
        if not args.dry_run:
            save_state(dc.build_state(old, cfg, today, today_sources,
                                      ledger_data, chains, []))
        return 0

    pool = load_pokedex()
    new_awards = []
    for day in pending:
        streak = dc.compute_streak(sorted(awarded_dates | {day}), day)
        award = dc.pick(owned, pool, streak, random.Random())
        if award is None:
            print(f"池子空了 —— {pokedex_total()} 只全部收集完成！")
            break

        # 链养成：登记「今天抽到了这条链」，再判断保底能不能兑现
        chain_state = dc.advance_chain(chains, award, day)
        if chain_state and dc.chain_shiny_ready(chain_state, day):
            award["shiny"] = True
            chain_state["lastShinyDate"] = day
            print(f"[保底] {dc.chain_key(award)} 养到 "
                  f"{dc.chain_care(chain_state, day)} 天，这次必闪")

        day_credits = src.credit_for_day(cfg, ledger_data.get(day) or {})
        contributors = [sid for sid, n in day_credits.items() if n > 0]
        award_full = {
            "id": award["id"],
            "nameZh": award["nameZh"],
            "shiny": bool(award["shiny"]),
            "at": now.isoformat(),
            "date": day,
            "sources": contributors,
            "form": award["id"],
        }
        tag = "✦ 闪光" if award["shiny"] else "●"
        prefix = f"补发 {day} " if day != today else ""
        print(f"★ {prefix}获得 {tag} {award['nameZh']}（No.{award['id']}）"
              f"   连读 {streak} 天")

        new_awards.append(award_full)
        owned.add(award["id"])
        awarded_dates.add(day)

    state = dc.build_state(old, cfg, today, today_sources, ledger_data,
                           chains, new_awards)

    if args.dry_run:
        print("[dry-run] 未写入文件")
        return 0

    save_state(state)
    print(f"已写入 {DEX_PATH}   图鉴 {len(state['collected'])}/{pokedex_total()}   "
          f"连读 {state['streak']} 天")

    if new_awards and not args.no_push:
        last = new_awards[-1]
        tag = "✦ 闪光" if last["shiny"] else ""
        try:
            push_notify.send(
                f"{tag} 捕获 {last['nameZh']}",
                f"今日额度 {state['today']['credit']}/{cfg['perAward']} · "
                f"连读 {state['streak']} 天 · "
                f"图鉴 {len(state['collected'])}/{pokedex_total()}",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[推送] 跳过: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
