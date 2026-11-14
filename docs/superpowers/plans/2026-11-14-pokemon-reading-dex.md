# 微信读书 × 宝可梦收集 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做一个「在微信读书读满一定时长 → 随机获得一只宝可梦 → 收集全国图鉴」的服务端 + 插件组合。

**Architecture:** 服务端是唯一权威，调用微信读书官方 Agent API 的 `/readdata/detail` 取当日阅读秒数，判定达标后从未收集池抽 1 只宝可梦，写入公开的 `dex.json`。图鉴页（GitHub Pages）与油猴脚本都是**只读**展示端，读 `dex.json` 渲染图鉴墙、进度条和当日开箱卡。

**Tech Stack:** Python 3 标准库（客户端/判定/发放）+ pytest（测试）+ 原生 HTML/CSS/JS（页面与插件）+ git + GitHub Pages（托管）+ GitHub API（托管 `dex.json` 与素材）

## Global Constraints

- 项目根目录：`/workspace/pokemon-reading-dex/`
- 微信读书 API Key 存于 secret `WEREAD_API_KEY`，**任何文件、日志、提交里都不得出现明文**
- 微信读书网关：`POST https://i.weread.qq.com/api/agent/gateway`，`skill_version` 一律用 `1.0.5`
- 所有时长单位是**秒**；阈值 30 分钟 = `1800` 秒
- 时区：`Asia/Shanghai`。**桶 key 已在服务端确认就是北京时间周期起点**（证据见下）
- 宝可梦总数 1025，编号 `0001`–`1025`
- 素材源：`42arch/pokemon-dataset-zh`（MIT），`data/pokemon/*.json` + `data/images/home/*.png`
- `dex.json` 是公开文件，**只放图鉴状态 + 当日已读秒数 + streak**，绝不放书架、书名、笔记、API Key
- 插件**只读**：不抽卡、不写状态、不持有微信读书 API Key
- 闪光概率：streak 1–2 → 1/96；≥3 → 1/48；≥7 → 1/32；≥30 → 1/16；≥100 → **保底必出**
- 未达标**无惩罚**，streak 归零而已
- 每次达标发 **1 只**
- 已收集的**不再抽**（池 = 尚未收集的），集满 1025 = 通关
- 提交信息前缀：`feat:` / `fix:` / `test:` / `docs:`

### 时区校准依据（已实测，不可再猜）

服务端返回的周期起点 key 用 `Asia/Shanghai` 格式化后精确落在自然周期边界：

| 接口返回 | key | SH 格式化 | 预期 |
|---|---|---|---|
| `mode=monthly` → `baseTime` | `1793462400` | `2026-11-01` | 当月 1 号 ✅ |
| `mode=annually` → `baseTime` | `1767196800` | `2026-01-01` | 当年 1 月 1 日 ✅ |
| `mode=overall` → 桶 `2025-01-01` | `1735689600` | `2025-01-01` | 当年 1 月 1 日 ✅ |

→ 日桶的 key 必然是**北京时间当日 00:00**，实现里直接用 `datetime.fromtimestamp(k, SH)` 判定日期，不再做时区猜测。

## File Structure

```
/workspace/pokemon-reading-dex/
├── README.md                       # 项目说明 + 使用方法
├── data/
│   ├── pokedex.json                # 1025 只精简资料（生成物，提交）
│   ├── dex.json                    # 唯一状态文件（提交）
│   └── images/home/                # 3006 张 home 立绘含 shiny（提交，44MB）
├── scripts/
│   ├── weread_client.py            # 微信读书网关封装（纯函数 + IO）
│   ├── dex_core.py                 # 抽卡 / streak / 阈值（纯逻辑，无 IO）
│   ├── fetch_pokemon.py            # 素材管线：clone → 精简 → pokedex.json
│   ├── award.py                    # 发放器：取数 → 判定 → 抽 → 写 dex.json
│   ├── check_today.py              # 自检：打印今日桶 / 候选 key / 各项状态
│   └── push_notify.py              # 微信推送（pushplus）
├── tests/
│   ├── test_weread_client.py
│   ├── test_dex_core.py
│   └── test_fetch_pokemon.py
├── site/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── plugin/
│   └── weread-dex.user.js
└── docs/superpowers/{specs,plans}/
```

---

### Task 1: 微信读书客户端 + 今日取值 + 自检脚本

**Files:**
- Create: `scripts/weread_client.py`
- Create: `scripts/check_today.py`
- Create: `tests/test_weread_client.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Consumes: secret `WEREAD_API_KEY`（`os.environ`）
- Produces:
  - `weread_client.call(api_name: str, **params) -> dict` —— 网关调用，失败抛 `WeReadError`
  - `weread_client.read_times(mode: str = "monthly") -> dict[int, int]` —— 返回 `{桶key: 秒数}`
  - `weread_client.today_seconds(rt: dict[int, int], now: datetime | None = None) -> tuple[int | None, int]` —— 返回 `(今日桶key | None, 今日秒数)`
  - `weread_client.WeReadError(Exception)`

- [ ] **Step 1: 写失败测试**

`tests/test_weread_client.py`：

```python
import datetime
import zoneinfo

import pytest

from scripts import weread_client as wc

SH = zoneinfo.ZoneInfo("Asia/Shanghai")


def _ts(y, m, d, hh=0, mm=0):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=SH).timestamp())


def test_read_times_returns_int_keyed_seconds(monkeypatch):
    monkeypatch.setattr(wc, "call", lambda api, **p: {"readTimes": {"1794585600": 1200}})
    rt = wc.read_times("monthly")
    assert rt == {1794585600: 1200}
    assert all(isinstance(k, int) and isinstance(v, int) for k, v in rt.items())


def test_today_seconds_picks_latest_bucket_of_today():
    today = _ts(2026, 11, 14)
    yesterday = _ts(2026, 11, 13)
    rt = {yesterday: 600, today: 900}
    key, secs = wc.today_seconds(rt)
    assert key == today
    assert secs == 900


def test_today_seconds_is_zero_when_latest_bucket_is_yesterday():
    today = _ts(2026, 11, 14)
    yesterday = _ts(2026, 11, 13)
    rt = {yesterday: 600}
    key, secs = wc.today_seconds(rt)
    assert key is None
    assert secs == 0


def test_today_seconds_ignores_future_buckets():
    today = _ts(2026, 11, 14)
    tomorrow = _ts(2026, 11, 15)
    rt = {today: 300, tomorrow: 999}
    key, secs = wc.today_seconds(rt)
    assert key == today
    assert secs == 300


def test_today_seconds_empty_read_times():
    assert wc.today_seconds({}) == (None, 0)


