#!/usr/bin/env python3
"""Issue 投递箱接收端：把客户端报上来的行为量并进账本。

    python3 scripts/collect_reports.py            # 读 issue → 校验 → 并账本
    python3 scripts/collect_reports.py --dry-run  # 只打印，不写盘不评论不关闭

**为什么是 Issue**：GitHub Pages 是纯静态站，只能读不能收 POST；
而仓库 `lilia-shound/pokemon-dex` 是 public、issues 已开，cron 里本来就有
`GITHUB_TOKEN` —— 零注册、零基建、零费用的一条「客户端能匿名投递、服务端能读回」
的通道。客户端只负责打开一个预填好的 New Issue 页面并让人点 Submit。

**信任边界**（公开仓库里任何人都能创建 issue，所以这条通道天然「任何人可写」）：

1. `author_association` 白名单 —— 只有仓库管理员的 issue 被采纳
2. 数值上限 —— 单源单日的量不超过该源的 `maxPerDay`（番茄钟 ≤ 1440 分钟）
3. 取 max —— 同一源同一天重复投递取最大值，不叠加

残余风险：仓库管理员本人可以伪造数据让图鉴多掉几只宝可梦。
这属于自伤范围（dex.json 本来就是公开的），可接受。

**语义**：`days[day]` = 该源**当天累计总量**（不是增量）。
服务端取 max，所以重复投递是幂等的 —— 客户端报总量才安全，
报增量的话第二次同步会被 max 吃掉。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import ledger as lg  # noqa: E402
from scripts import sources as src  # noqa: E402

DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
SOURCES_PATH = pathlib.Path(os.environ.get("SOURCES_PATH", ROOT / "data" / "sources.json"))
DEFAULT_REPO = "lilia-shound/pokemon-dex"
API = "https://api.github.com"

# issue 标题前缀：只有带它的 issue 才会被当成报告
REPORT_TITLE_PREFIX = "[report]"
# 报告格式版本
REPORT_VERSION = 1
# 可以被采纳的 issue 作者身份
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
# 单次投递最多带多少天（与注册表的 ledgerDays 对齐）
MAX_REPORT_DAYS = 30


def _is_int(v) -> bool:
    """是不是整数。**bool 不算** —— Python 里 True 是 int 的子类。"""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_date(s) -> bool:
    """是不是合法的 YYYY-MM-DD 日期字符串。

    `date.fromisoformat` 在 Python 3.11+ 接受「20261118」这种基本格式，
    所以额外比一次 `isoformat()` 的结果，保证是带横杠的十进制写法。
    """
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return d.isoformat() == s


def parse_report(body: str, cfg: dict, today: str) -> tuple[dict[str, int], str | None]:
    """解析一条报告的 body。

    成功返回 `(days, None)`，失败返回 `({}, 原因)`。
    **任何一条校验不过就丢弃整个报告**（不部分采纳）——
    一条坏数据不该只污染一半，另一半照常处理。

    校验清单：
      1. body 是 JSON 对象
      2. `v` == 1
      3. `source` 在注册表里、`mode == "push"`、`enabled` 为真
      4. `days` 是对象，条数 ≤ 30
      5. 每个 key 是合法 YYYY-MM-DD，且不晚于今天
      6. 每个 value 是非负整数
      7. 每个 value ≤ 该源的 `maxPerDay`
    """
    if not isinstance(body, str) or not body.strip():
        return {}, "报告内容为空"
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        return {}, f"body 不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return {}, "body 不是 JSON 对象"

    version = data.get("v")
    if version != REPORT_VERSION:
        return {}, f"版本不对：期望 v={REPORT_VERSION}，收到 {version!r}"

    source_id = data.get("source")
    if not isinstance(source_id, str) or not source_id:
        return {}, f"source 不是源 id：{source_id!r}"
    source = (cfg.get("sources") or {}).get(source_id)
    if source is None:
        return {}, f"注册表里没有这个源：{source_id}"
    if source.get("mode") != "push":
        return {}, f"源 {source_id} 不是 push 源，不能靠 issue 上报"
    if not source.get("enabled"):
        return {}, f"源 {source_id} 当前是停用的"

    days = data.get("days")
    if not isinstance(days, dict):
        return {}, f"days 不是对象：{days!r}"
    if not days:
        return {}, "报告里没有天数"
    if len(days) > MAX_REPORT_DAYS:
        return {}, f"一次最多带 {MAX_REPORT_DAYS} 天，收到 {len(days)} 天"

    cap = source.get("maxPerDay")
    out: dict[str, int] = {}
    for day, amount in days.items():
        if not _is_date(day):
            return {}, f"日期格式不对：{day!r}（要 YYYY-MM-DD）"
        if day > today:
            return {}, f"日期在未来：{day}"
        if not _is_int(amount):
            return {}, f"{day} 的量不是整数：{amount!r}"
        if amount < 0:
            return {}, f"{day} 的量是负数：{amount}"
        if cap is not None and amount > int(cap):
            return {}, f"{day} 的量 {amount} 超过 {source_id} 的上限 {cap}"
        out[day] = int(amount)
    return out, None


# ── 编排层 ──────────────────────────────────────────────────────────────
# 下面这层不碰网络（process_issues 是纯函数），网络只在最外面薄薄一层。


def _source_of(body: str) -> str:
    """取出报告的源 id。**只在 parse_report 已经通过之后调用**（那时 body 保证可解析）。"""
    return str(json.loads(body)["source"])


def is_trusted(issue: dict) -> bool:
    """issue 作者是否可信。只有仓库管理员的 issue 会被采纳。"""
    return (issue.get("author_association") or "") in TRUSTED_ASSOCIATIONS


def process_issues(issues: list[dict], cfg: dict, today: str,
                   ledger: dict) -> tuple[dict, list[dict]]:
    """把一批 issue 处理掉，返回 `(新账本, 处理结果列表)`。

    处理结果每条：
      {"number": int|None, "action": str, "reason": str|None, "source": str|None,
       "days": dict, "credits": dict}

    action 的取值：
      - `skipped`   标题不是报告（不是给我们看的）
      - `untrusted` 作者身份不在白名单 —— **不关闭**，留给人工看
      - `rejected`  报告没过校验 —— **不关闭**，留给人工看
      - `accepted`  采纳，量已并进账本

    合并用 `ledger.merge_amounts`（同源同一天取 max）⇒ 重复投递是幂等的。
    """
    new_ledger = ledger
    results: list[dict] = []
    for issue in issues:
        number = issue.get("number")
        title = issue.get("title") or ""
        if not title.startswith(REPORT_TITLE_PREFIX):
            results.append({"number": number, "action": "skipped",
                            "reason": "标题不是报告", "source": None,
                            "days": {}, "credits": {}})
            continue
        if not is_trusted(issue):
            results.append({"number": number, "action": "untrusted",
                            "reason": f"作者身份 {issue.get('author_association')!r} 不可信",
                            "source": None, "days": {}, "credits": {}})
            continue
        body = issue.get("body") or ""
        days, err = parse_report(body, cfg, today)
        if err:
            results.append({"number": number, "action": "rejected",
                            "reason": err, "source": None, "days": {}, "credits": {}})
            continue
        source = _source_of(body)
        new_ledger = lg.merge_amounts(new_ledger, source, days)
        # 每个日期按该源的口径折算成额度（其他源没量，贡献 0）
        credits = {d: src.day_credit(cfg, {source: v}) for d, v in days.items()}
        unit = (cfg.get("sources") or {}).get(source, {}).get("unit", "")
        results.append({"number": number, "action": "accepted", "reason": None,
                        "source": source, "days": days, "credits": credits,
                        "unit": unit})
    return new_ledger, results


# ── 网络层 ──────────────────────────────────────────────────────────────


def _api(token: str, method: str, url: str, payload: dict | None = None):
    """调 GitHub API。返回 (status, 解析后的 JSON 或原始文本)。"""
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "pokemon-dex-collect-reports")
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def filter_report_issues(items: list) -> list[dict]:
    """从 issues 接口的返回里挑出报告。

    issues 接口会把 PR 一起返回（PR 在 GitHub 眼里也是 issue）
    ⇒ 带 `pull_request` 字段的跳过；标题不以 `[report]` 开头的也跳过。
    """
    out = []
    for item in items or []:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        if not (item.get("title") or "").startswith(REPORT_TITLE_PREFIX):
            continue
        out.append(item)
    return out


def list_report_issues(token: str, repo: str) -> list[dict]:
    """拉 open issue，只留标题以 `[report]` 开头的。"""
    url = f"{API}/repos/{repo}/issues?state=open&per_page=100"
    status, data = _api(token, "GET", url)
    if status != 200:
        raise RuntimeError(f"拉 issue 失败：HTTP {status} {data}")
    if not isinstance(data, list):
        raise RuntimeError(f"拉 issue 返回的不是列表：{data}")
    return filter_report_issues(data)


def comment_and_close(token: str, repo: str, number: int, note: str) -> None:
    """在 issue 下留一条评论并关闭它。"""
    base = f"{API}/repos/{repo}/issues/{number}"
    _api(token, "POST", f"{base}/comments", {"body": note})
    _api(token, "PATCH", base, {"state": "closed"})


def _note(result: dict) -> str:
    """写给 issue 里的评论。"""
    days = result.get("days") or {}
    credits = result.get("credits") or {}
    unit = result.get("unit") or ""
    total = sum(credits.values())
    return (f"已采纳：{len(days)} 天的量（{result['source']}），"
            f"该源口径共折算 {total} 个额度。\n\n"
            + "\n".join(f"- {d}：{v} {unit}" for d, v in sorted(days.items())))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 Issue 投递箱里的报告并进账本")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘不评论不关闭")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库（owner/name）")
    ap.add_argument("--date", default=None, help="今天（YYYY-MM-DD），默认取北京时间今天")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[失败] 环境里没有 GITHUB_TOKEN，没法读 issue", file=sys.stderr)
        return 1

    today = args.date or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")

    try:
        cfg = src.load_config(SOURCES_PATH)
    except src.SourceConfigError as exc:
        print(f"[失败] 源注册表有问题：{exc}", file=sys.stderr)
        return 1

    if not DEX_PATH.exists():
        print(f"[失败] 找不到状态文件 {DEX_PATH}", file=sys.stderr)
        return 1
    state = json.loads(DEX_PATH.read_text(encoding="utf-8"))
    ledger = state.get("ledger") or {}

    try:
        issues = list_report_issues(token, args.repo)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 拉 issue 失败：{exc}", file=sys.stderr)
        return 1
    print(f"发现 {len(issues)} 条报告 issue（今天 {today}）")

    new_ledger, results = process_issues(issues, cfg, today, ledger)

    accepted = [r for r in results if r["action"] == "accepted"]
    for r in results:
        if r["action"] == "accepted":
            print(f"[采纳] #{r['number']} {r['source']} {r['days']} → 额度 {r['credits']}")
        elif r["action"] in ("untrusted", "rejected"):
            print(f"[跳过] #{r['number']} {r['reason']}（不关闭，留给人工看）",
                  file=sys.stderr)

    if not accepted:
        print("没有可采纳的报告，账本没变")
        return 0

    if args.dry_run:
        print("[dry-run] 不写盘、不评论、不关闭")
        return 0

    if new_ledger != ledger:
        state["ledger"] = new_ledger
        DEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEX_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"账本已更新：{len(new_ledger)} 天")

    for r in accepted:
        try:
            comment_and_close(token, args.repo, r["number"], _note(r))
            print(f"[已关闭] #{r['number']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 关不掉 #{r['number']}：{exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
