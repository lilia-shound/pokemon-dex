#!/usr/bin/env python3
"""免登录投递通道（Cloudflare Worker）的服务端接收端。

    python3 scripts/collect_worker.py            # 拉队列 → 校验 → 并账本 → ack
    python3 scripts/collect_worker.py --dry-run  # 只打印，不写盘不 ack

**为什么多这一条通道**：push 源（番茄钟 / 背单词 / KOReader）原来的投递方式是
「客户端打开 GitHub 新建 issue 页，人点 Submit」—— 要登录、要点两次。
Worker 通道让客户端**点一下就完事**，服务端照样只认自己算出来的账。

**信任边界**：ingest token 写在她网站公开的 JS 里 ⇒ 这条通道天生「任何人可写」。
所以：
  1. Worker 侧限幅（源白名单 / 单日上限 / ≤30 天 / 队列 ≤200 条）
  2. 服务端**再校验一遍**（复用 collect_reports.parse_report —— 与 Issue 通道同构）
  3. 并账本取 max（同源同一天）⇒ 重复投递幂等
  4. 结算规则不变（perAward=2）⇒ 一天最多掉 1 只，被刷最多让图鉴走得快一点

**结算核心一行不改** —— 这里只负责「把远端数据变成账本里的量」。

**这条通道是「旁路」**：没配置 / 拉不到 ⇒ 打印一句就 exit 0，
**绝不阻塞发奖**（发奖那一步不依赖它）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import collect_reports as cr  # noqa: E402
from scripts import ledger as lg  # noqa: E402
from scripts import sources as src  # noqa: E402

DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
SOURCES_PATH = pathlib.Path(os.environ.get("SOURCES_PATH", ROOT / "data" / "sources.json"))

# 拉队列与 ack 的超时（秒）。★ 必须设 —— cron 不该被一个不响应的 Worker 挂住。
# ★ 60 而不是 120：**2026-11-26 实测，这一路的失败形态不是「慢」而是「响应丢了」** ——
#   服务端把活干完了（ack 的三个 key 确实被删了），但响应没回到沙箱 ⇒
#   客户端只能等满超时。**超时定多少，失败一次的代价就是多少** ⇒
#   60 秒 × 3 次（最坏 3 分钟）比 120 秒 × 2 次（最坏 4 分钟）多一次机会、还少等一分钟。
# ★ 成功的那些请求是 2~21 秒（首包/冷启动 7~45 秒）⇒ 60 秒够用。
TIMEOUT = 60

# 拉队列的重试次数与间隔（秒）。
# ★ 实测这条链路会抖（TLS 掐断、冷启动慢、响应丢失）⇒ 一次失败就放弃，
#   意味着这一轮的报告要等下一轮才被拉走。
# ★ **2026-11-26 实测**：两次拉取里，第一次挂满超时、第二次 15 秒就成了；
#   另一次两次都挂满 ⇒ 2 次尝试**不够** ⇒ 提到 3 次。
# ★ 重试是安全的：/pull 是**只读**的（不删队列），响应丢了只是这轮没读到，
#   下轮重读一遍就行（并账取 max ⇒ 幂等，不会多算）。
PULL_ATTEMPTS = 3
PULL_RETRY_PAUSE = 3

# ack 的超时（秒）与重试次数。
# ★ ack 用更短的预算：它失败**不致命** —— 条目还在队列里，下轮会重拉、重并账（取 max，幂等），
#   再 ack 一次。实测 ack 的响应也经常丢（服务端删完了、响应没回来）⇒ 重试照样安全。
#   但没必要为它等太久 ⇒ 45 秒 × 2 次就收手，别把一整轮 cron 的时间都耗在 ack 上。
ACK_TIMEOUT = 45
ACK_ATTEMPTS = 2

# Cloudflare 前面会按「浏览器签名」封掉 urllib 的默认 UA（`Python-urllib/x.y`）
# ⇒ 报 `HTTP 403 error code: 1010`。
# ★ 与 scripts/notify.py 里给 Resend 加 UA 是**同一件事**（Resend 也在 Cloudflare 后面）。
# ⇒ 这里也必须带一个像浏览器的 UA，否则 cron 每轮都拉不到队列（而它是旁路 ⇒ 只会静默失败）。
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _http(url: str, method: str = "GET", payload=None, headers=None, timeout: int = 30):
    """发一个 HTTP 请求，返回 (status, 解析后的 JSON 或原始文本)。

    连不上时返回 `(0, 原因字符串)` —— **不抛异常**，
    好让上层把「通道不通」和「数据有问题」分开说。

    ★ 一定会带上 `BROWSER_UA`（调用方没给 User-Agent 时）——
      Cloudflare 会按浏览器签名封掉 urllib 的默认 UA ⇒ 403 / error code 1010。
    """
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if not any(k.lower() == "user-agent" for k in hdrs):
        hdrs["User-Agent"] = BROWSER_UA
    req = urllib.request.Request(url, method=method, data=data)
    for k, v in hdrs.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 —— 连不上/超时/证书…全归一类
        return 0, str(exc)


def pull_items(url: str, admin_token: str, timeout: int = TIMEOUT) -> list[dict]:
    """从 Worker 拉待处理的队列。**拉不到就抛异常**（调用方负责跳过）。"""
    status, data = _http(url.rstrip("/") + "/pull", "GET",
                         headers={"X-Dex-Admin": admin_token}, timeout=timeout)
    if status != 200:
        raise RuntimeError("拉队列失败：HTTP %s %s" % (status, data))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RuntimeError("拉队列返回的不是预期形状：%s" % data)
    return data["items"]


def ack_items(url: str, admin_token: str, ids, timeout: int = ACK_TIMEOUT) -> int:
    """确认已并账，把这些条目从队列里删掉。返回**真删掉**的条数。

    ★ 用服务端回的 `removed`，不用「我们请求删了几条」——
      Worker 那边是「先 get 确认它在，再 delete」⇒ `removed` 才是真删掉的数。
      两者可能不等（条目已过期、或上一轮已 ack 过）⇒ 报真数字，
      否则日志里的「已 ack N 条」会虚高，看不出「其实一条都没删掉」。
    """
    ids = [i for i in (ids or []) if isinstance(i, str) and i]
    if not ids:
        return 0
    status, data = _http(url.rstrip("/") + "/ack", "POST", payload={"ids": ids},
                         headers={"X-Dex-Admin": admin_token}, timeout=timeout)
    if status != 200:
        raise RuntimeError("ack 失败：HTTP %s %s" % (status, data))
    removed = data.get("removed") if isinstance(data, dict) else None
    if isinstance(removed, int) and not isinstance(removed, bool):
        return int(removed)
    return len(ids)


def _pull_with_retry(url: str, admin_token: str) -> list[dict]:
    """拉队列，失败重试一次。

    ★ 为什么要有重试：这条链路在国内会抖（TLS 被掐、冷启动慢、KV 最终一致）。
      一次失败就放弃 ⇒ 这一轮的报告要等 3 小时后的下一轮才被拉走 ⇒ 她会以为「交了没动静」。
    ★ 只重试**网络类**失败（status 0 / 超时 / 5xx）；凭证不对（401）不重试 —— 重试也没用。
    """
    last_exc = None
    for attempt in range(PULL_ATTEMPTS):
        try:
            return pull_items(url, admin_token, timeout=TIMEOUT)
        except RuntimeError as exc:
            last_exc = exc
            # 凭证不对 ⇒ 重试没有意义，直接放弃。
            if "HTTP 401" in str(exc):
                raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if attempt + 1 < PULL_ATTEMPTS:
            time.sleep(PULL_RETRY_PAUSE)
    raise last_exc  # type: ignore[misc]


def _ack_with_retry(url: str, admin_token: str, ids) -> int:
    """ack 队列条目，失败重试一次。

    ★ 为什么要重试：这条链路会抖（TLS 被掐、首包慢）。
      ack 失败的后果不严重（条目还在队列里 ⇒ 下轮会重拉 ⇒ 并账取 max ⇒ 不会多算），
      但它会让队列**一直有残留** ⇒ 每轮都白拉一遍。
      ack 天然幂等（删了就是删了）⇒ 重试安全。
    """
    last_exc = None
    for attempt in range(ACK_ATTEMPTS):
        try:
            return ack_items(url, admin_token, ids, timeout=ACK_TIMEOUT)
        except RuntimeError as exc:
            last_exc = exc
            if "HTTP 401" in str(exc):
                raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if attempt + 1 < ACK_ATTEMPTS:
            time.sleep(PULL_RETRY_PAUSE)
    raise last_exc  # type: ignore[misc]


def process_items(items, cfg: dict, today: str, ledger: dict):
    """把一批队列条目处理掉，返回 `(新账本, 结果列表)`。

    **纯函数**（不碰网络、不碰文件）⇒ 可以直接单测。

    结果每条：
      {"id": str|None, "action": "accepted"|"rejected", "reason": str|None,
       "source": str|None, "days": dict, "credits": dict}

    合并用 `ledger.merge_amounts`（同源同一天取 max）⇒ 重复投递是幂等的。
    """
    new_ledger = ledger
    results = []
    for item in items or []:
        iid = item.get("id") if isinstance(item, dict) else None
        body = item.get("body") if isinstance(item, dict) else None
        raw = json.dumps(body, ensure_ascii=False) if body is not None else ""
        days, err = cr.parse_report(raw, cfg, today)
        if err:
            # 校验不过 ⇒ 不并账。**但仍然会被 ack 掉**（主流程负责）——
            # Worker 已经限过幅，这里失败说明两边规则漂了，要修的是代码，
            # 留着它只会让每轮都拉一遍、拉不动的噪音。
            results.append({"id": iid, "action": "rejected", "reason": err,
                            "source": None, "days": {}, "credits": {}})
            continue
        source = body.get("source")
        new_ledger = lg.merge_amounts(new_ledger, source, days)
        credits = {d: src.day_credit(cfg, {source: v}) for d, v in days.items()}
        results.append({"id": iid, "action": "accepted", "reason": None,
                        "source": source, "days": days, "credits": credits})
    return new_ledger, results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从免登录投递通道（Worker）收报告并进账本")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘不 ack")
    ap.add_argument("--date", default=None, help="今天（YYYY-MM-DD），默认取北京时间今天")
    args = ap.parse_args(argv)

    url = os.environ.get("WORKER_URL")
    admin_token = os.environ.get("WORKER_ADMIN_TOKEN")
    if not url or not admin_token:
        print("[投递] 未配置 WORKER_URL / WORKER_ADMIN_TOKEN，跳过")
        return 0

    today = args.date or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

    try:
        cfg = src.load_config(SOURCES_PATH)
    except src.SourceConfigError as exc:
        print("[投递] 源注册表有问题：%s" % exc, file=sys.stderr)
        return 1

    if not DEX_PATH.exists():
        print("[投递] 找不到状态文件 %s" % DEX_PATH, file=sys.stderr)
        return 1

    try:
        items = _pull_with_retry(url, admin_token)
    except Exception as exc:  # noqa: BLE001
        # ★ 拉不到就跳过 —— **绝不阻塞发奖**（这是旁路）。
        print("[投递] 拉取失败：%s，跳过" % exc, file=sys.stderr)
        return 0

    if not items:
        print("[投递] 队列是空的")
        return 0

    state = json.loads(DEX_PATH.read_text(encoding="utf-8"))
    ledger = state.get("ledger") or {}
    new_ledger, results = process_items(items, cfg, today, ledger)

    accepted = [r for r in results if r["action"] == "accepted"]
    for r in results:
        if r["action"] == "accepted":
            print("[投递] 采纳 %s %s %s → 额度 %s"
                  % (r["id"], r["source"], r["days"], r["credits"]))
        else:
            print("[投递] 拒收 %s %s" % (r["id"], r["reason"]), file=sys.stderr)

    if not accepted:
        print("[投递] 没有可采纳的报告，账本没变")
        # 非法条目也要 ack 掉（别让每轮都拉同一批垃圾）
        if not args.dry_run:
            try:
                removed = _ack_with_retry(url, admin_token,
                                          [r["id"] for r in results])
                print("[投递] 已 ack %s 条" % removed)
            except Exception as exc:  # noqa: BLE001
                print("[投递] ack 失败：%s" % exc, file=sys.stderr)
        return 0

    if args.dry_run:
        print("[dry-run] 不写盘、不 ack")
        return 0

    if new_ledger != ledger:
        state["ledger"] = new_ledger
        DEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEX_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print("账本已更新：%s 天" % len(new_ledger))

    try:
        removed = _ack_with_retry(url, admin_token, [r["id"] for r in results])
        print("[投递] 已 ack %s 条" % removed)
    except Exception as exc:  # noqa: BLE001
        # ack 失败不要紧：条目还在队列里，下轮会重拉 ⇒ 并账取 max ⇒ 幂等不多算。
        print("[投递] ack 失败（下轮会重拉，账本取 max 不会多算）：%s" % exc, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