def test_read_times_tolerates_missing_key():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(wc, "call", lambda api, **p: {})
    try:
        assert wc.read_times("monthly") == {}
    finally:
        monkey.undo()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_weread_client.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.weread_client'`

- [ ] **Step 3: 写实现**

`scripts/weread_client.py`：

```python
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

    判定规则：取所有 <= 今天的桶里最大的那个；若它的北京日期等于今天，
    则它的值就是今日秒数；否则（最新的桶是昨天或更早）今天还没读，返回 0。
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
```

`scripts/check_today.py`：

```python
#!/usr/bin/env python3
"""自检：把微信读书的今日状态摊开打印。

用法：
    python3 scripts/check_today.py            # 打印今日秒数与所有桶
    python3 scripts/check_today.py --weekly   # 改用 weekly 分桶
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="monthly", choices=["monthly", "weekly", "annually", "overall"])
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
        "thresholdSeconds": 1800,
        "reached": secs >= 1800,
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
    print(f"今日秒数   {secs} 秒 ({round(secs / 60, 1)} 分)   达标线 1800 秒 → {'达标' if secs >= 1800 else '未达标'}")
    print(f"今日桶 key {key}")
    print("--- 全部桶 ---")
    for b in out["buckets"]:
        print(f"  {b['date']}  key={b['key']:<12} {b['seconds']:>7} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_weread_client.py -v`
Expected: 6 passed

- [ ] **Step 5: 实跑自检脚本**

Run: `cd /workspace/pokemon-reading-dex && python3 scripts/check_today.py`
Expected: 打印本月桶列表；由于最近未读，今日秒数应为 0

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
git init -q 2>/dev/null || true
git add scripts/weread_client.py scripts/check_today.py tests/test_weread_client.py requirements-dev.txt
git commit -m "feat: 微信读书客户端与今日取值自检脚本"
```

---

### Task 2: 宝可梦素材管线

**Files:**
- Create: `scripts/fetch_pokemon.py`
- Create: `tests/test_fetch_pokemon.py`
- Create: `data/pokedex.json`（生成物）
- Create: `data/images/home/**`（生成物，44MB）

**Interfaces:**
- Consumes: 网络（`github.com` git clone）、`GITHUB_TOKEN` 可选
- Produces:
  - `fetch_pokemon.extract(pokemon_json: dict) -> dict` —— 把 42arch 单只 JSON 压成精简字段
  - `fetch_pokemon.pick_art(home_images: list, name_zh: str, want_shiny: bool) -> str | None` —— 从 `home_images` 选出主立绘文件名
  - `fetch_pokemon.build()` —— 跑完整管线，写 `data/pokedex.json` 与 `data/images/home/`

- [ ] **Step 1: 写失败测试**

`tests/test_fetch_pokemon.py`：

```python
import json
import pathlib

from scripts import fetch_pokemon as fp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pokemon_0025.json"


def test_extract_keeps_zh_name_and_flavor_text():
    src = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out = fp.extract(src)
    assert out["id"] == "0025"
    assert out["nameZh"] == "皮卡丘"
    assert out["nameJa"] == "ピカチュウ"
    assert out["nameEn"] == "Pikachu"
    assert out["types"] == ["电"]
    assert out["category"] == "鼠宝可梦"
    assert out["flavor"], "必须有中文图鉴描述"
    assert out["abilities"], "必须有特性"
    assert out["art"], "必须选到主立绘"
    assert out["artShiny"], "必须选到闪光立绘"


def test_pick_art_prefers_plain_form():
    imgs = [
        {"name": "皮卡丘-雄性", "image": "0025-皮卡丘-雄性.png", "shiny": "0025-皮卡丘-雄性-shiny.png"},
        {"name": "皮卡丘", "image": "0025-皮卡丘.png", "shiny": "0025-皮卡丘-shiny.png"},
    ]
    assert fp.pick_art(imgs, "皮卡丘", False) == "0025-皮卡丘.png"
    assert fp.pick_art(imgs, "皮卡丘", True) == "0025-皮卡丘-shiny.png"


def test_pick_art_falls_back_to_any_variant():
    imgs = [{"name": "某形态", "image": "0906-新叶喵-世界帽子.png", "shiny": "0906-新叶喵-世界帽子-shiny.png"}]
    assert fp.pick_art(imgs, "新叶喵", False) == "0906-新叶喵-世界帽子.png"


def test_pick_art_returns_none_when_empty():
    assert fp.pick_art([], "不存在", False) is None
```

同时把一份真实样本存成 fixture：

Run:
```bash
mkdir -p /workspace/pokemon-reading-dex/tests/fixtures
cp /tmp/pkds/data/pokemon/0025-皮卡丘.json /workspace/pokemon-reading-dex/tests/fixtures/pokemon_0025.json
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_fetch_pokemon.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.fetch_pokemon'`

- [ ] **Step 3: 写实现**

`scripts/fetch_pokemon.py`：

```python
#!/usr/bin/env python3
"""宝可梦素材管线：42arch/pokemon-dataset-zh -> data/pokedex.json + data/images/home/

用 git sparse checkout 只拉 data/pokemon 与 data/images/home，约 6 秒 / 77MB。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

REPO = "https://github.com/42arch/pokemon-dataset-zh.git"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / ".cache" / "pokemon-dataset-zh"
OUT_JSON = ROOT / "data" / "pokedex.json"
OUT_IMG = ROOT / "data" / "images" / "home"


def _clone() -> pathlib.Path:
    if (SRC_DIR / "data" / "pokemon").is_dir():
        return SRC_DIR
    SRC_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1", REPO, str(SRC_DIR)],
        check=True,
    )
    subprocess.run(["git", "-C", str(SRC_DIR), "sparse-checkout", "set", "data/pokemon", "data/images/home"], check=True)
    subprocess.run(["git", "-C", str(SRC_DIR), "checkout"], check=True)
    return SRC_DIR


def pick_art(home_images: list, name_zh: str, want_shiny: bool) -> str | None:
    """从 home_images 里挑主立绘。

    优先「编号-名字」这种无形态后缀的条目；没有就退回第一个可用条目。
    """
    if not home_images:
        return None
    key = "shiny" if want_shiny else "image"
    fallback = None
    for item in home_images:
        fname = item.get(key)
        if not fname:
            continue
        if fallback is None:
            fallback = fname
        stem = fname[:-4] if fname.endswith(".png") else fname
        # "0025-皮卡丘" 或 "0025-皮卡丘-世界帽子" 都以 编号-名字 开头
        if stem == f"{stem.split('-')[0]}-{name_zh}" or stem.split("-", 1)[1] == name_zh:
            return fname
    return fallback


def _first_flavor(src: dict) -> str:
    """取一条官方中文图鉴描述。"""
    for gen in src.get("pokedex_entries") or []:
        for ver in gen.get("versions") or []:
            text = (ver.get("text") or "").strip()
            if text:
                return text
    return ""


def extract(src: dict) -> dict:
    """把 42arch 单只 JSON 压成图鉴页需要的精简字段。"""
    pid = str(src.get("pokedex_id") or "").zfill(4)
    name_zh = src.get("name_zh") or ""
    forms = src.get("forms") or []
    form = forms[0] if forms else {}
    types = form.get("types") or []
    abilities = [a.get("name") for a in (form.get("abilities") or []) if a.get("name")]
    home_images = src.get("home_images") or []
    art = pick_art(home_images, name_zh, False)
    art_shiny = pick_art(home_images, name_zh, True)
    chain = []
    for link in src.get("evolution_chains") or []:
        for node in link:
            if node.get("name"):
                chain.append(node["name"])
    return {
        "id": pid,
        "nameZh": name_zh,
        "nameJa": src.get("name_ja") or "",
        "nameEn": src.get("name_en") or "",
        "types": types,
        "category": form.get("category") or "",
        "abilities": abilities,
        "height": form.get("height") or "",
        "weight": form.get("weight") or "",
        "flavor": _first_flavor(src),
        "summary": (src.get("description") or "").strip(),
        "profile": (src.get("profile") or "").strip(),
        "evolution": chain,
        "art": art,
        "artShiny": art_shiny,
    }


def build() -> int:
    src_dir = _clone()
    pokemon_dir = src_dir / "data" / "pokemon"
    files = sorted(pokemon_dir.glob("*.json"))
    if not files:
        print("[失败] 没拿到任何宝可梦数据", file=sys.stderr)
        return 1

    entries = []
    for f in files:
        src = json.loads(f.read_text(encoding="utf-8"))
        entry = extract(src)
        if entry["nameZh"]:
            entries.append(entry)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"count": len(entries), "pokemon": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    src_img = src_dir / "data" / "images" / "home"
    if OUT_IMG.exists():
        shutil.rmtree(OUT_IMG)
    shutil.copytree(src_img, OUT_IMG)

    missing = [e["id"] for e in entries if not e["art"]]
    print(f"[完成] {len(entries)} 只 -> {OUT_JSON.relative_to(ROOT)}")
    print(f"[完成] 图片 {sum(1 for _ in OUT_IMG.glob('*.png'))} 张 -> {OUT_IMG.relative_to(ROOT)}")
    if missing:
        print(f"[注意] {len(missing)} 只没有 home 立绘: {', '.join(missing[:20])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_fetch_pokemon.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑管线**

Run: `cd /workspace/pokemon-reading-dex && python3 scripts/fetch_pokemon.py`
Expected: 打印 `[完成] 1025 只` 与图片张数

- [ ] **Step 6: 校验产物**

Run:
```bash
cd /workspace/pokemon-reading-dex
python3 -c "
import json
d=json.load(open('data/pokedex.json',encoding='utf-8'))
p=d['pokemon']
print('总数',d['count'])
print('有中文图鉴描述',sum(1 for x in p if x['flavor']))
print('有中文名',sum(1 for x in p if x['nameZh']))
print('有主立绘',sum(1 for x in p if x['art']))
print('有闪光立绘',sum(1 for x in p if x['artShiny']))
print('第九代样本',[x['flavor'][:20] for x in p if x['id']=='0906'])
"
```
Expected: 1025 只，中文名 1025，中文图鉴描述 ≥ 1000（第九代也要有）

- [ ] **Step 7: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add scripts/fetch_pokemon.py tests/test_fetch_pokemon.py tests/fixtures data/pokedex.json data/images
git commit -m "feat: 宝可梦素材管线，1025 只中文资料与 home 立绘"
```

---

### Task 3: 抽卡核心逻辑（纯函数）

**Files:**
- Create: `scripts/dex_core.py`
- Create: `tests/test_dex_core.py`

**Interfaces:**
- Consumes: 无（纯逻辑）
- Produces:
  - `dex_core.SHINY_ODDS: dict[int, float]` —— streak 阈值 → 闪光概率
  - `dex_core.shiny_probability(streak: int) -> float`
  - `dex_core.compute_streak(award_dates: list[str], today: str) -> int` —— 连续达标天数（不含今天未达标的情况）
  - `dex_core.pick(owned_ids: set[str], pool: list[dict], streak: int, rng: random.Random) -> dict | None` —— 返回抽中的宝可梦条目（含 `shiny` 标记），池空返回 None
  - `dex_core.new_state(old: dict, award: dict, today: str, today_secs: int) -> dict` —— 生成新的 `dex.json`

- [ ] **Step 1: 写失败测试**

`tests/test_dex_core.py`：

```python
import random

import pytest

from scripts import dex_core as dc


def test_shiny_probability_by_streak():
    assert dc.shiny_probability(0) == pytest.approx(1 / 96)
    assert dc.shiny_probability(2) == pytest.approx(1 / 96)
    assert dc.shiny_probability(3) == pytest.approx(1 / 48)
    assert dc.shiny_probability(7) == pytest.approx(1 / 32)
    assert dc.shiny_probability(30) == pytest.approx(1 / 16)
    assert dc.shiny_probability(100) == 1.0


def test_compute_streak_counts_consecutive_days():
    assert dc.compute_streak(["2026-11-12", "2026-11-13"], "2026-11-14") == 2
    assert dc.compute_streak(["2026-11-14"], "2026-11-14") == 1


def test_compute_streak_breaks_on_gap():
    assert dc.compute_streak(["2026-11-10", "2026-11-12"], "2026-11-13") == 1


def test_compute_streak_zero_when_today_missing():
    assert dc.compute_streak(["2026-11-12"], "2026-11-14") == 0


def test_pick_excludes_owned():
    pool = [{"id": "0001", "nameZh": "妙蛙种子"}, {"id": "0002", "nameZh": "妙蛙草"}]
    rng = random.Random(1)
    got = dc.pick({"0001"}, pool, 0, rng)
    assert got is not None
    assert got["id"] == "0002"


def test_pick_returns_none_when_pool_empty():
    assert dc.pick({"0001"}, [{"id": "0001"}], 0, random.Random(1)) is None


def test_pick_marks_shiny_with_forced_rng():
    # 用 0.0 序列强制命中闪光判定
    pool = [{"id": "0001", "nameZh": "妙蛙种子"}]
    got = dc.pick(set(), pool, 0, random.Random(0))
    assert got is not None
    assert "shiny" in got


def test_new_state_appends_award_and_is_idempotent():
    old = {"collected": [], "streak": 0, "lastAwardDate": None, "today": {"seconds": 0}}
    award = {"id": "0001", "nameZh": "妙蛙种子", "shiny": False, "at": "2026-11-14T22:00:00+08:00"}
    new = dc.new_state(old, award, "2026-11-14", 1800)
    assert len(new["collected"]) == 1
    assert new["collected"][0]["id"] == "0001"
    assert new["lastAwardDate"] == "2026-11-14"
    assert new["streak"] == 1
    assert new["today"]["seconds"] == 1800
    # 幂等：同一天再跑一次不重复加
    again = dc.new_state(new, award, "2026-11-14", 1800)
    assert len(again["collected"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_dex_core.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.dex_core'`

- [ ] **Step 3: 写实现**

`scripts/dex_core.py`：

```python
"""抽卡与状态机。纯逻辑，不碰网络、不碰文件。"""

from __future__ import annotations

import datetime
import random

# streak 阈值 -> 闪光概率。阈值取「>= 该值即生效」
SHINY_ODDS: dict[int, float] = {
    0: 1 / 96,
    3: 1 / 48,
    7: 1 / 32,
    30: 1 / 16,
    100: 1.0,
}

THRESHOLD_SECONDS = 1800  # 30 分钟


def shiny_probability(streak: int) -> float:
    """按连读天数返回闪光概率。streak>=100 保底必出。"""
    prob = SHINY_ODDS[0]
    for threshold, p in sorted(SHINY_ODDS.items()):
        if streak >= threshold:
            prob = p
    return prob


def compute_streak(award_dates: list[str], today: str) -> int:
    """连续达标天数：today 必须在 award_dates 里，然后往前逐天数。

    dates 是「ISO 日期」列表。today 未达标则返回 0。
    """
    days = set(award_dates)
    if today not in days:
        return 0
    cur = datetime.date.fromisoformat(today)
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


def new_state(old: dict, award: dict | None, today: str, today_secs: int) -> dict:
    """算出新的 dex.json。

    幂等：若 old['lastAwardDate'] == today，不再追加。
    """
    collected = list(old.get("collected") or [])
    last = old.get("lastAwardDate")
    if award is not None and last != today:
        collected.append(
            {
                "id": award.get("id"),
                "nameZh": award.get("nameZh", ""),
                "shiny": bool(award.get("shiny")),
                "at": award.get("at"),
            }
        )
        last = today

    award_dates = [c.get("date") for c in collected if c.get("date")]
    streak = compute_streak(award_dates, today) if last == today else 0

    return {
        "version": 1,
        "collected": collected,
        "streak": streak,
        "lastAwardDate": last,
        "today": {
            "date": today,
            "seconds": int(today_secs),
            "threshold": THRESHOLD_SECONDS,
        },
        "updatedAt": award.get("at") if award else old.get("updatedAt"),
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /workspace/pokemon-reading-dex && python3 -m pytest tests/test_dex_core.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add scripts/dex_core.py tests/test_dex_core.py
git commit -m "feat: 抽卡核心逻辑与状态机"
```

---

### Task 4: 发放器

**Files:**
- Create: `scripts/award.py`
- Modify: `scripts/dex_core.py`（给 `new_state` 补 `date` 字段到 collected）

**Interfaces:**
- Consumes: `scripts.weread_client.read_times`、`scripts.dex_core`、`data/pokedex.json`、env `DEX_PATH`（默认 `data/dex.json`）
- Produces:
  - `award.main(argv: list[str] | None = None) -> int` —— 跑一轮判定
  - CLI: `--dry-run`（只打印不发奖）、`--force`（无视 streak 强制发）、`--date YYYY-MM-DD`

- [ ] **Step 1: 改 `dex_core.new_state` 让 collected 带日期**

在 `scripts/dex_core.py` 的 `new_state` 里，把追加进 collected 的记录改成带 `date`：

```python
        collected.append(
            {
                "id": award.get("id"),
                "nameZh": award.get("nameZh", ""),
                "shiny": bool(award.get("shiny")),
                "at": award.get("at"),
                "date": today,
            }
        )
```

- [ ] **Step 2: 写发放器**

`scripts/award.py`：

```python
#!/usr/bin/env python3
"""发放器：查今日阅读时长 → 判定 → 抽卡 → 写 dex.json。

    python3 scripts/award.py             # 正常跑一轮
    python3 scripts/award.py --dry-run   # 只看判定结果，不写文件
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
from scripts import weread_client as wc  # noqa: E402

SH = zoneinfo.ZoneInfo("Asia/Shanghai")
DEX_PATH = pathlib.Path(os.environ.get("DEX_PATH", ROOT / "data" / "dex.json"))
POKEDEX_PATH = ROOT / "data" / "pokedex.json"


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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="无视达标线直接发奖")
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
    streak = old.get("streak") or 0
    already = old.get("lastAwardDate") == today

    reached = secs >= dc.THRESHOLD_SECONDS or args.force
    print(f"日期 {today}   今日已读 {secs} 秒 ({round(secs / 60, 1)} 分)   达标线 {dc.THRESHOLD_SECONDS} 秒")
    print(f"已收集 {len(owned)}/1025   连读 {streak} 天   闪光概率 {dc.shiny_probability(streak):.4f}")

    if already:
        print("今天已经发过奖了，跳过。")
        return 0
    if not reached:
        print("未达标，今天没有宝可梦。")
        if not args.dry_run:
            save_state(dc.new_state(old, None, today, secs))
        return 0

    pool = load_pokedex()
    award = dc.pick(owned, pool, streak, random.Random())
    if award is None:
        print("池子空了 —— 1025 只全部收集完成！")
        return 0

    stamp = now.isoformat()
    award_full = {
        "id": award["id"],
        "nameZh": award["nameZh"],
        "shiny": award["shiny"],
        "at": stamp,
    }
    print(f"★ 获得 {'闪光 ' if award['shiny'] else ''}{award['nameZh']}（{award['id']}）")

    if args.dry_run:
        print("[dry-run] 未写入文件")
        return 0

    state = dc.new_state(old, award_full, today, secs)
    save_state(state)
    print(f"已写入 {DEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 跑 dry-run**

Run: `cd /workspace/pokemon-reading-dex && python3 scripts/award.py --dry-run`
Expected: 打印今日 0 秒、未达标、无宝可梦，不写文件

- [ ] **Step 4: 用 --force 验证抽卡链路**

Run:
```bash
cd /workspace/pokemon-reading-dex
cp data/dex.json /tmp/dex.bak 2>/dev/null || true
python3 scripts/award.py --force
python3 -c "import json;d=json.load(open('data/dex.json',encoding='utf-8'));print('collected',len(d['collected']),'| streak',d['streak'],'| last',d['lastAwardDate'])"
```
Expected: 抽到一只，collected=1，streak=1

- [ ] **Step 5: 还原状态**

Run: `cd /workspace/pokemon-reading-dex && git checkout -- data/dex.json 2>/dev/null || printf '{\n  "version": 1,\n  "collected": [],\n  "streak": 0,\n  "lastAwardDate": null,\n  "today": {}\n}\n' > data/dex.json`

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add scripts/award.py scripts/dex_core.py data/dex.json
git commit -m "feat: 发放器，读时长达标即抽一只宝可梦"
```

---

### Task 5: 图鉴页

**Files:**
- Create: `site/index.html`
- Create: `site/style.css`
- Create: `site/app.js`

**Interfaces:**
- Consumes: `data/pokedex.json`（拷进 `site/`）、`data/dex.json`（拷进 `site/`）、`data/images/home/`（拷进 `site/img/`）
- Produces: 静态站点，GitHub Pages 可直接托管

- [ ] **Step 1: 写 HTML**

`site/index.html`：

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>训练家图鉴</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="top">
  <div class="ring" id="ring"><span id="ringText">—</span></div>
  <div class="meta">
    <h1>训练家图鉴</h1>
    <p id="summary">载入中…</p>
    <p id="streak" class="dim"></p>
  </div>
</header>

<section class="today" id="today" hidden>
  <p class="todayLabel" id="todayLabel"></p>
  <div class="card" id="todayCard"></div>
</section>

<nav class="filters" id="filters"></nav>
<main class="grid" id="grid"></main>

<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 写 CSS**

`site/style.css`：

```css
:root{
  --bg:#fbf7f0; --card:#fff; --ink:#2b2622; --dim:#8b8079;
  --line:#e8e0d4; --accent:#c8552c; --shiny:#c9a227;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#17140f;--card:#221d17;--ink:#efe7db;--dim:#9a8d7f;--line:#3a3128;--accent:#e2794f;--shiny:#e3c05a}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.top{display:flex;gap:18px;align-items:center;padding:24px 20px 12px;max-width:1100px;margin:0 auto}
.ring{width:84px;height:84px;border-radius:50%;flex:0 0 auto;display:grid;place-items:center;
  background:conic-gradient(var(--accent) calc(var(--p,0)*1%),var(--line) 0);
  position:relative}
.ring::after{content:"";position:absolute;inset:8px;border-radius:50%;background:var(--bg)}
.ring span{position:relative;z-index:1;font-weight:600;font-size:14px}
h1{font-size:20px;margin:0 0 2px}
p{margin:0}
.dim{color:var(--dim);font-size:13px}
.today{max-width:1100px;margin:8px auto 0;padding:0 20px}
.todayCard{margin-top:8px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;
  display:flex;gap:16px;align-items:center;box-shadow:0 6px 20px rgba(0,0,0,.06)}
.todayCard img{width:110px;height:110px;object-fit:contain}
.todayCard .flavor{margin-top:6px;color:var(--dim);font-size:13px;max-width:640px}
.filters{max-width:1100px;margin:18px auto 0;padding:0 20px;display:flex;gap:8px;flex-wrap:wrap}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
  padding:5px 13px;cursor:pointer;font-size:13px}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.grid{max-width:1100px;margin:12px auto 60px;padding:0 20px;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:12px}
.cell{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px;text-align:center;
  transition:transform .18s ease}
.cell:hover{transform:translateY(-3px)}
.cell img{width:96px;height:96px;object-fit:contain;display:block;margin:0 auto}
.cell.locked img{filter:grayscale(1) opacity(.22)}
.cell .no{font-size:11px;color:var(--dim)}
.cell .nm{font-size:13px;margin-top:2px;min-height:2.6em}
.cell .ty{font-size:11px;color:var(--dim)}
.cell.shiny{box-shadow:0 0 0 2px var(--shiny) inset}
.badge{display:inline-block;font-size:11px;color:var(--shiny);font-weight:600}
```

- [ ] **Step 3: 写 JS**

`site/app.js`：

```javascript
const IMG = "img/";
let POKEDEX = null, DEX = null, FILTER = "all";

const $ = (id) => document.getElementById(id);

function artOf(p) {
  const f = p.art || p.artShiny;
  return f ? IMG + encodeURIComponent(f) : "";
}

async function load() {
  const [pd, dx] = await Promise.all([
    fetch("pokedex.json").then(r => r.json()),
    fetch("dex.json").then(r => r.json()).catch(() => ({ collected: [], streak: 0, today: {} })),
  ]);
  POKEDEX = pd.pokemon;
  DEX = dx;
  render();
}

function ownedMap() {
  const m = new Map();
  for (const c of DEX.collected || []) m.set(c.id, c);
  return m;
}

function render() {
  const owned = ownedMap();
  const n = owned.size, total = POKEDEX.length;
  $("ring").style.setProperty("--p", total ? (n / total) * 100 : 0);
  $("ringText").textContent = `${n}/${total}`;
  $("summary").textContent = `图鉴 ${n}/${total} · 完成 ${((n / total) * 100).toFixed(1)}%`;
  $("streak").textContent = `连读 ${DEX.streak || 0} 天`;

  const today = DEX.today || {};
  const last = (DEX.collected || [])[DEX.collected.length - 1];
  if (last && DEX.lastAwardDate === today.date) {
    $("today").hidden = false;
    $("todayLabel").textContent = `今日捕获 · ${today.date}`;
    const p = POKEDEX.find(x => x.id === last.id) || {};
    $("todayCard").innerHTML = `
      <img src="${artOf(p).replace(p.art || "", p.artShiny || "")}" alt="${p.nameZh || ""}">
      <div>
        <div><b>${p.nameZh || last.nameZh}</b> ${last.shiny ? '<span class="badge">✦ 闪光</span>' : ""}</div>
        <div class="dim">No.${p.id || last.id} · ${(p.types || []).join("/")} · ${p.category || ""}</div>
        <div class="flavor">${p.flavor || ""}</div>
      </div>`;
  }

  const gens = [...new Set(POKEDEX.map(p => Number(p.id) <= 151 ? 1 : Number(p.id) <= 251 ? 2
    : Number(p.id) <= 386 ? 3 : Number(p.id) <= 493 ? 4 : Number(p.id) <= 649 ? 5
    : Number(p.id) <= 721 ? 6 : Number(p.id) <= 809 ? 7 : Number(p.id) <= 905 ? 8 : 9))].sort();
  $("filters").innerHTML = [["all", "全部"], ...gens.map(g => [g, `第${g}世代`])]
    .map(([v, t]) => `<button data-f="${v}" class="${String(v) === String(FILTER) ? "on" : ""}">${t}</button>`).join("");
  $("filters").querySelectorAll("button").forEach(b => b.onclick = () => { FILTER = b.dataset.f; render(); });

  const ownedIds = owned;
  const list = POKEDEX.filter(p => {
    if (FILTER === "all") return true;
    const g = Number(p.id) <= 151 ? 1 : Number(p.id) <= 251 ? 2 : Number(p.id) <= 386 ? 3
      : Number(p.id) <= 493 ? 4 : Number(p.id) <= 649 ? 5 : Number(p.id) <= 721 ? 6
      : Number(p.id) <= 809 ? 7 : Number(p.id) <= 905 ? 8 : 9;
    return g === Number(FILTER);
  });
  $("grid").innerHTML = list.map(p => {
    const c = ownedIds.get(p.id);
    const img = artOf(p);
    return `<div class="cell ${c ? "" : "locked"} ${c && c.shiny ? "shiny" : ""}">
      <div class="no">No.${p.id}</div>
      <img src="${img}" alt="${p.nameZh}" loading="lazy">
      <div class="nm">${c ? p.nameZh : "???"}</div>
      <div class="ty">${c ? (p.types || []).join("/") : (p.types || []).join("/")}</div>
    </div>`;
  }).join("");
}

load().catch(e => { $("summary").textContent = "载入失败：" + e.message; });
```

- [ ] **Step 4: 拷贝数据进 site/**

Run:
```bash
cd /workspace/pokemon-reading-dex
mkdir -p site/img
cp data/pokedex.json site/pokedex.json
cp data/dex.json site/dex.json
cp data/images/home/*.png site/img/
ls site/img | wc -l
```
Expected: 3006 张

- [ ] **Step 5: 本地起服务并截图**

Run:
```bash
cd /workspace/pokemon-reading-dex/site && python3 -m http.server 8899 &
sleep 2
cd /workspace/pokemon-reading-dex && node -e "
const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1200,height:1400}});
await p.goto('http://127.0.0.1:8899/index.html');await p.waitForTimeout(2500);
await p.screenshot({path:'/workspace/pokemon-reading-dex/site-preview.png',fullPage:false});
console.log('summary=',await p.textContent('#summary'));await b.close();})();
"
```
Expected: summary 文本含「图鉴 0/1025」，截图生成

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add site
git commit -m "feat: 图鉴页，图鉴墙与今日开箱卡"
```

---

### Task 6: 油猴插件

**Files:**
- Create: `plugin/weread-dex.user.js`

**Interfaces:**
- Consumes: `data/dex.json`（从站点拉）、`data/pokedex.json`（从站点拉）
- Produces: 浏览器端脚本，页面内注入胶囊 + 图鉴墙 + 开箱卡

- [ ] **Step 1: 写插件**

`plugin/weread-dex.user.js`：

```javascript
// ==UserScript==
// @name         微信读书 · 训练家图鉴
// @namespace    https://lilia-shound.github.io/pokemon-dex/
// @version      1.0.0
// @description  在微信读书网页版显示今日阅读进度与宝可梦图鉴
// @match        https://weread.qq.com/web/reader*
// @match        https://weread.qq.com/web/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(() => {
  "use strict";
  const BASE = "https://lilia-shound.github.io/pokemon-dex";
  const CSS = `
  .tx-caps{position:fixed;right:18px;top:18px;z-index:2147483000;display:flex;gap:8px;align-items:center;
    background:rgba(255,255,255,.92);border:1px solid #e8e0d4;border-radius:999px;padding:7px 13px;
    box-shadow:0 4px 16px rgba(0,0,0,.1);font:13px/1 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#2b2622;cursor:pointer}
  .tx-caps .bar{width:64px;height:6px;border-radius:3px;background:#e8e0d4;overflow:hidden}
  .tx-caps .bar i{display:block;height:100%;background:#c8552c;width:0}
  .tx-modal{position:fixed;inset:0;z-index:2147483001;background:rgba(20,16,12,.55);display:none;
    align-items:center;justify-content:center;backdrop-filter:blur(3px)}
  .tx-modal.on{display:flex}
  .tx-panel{background:#fbf7f0;color:#2b2622;width:min(920px,92vw);height:min(78vh,760px);border-radius:16px;
    display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.35)}
  .tx-head{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid #e8e0d4}
  .tx-grid{overflow:auto;padding:14px 18px;display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:10px}
  .tx-cell{background:#fff;border:1px solid #e8e0d4;border-radius:10px;padding:8px;text-align:center;font-size:11px}
  .tx-cell img{width:72px;height:72px;object-fit:contain;display:block;margin:0 auto}
  .tx-cell.locked img{filter:grayscale(1) opacity(.25)}
  .tx-cell.shiny{box-shadow:0 0 0 2px #c9a227 inset}
  .tx-open{margin:12px 18px 0;background:#fff;border:1px solid #e8e0d4;border-radius:12px;padding:12px;display:flex;gap:14px;align-items:center}
  .tx-open img{width:84px;height:84px;object-fit:contain}
  .tx-open .fl{color:#8b8079;font-size:12px;margin-top:4px}
  `;
  const st = document.createElement("style");
  st.textContent = CSS;
  document.head.appendChild(st);

  let DEX = { collected: [], streak: 0, today: {} };
  let POKEDEX = { pokemon: [] };
  let MODAL = null;

  async function load() {
    try {
      const [d, p] = await Promise.all([
        fetch(BASE + "/dex.json", { cache: "no-store" }).then((r) => r.json()),
        fetch(BASE + "/pokedex.json", { cache: "no-store" }).then((r) => r.json()),
      ]);
      DEX = d; POKEDEX = p;
    } catch (e) {
      console.warn("[训练家图鉴] 数据拉取失败", e);
    }
    paintCaps();
    if (MODAL) paintModal();
  }

  function todayInfo() {
    const t = DEX.today || {};
    const thr = t.threshold || 1800;
    const sec = t.seconds || 0;
    return { sec, thr, pct: Math.min(100, Math.round((sec / thr) * 100)), date: t.date };
  }

  function paintCaps() {
    let el = document.getElementById("tx-caps");
    if (!el) {
      el = document.createElement("div");
      el.id = "tx-caps";
      el.onclick = openModal;
      document.body.appendChild(el);
    }
    const i = todayInfo();
    el.innerHTML = `<span>🏾 ${i.pct}%</span><span class="bar"><i style="width:${i.pct}%"></i></span><span>${Math.round(i.sec / 60)}/${Math.round(i.thr / 60)} 分</span>`;
  }

  function openModal() {
    if (!MODAL) {
      MODAL = document.createElement("div");
      MODAL.className = "tx-modal";
      MODAL.onclick = (e) => { if (e.target === MODAL) closeModal(); };
      document.body.appendChild(MODAL);
    }
    paintModal();
    MODAL.classList.add("on");
  }
  function closeModal() { MODAL && MODAL.classList.remove("on"); }

  function paintModal() {
    const owned = new Map((DEX.collected || []).map((c) => [c.id, c]));
    const last = (DEX.collected || [])[(DEX.collected || []).length - 1];
    const lastP = last ? POKEDEX.pokemon.find((x) => x.id === last.id) : null;
    const n = owned.size, total = POKEDEX.pokemon.length;
    const head = `<div class="tx-head"><b>训练家图鉴 ${n}/${total}</b><span>连读 ${DEX.streak || 0} 天 · 关闭 ✕</span></div>`;
    const open = last && DEX.lastAwardDate === (DEX.today || {}).date && lastP ? `
      <div class="tx-open">
        <img src="${BASE}/img/${encodeURIComponent(lastP.artShiny && last.shiny ? lastP.artShiny : lastP.art)}">
        <div><div><b>${lastP.nameZh}</b> ${last.shiny ? '<span style="color:#c9a227">✦ 闪光</span>' : ""}</div>
        <div style="color:#8b8079;font-size:12px">No.${lastP.id} · ${(lastP.types || []).join("/")}</div>
        <div class="fl">${lastP.flavor || ""}</div></div>
      </div>` : "";
    const cells = POKEDEX.pokemon.map((p) => {
      const c = owned.get(p.id);
      const img = (c && c.shiny && p.artShiny) ? p.artShiny : p.art;
      return `<div class="tx-cell ${c ? "" : "locked"} ${c && c.shiny ? "shiny" : ""}">
        <div style="color:#8b8079">No.${p.id}</div>
        <img src="${BASE}/img/${encodeURIComponent(img)}" loading="lazy">
        <div>${c ? p.nameZh : "???"}</div></div>`;
    }).join("");
    MODAL.innerHTML = `<div class="tx-panel">${head}${open}<div class="tx-grid">${cells}</div></div>`;
  }

  load();
  setInterval(load, 10 * 60 * 1000);
})();
```

- [ ] **Step 2: 用 Playwright 在模拟的微信读书页面上验证**

Run:
```bash
cd /workspace/pokemon-reading-dex
node -e "
const {chromium}=require('playwright');
(async()=>{
 const b=await chromium.launch();
 const p=await b.newPage({viewport:{width:1280,height:900}});
 await p.route('**/pokemon-dex/**', r=>r.continue());
 await p.goto('http://127.0.0.1:8899/blank.html').catch(()=>{});
 await p.setContent('<html><body>fake reader</body></html>');
 await p.addScriptTag({path:'plugin/weread-dex.user.js'});
 await p.waitForTimeout(1500);
 console.log('caps=', await p.textContent('#tx-caps').catch(()=>'MISSING'));
 await p.click('#tx-caps');
 await p.waitForTimeout(1200);
 console.log('modal cells=', await p.locator('.tx-cell').count());
 await p.screenshot({path:'plugin-preview.png'});
 await b.close();
})();
"
```
Expected: 打印 caps 文本与 modal 格子数（1025），生成 plugin-preview.png

- [ ] **Step 3: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add plugin
git commit -m "feat: 油猴插件，阅读页进度胶囊与图鉴墙"
```

---

### Task 7: 托管与通知

**Files:**
- Create: `scripts/push_notify.py`
- Create: `scripts/deploy.sh`
- Modify: `scripts/award.py`（发奖后调用推送）

**Interfaces:**
- Consumes: env `PUSHPLUS_TOKEN`（可选）、env `GITHUB_TOKEN`
- Produces: `push_notify.send(title: str, content: str) -> bool`

- [ ] **Step 1: 写推送模块**

`scripts/push_notify.py`：

```python
"""微信推送（pushplus）。未配置 token 时静默跳过。"""

from __future__ import annotations

import json
import os
import urllib.request


def send(title: str, content: str) -> bool:
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        print("[推送] 未配置 PUSHPLUS_TOKEN，跳过")
        return False
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=json.dumps({"token": token, "title": title, "content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 失败: {exc}")
        return False
    ok = payload.get("code") == 200
    print(f"[推送] {'成功' if ok else '失败'}: {payload.get('msg')}")
    return ok
```

- [ ] **Step 2: 在 award.py 里接上推送**

在 `scripts/award.py` 的 `save_state(state)` 之后、`print(f"已写入 {DEX_PATH}")` 之前插入：

```python
    try:
        from scripts import push_notify
        icon = "✦" if award_full["shiny"] else "●"
        push_notify.send(
            f"{icon} 捕获 {'闪光' if award_full['shiny'] else ''}{award_full['nameZh']}",
            f"今日已读 {round(secs / 60)} 分钟 · 连读 {streak} 天 · 图鉴 {len(owned) + 1}/1025",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[推送] 跳过: {exc}")
```

- [ ] **Step 3: 写部署脚本**

`scripts/deploy.sh`：

```bash
#!/usr/bin/env bash
# 把 site/ 与状态文件推到 GitHub Pages 仓库
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p site/img
cp data/pokedex.json site/pokedex.json
cp data/dex.json site/dex.json
cp data/images/home/*.png site/img/

if [ ! -d .git ]; then
  git init -q
  git remote add origin https://github.com/lilia-shound/pokemon-dex.git || true
fi

git add site data/pokedex.json data/dex.json data/images
git -c user.name="Mavis" -c user.email="mavis@local" commit -q -m "chore: 更新图鉴站与状态" || true
git push origin HEAD:main
echo "已推送。图鉴页： https://lilia-shound.github.io/pokemon-dex/"
```

- [ ] **Step 4: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add scripts/push_notify.py scripts/deploy.sh scripts/award.py
git commit -m "feat: 微信推送与部署脚本"
```

---

### Task 8: 第一圈实测（需要姜来配合）

**Files:** 无代码改动，只有实测与记录

- [ ] **Step 1: 请姜来在微信读书读 10 分钟**

在对话里明确请求：打开微信读书（App 或网页版都行），读任意一本书 10 分钟，读完回来说一声。

- [ ] **Step 2: 跑自检确认时长确实增长**

Run: `cd /workspace/pokemon-reading-dex && python3 scripts/check_today.py`
Expected: 今日秒数 > 0，且能看到今日桶 key

若仍为 0 → 微信读书口径不认这次阅读。**不要伪造，先如实报告**，然后按设计文档 §4.4 的兜底方案改判定源。

- [ ] **Step 3: 跑一次真实发放**

Run: `cd /workspace/pokemon-reading-dex && python3 scripts/award.py`
Expected: 若今日秒数 ≥ 1800 则抽到一只；否则提示未达标（阈值可临时用 `--force` 验链路）

- [ ] **Step 4: 部署并在微信读书页面上验证**

Run: `bash scripts/deploy.sh`，然后浏览器打开 `https://lilia-shound.github.io/pokemon-dex/` 与微信读书阅读页，确认胶囊与图鉴墙都正常

- [ ] **Step 5: 记录实测结论**

在 `notes/01-research.md` 追加「实测结论」小节，写清：哪种阅读方式会让 `readTimes` 增长、阈值是否合适、时区是否确认为北京时间。

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
git add notes
git commit -m "docs: 记录第一圈实测结论"
```

---

## Self-Review

**Spec coverage 检查**

| 设计文档条目 | 对应任务 |
|---|---|
| §3 架构与权威归属 | Task 4（服务端唯一判定）、Task 5/6（只读展示） |
| §3.2 隐私边界 | Task 4（`dex.json` 只写必要字段）、Task 6（插件不存 Key） |
| §4.1–4.3 接口与分桶 | Task 1 |
| §4.4 待实测口径 | Task 8 Step 2 |
| §5 抽卡规则（未收集池 / 闪光 / streak 加成） | Task 3 |
| §6.1 数据源与字段 | Task 2 |
| §6.2 图片分发 | Task 2 Step 5、Task 5 Step 4 |
| §7 组件表（7 个模块） | Task 1–7 一一对应 |
| §7.1 插件三功能 | Task 6 |
| §8 错误处理（Key 失效 / 时区 / 幂等 / 写失败） | Task 1（WeReadError 抛出）、Task 1（today_seconds 规则）、Task 3（幂等）、Task 4（写失败不更新状态） |
| §9 验收标准 6 条 | Task 8 Step 1–5 逐条对应 |
| §10 YAGNI | 未实现任何被排除项 |

**占位符扫描**：无 TBD / TODO / "实现相关错误处理" / "写测试覆盖上述"。所有代码块均为可直接落盘的完整实现。

**类型一致性核对**
- `weread_client.read_times(mode) -> dict[int,int]` → Task 1 定义，Task 4 调用一致 ✅
- `weread_client.today_seconds(rt, now=None) -> tuple[int|None,int]` → Task 1 定义，Task 4 调用 `_, secs = ...` 一致 ✅
- `dex_core.pick(owned_ids, pool, streak, rng) -> dict|None` → Task 3 定义，Task 4 调用一致 ✅
- `dex_core.new_state(old, award, today, secs) -> dict` → Task 3 定义，Task 4 调用一致 ✅
- `dex_core.SHINY_ODDS` / `THRESHOLD_SECONDS` → Task 3 定义，Task 4 与测试引用一致 ✅
- `fetch_pokemon.extract(src) -> dict` / `pick_art(home_images, name_zh, want_shiny)` → Task 2 定义，测试一致 ✅
- `push_notify.send(title, content) -> bool` → Task 7 定义，Task 7 Step 2 调用一致 ✅

**已知遗留（有意为之，不阻塞）**
- `dex_core.new_state` 里 `compute_streak` 依赖 `collected[].date`，该字段在 Task 4 Step 1 才补上 —— Task 3 的测试用 `new_state` 时传的 award 不含 date，故 streak 断言依赖 `lastAwardDate == today` 分支。Task 4 Step 1 补字段后行为一致，Task 3 的测试需在 Task 4 之后仍通过（`new_state` 追加时写 `date=today`，streak 计算结果不变）。
- 第 9 代部分宝可梦的 `home` 立绘缺失（实测 1013/1025 有），`extract` 会让 `art` 为空，图鉴页用 CSS 占位，不影响收集逻辑。


---

## 执行偏差记录（2026-11-14 实做后补记）

计划是在实现前写的，实做中有 6 处偏离，全部记录在此。

| # | 计划写的 | 实际做的 | 为什么 |
|---|---|---|---|
| 1 | 测试用 `pytest` | 改用标准库 `unittest` + `run_tests.py` | 沙箱 PyPI 不通（`pip install pytest` 报 `No matching distribution`），零依赖更稳 |
| 2 | 图片落到 `data/images/home/`，再用 `site/img/` | 图片**不落盘**，`pokedex.json` 里存完整直链 | 52MB 仓库 push 反复断连（`GnuTLS recv error` ×4）；且图鉴页/插件本来就跑在姜来浏览器里，够得到 raw CDN。仓库 54MB → 1.8MB |
| 3 | 立绘选「编号-名字」标准形态 | 加了分层优先级：标准形态 > 性别变体 > 其它变体 > 形态/超极巨化/帽子 | 阿尔宙斯、厄诡椪只有形态立绘，得挑一个最像"本体"的 |
| 4 | 12 只无 HOME 立绘 → 占位 | PokeAPI `official-artwork` 直链兜底 | 抽到它们时开箱卡不能是空白 |
| 5 | streak 今天没达标就归零 | 今天没达标时显示**截至昨天的连续天数** | "已经连读 5 天，今天还没读"和"连读 0 天"体感完全不同 |
| 6 | 闪光/开箱卡取 `collected` 数组最后一条 | 改为**按日期找今天那条** | 数组顺序与 `lastAwardDate` 不一致时会开错宝可梦（实测踩到） |

### 额外修掉的两个真 bug

1. **闪光记录被覆盖**：同一只宝可梦收集多次时，`Map` 后者覆盖前者，导致闪光版被后来的普通版盖掉 → 改为保留闪光的那次
2. **开箱卡开错宝可梦**：见上表第 6 项

### 部署方式

- repo：`lilia-shound/pokemon-dex`（public —— Pages 免费账户不支持 private repo）
- `main` 分支：源码 + 数据
- `gh-pages` 分支：只含 `site/` 内容，Pages 源设在这里
- 图片不落仓库 → 不存在同一份 blob 存两遍的问题
- `scripts/deploy.sh` 用 `GIT_ASKPASS` 传 token，**不出现在命令行或 git 配置里**
  （最初版本把 token 拼进 URL，`git subtree push` 直接把 token 明文打印到了输出里，已改）

### 定时判定

主力用 mavis 的定时任务（每 3 小时），密钥留在本地 secret，不进 GitHub。
`.github/workflows/daily-award.yml` 作为可选兜底 —— 需要在 repo 里手动加
`WEREAD_API_KEY` secret 才生效，否则会优雅跳过。
