# 训练家图鉴中枢 S1（内核） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「读满 N 分钟 → 掉一只宝可梦」的内核从微信读书专用升级成多源通用：引入源注册表、额度折算、逐日账本，让阅读、番茄钟、备考的行为量可以汇进同一个图鉴。

**Architecture:** `data/sources.json` 是源注册表（每个源声明「多少原始单位 = 1 额度」）。`scripts/sources.py` 负责读配置与折算额度，`scripts/ledger.py` 负责逐日账本的合并/裁剪/迁移，`scripts/dex_core.py` 保持纯逻辑并新增「待发奖日判定 / v2 状态组装 / 进化链养成」，`scripts/award.py` 继续当编排器（取数 → 合并账本 → 判定 → 抽卡 → 写盘 → 推送）。**抽卡权仍然只在服务端**，客户端只是数据源。

**Tech Stack:** Python 3.11（标准库 only，零第三方依赖）、unittest、GitHub Actions（cron 结算）、GitHub Pages（图鉴页）。

**上游设计：** [2026-11-18-pokemon-dex-hub-design.md](../specs/2026-11-18-pokemon-dex-hub-design.md)

---

## Global Constraints

这些约束适用于**每一个** Task，不需要在 Task 里重复。

- **Python 3.11，标准库 only。** 不引入 pytest、不引入 requests、不引入 pydantic。`run_tests.py` 用 `unittest.TestLoader().discover()`，新测试文件必须能被它自动发现。
- **测试放 `tests/`，文件名 `test_*.py`，类名 `XxxTest`，方法名 `test_xxx`。** 参考既有 `tests/test_dex_core.py` 的写法。
- **中文注释与中文 docstring。** 现有代码全是中文，follow 它。
- **`scripts/ledger.py` / `scripts/dex_core.py` 一律不碰网络、不碰文件；`scripts/sources.py` 只有 `load_config` 一处 IO（读注册表）。** 其余 IO 在 `scripts/award.py`（读状态、写状态）。纯函数（`validate_config` / `credit_for_day` / `day_credit` / `merge_amounts` / `prune` / `pick` …）全部不碰 IO，可直接单测。
- **结算只认北京时间（Asia/Shanghai）。** 所有日期是 `YYYY-MM-DD` 字符串。
- **额度按天分桶，跨天清零。** 当天攒超的额度不结转。
- **一天最多 1 只。** 由注册表根级 `maxPerDay` 表达，当前值 1。
- **`perAward = 2` 意味着微信读书单独要 1800 秒官方口径（约 39 分钟体感）才掉 1 只** —— 比旧版 15 分钟翻倍。这是姜来拍板「番茄钟和阅读平权」的必然结果。想恢复旧体验，把 `data/sources.json` 的 `perAward` 改成 1 即可，代码不用动。
- **现有读方读的字段一个都不能删。** `site/app.js` 与 `plugin/weread-dex.user.js` 依赖 `collected[].id / .nameZh / .shiny / .at`、`streak`、`today.seconds`、`today.threshold`。多源信息一律走**新增**字段。
- **`dex_core.THRESHOLD_SECONDS` 保留**（值 900），作为向后兼容的默认值常量；**权威值在 `data/sources.json` 的 `sources.weread.per`**。`DEX_THRESHOLD` 环境变量继续能覆盖它。
- **提交前 `python3 run_tests.py` 必须全绿。**
- **一次提交只做一件事**，commit message 用中文，动词开头（`feat:` / `fix:` / `test:` / `docs:` / `refactor:`）。
- **不要跑 `scripts/deploy.sh`** —— 它会动 `gh-pages` 分支。S1 只推 `main`。
- **沙箱里 git 推代码**：`git -c http.expectContinue=false push origin HEAD:main`，并且 `export GIT_SSL_NO_VERIFY=1`（网关 TLS 问题，详见 `drive-and-deploy` 备忘）。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `data/sources.json` | 新建 | 源注册表：每个源的名字、单位、`per`（多少单位 = 1 额度）、`mode`、`enabled`、`maxPerDay`；根级 `perAward` / `maxPerDay` / `maxBackfill` / `ledgerDays` |
| `scripts/sources.py` | 新建 | 读 + 校验 `sources.json`；额度折算（`floor`）；只含纯逻辑 |
| `scripts/ledger.py` | 新建 | 账本合并（同源同日取 max）、滚动裁剪、v1→v2 状态迁移；只含纯逻辑 |
| `scripts/dex_core.py` | 修改 | 保留 `shiny_probability` / `compute_streak` / `pick` / `THRESHOLD_SECONDS`；新增 `pending_days` / `build_state` / `chain_key` / `advance_chain`；**移除 `new_state`**（被 `build_state` 取代，避免两套状态机） |
| `scripts/award.py` | 修改 | 编排：读配置 → 迁移 → 拉数 → 合并账本 → 判定待发奖日 → 逐日抽卡 → 组装 v2 状态 → 写盘 → 推送。新增 `--migrate-only` |
| `scripts/check_today.py` | 修改 | 自检改读配置（阈值以 `sources.json` 为准），并打印今日额度折算 |
| `tests/test_sources.py` | 新建 | 配置校验 + 额度折算 |
| `tests/test_ledger.py` | 新建 | 合并 / 裁剪 / 迁移 |
| `tests/test_dex_core_v2.py` | 新建 | `pending_days` / `build_state` |
| `tests/test_chains.py` | 新建 | 进化链 |
| `tests/test_award_flow.py` | 修改 | 既有回归测试按新模型改写（阈值数字要调） |
| `tests/test_award_multi.py` | 新建 | 多源合并 + 一次结清 + 保底闪光 |
| `data/dex.json` | 修改 | 真实状态迁移到 v2 |
| `site/dex.json` | 修改 | 同步 |
| `README.md` | 修改 | 写清新模型、源注册表、额度语义 |
| `.github/workflows/daily-award.yml` | 修改 | 把 `data/sources.json` 一并复制到 `site/` |

---

## Task 0: 修掉三个日期依赖的既有测试

**Files:**
- Modify: `tests/test_weread_client.py`

**Interfaces:**
- Consumes: 无
- Produces: 无

> 为什么先做这个：**动手之前 `python3 run_tests.py` 就是红的**（`Ran 53 tests` →
> `FAILED (failures=2, errors=1)`）。
> `TodaySecondsTest` 里硬编码了「今天 = 2026-11-14」，而 `today_seconds` 默认拿**真实时钟**
> 去比 —— 一过那天，桶就落在过去，函数按设计返回「今天还没读」，测试自然就红了。
> **是测试写错了，不是代码坏了。** 修好之后，后面每个 Task 的「全部 PASS」才作数。

- [ ] **Step 1: 跑测试，确认这 3 个是红的**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: `FAILED (failures=2, errors=1)`，全部落在 `tests.test_weread_client.TodaySecondsTest`：
`test_picks_latest_bucket_of_today`、`test_ignores_future_buckets`、`test_bucket_key_is_beijing_midnight`。

- [ ] **Step 2: 给这三个测试传固定的 now**

把 `tests/test_weread_client.py` 里 `TodaySecondsTest` 的这三个方法替换成：

```python
    def test_picks_latest_bucket_of_today(self):
        now = datetime.datetime(2026, 11, 14, 10, 0, tzinfo=SH)
        today = _ts(2026, 11, 14)
        yesterday = _ts(2026, 11, 13)
        key, secs = wc.today_seconds({yesterday: 600, today: 900}, now=now)
        self.assertEqual(key, today)
        self.assertEqual(secs, 900)

    def test_ignores_future_buckets(self):
        now = datetime.datetime(2026, 11, 14, 10, 0, tzinfo=SH)
        today = _ts(2026, 11, 14)
        tomorrow = _ts(2026, 11, 15)
        key, secs = wc.today_seconds({today: 300, tomorrow: 999}, now=now)
        self.assertEqual(key, today)
        self.assertEqual(secs, 300)

    def test_bucket_key_is_beijing_midnight(self):
        """桶 key 落在北京时间的自然日 00:00。"""
        now = datetime.datetime(2026, 11, 14, 10, 0, tzinfo=SH)
        today = _ts(2026, 11, 14)
        key, _ = wc.today_seconds({today: 100}, now=now)
        self.assertEqual(
            datetime.datetime.fromtimestamp(key, SH),
            datetime.datetime(2026, 11, 14, 0, 0, tzinfo=SH),
        )
```

`test_zero_when_latest_bucket_is_yesterday` 和 `test_accepts_explicit_now` **不用动**
（前者本来就返回 `(None, 0)`，后者已经传了 `now`）。

- [ ] **Step 3: 跑测试，确认全绿**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: `Ran 53 tests` → `OK`

- [ ] **Step 4: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add tests/test_weread_client.py && git commit -m "test: 修掉三个日期依赖的失败（测试硬编码了今天，函数按设计比较真实时钟）"
```

---

## Task 1: 源注册表与额度折算

**Files:**
- Create: `data/sources.json`
- Create: `scripts/sources.py`
- Create: `tests/test_sources.py`

**Interfaces:**
- Consumes: 无（这是地基）
- Produces:
  - `scripts.sources.SourceConfigError(RuntimeError)`
  - `scripts.sources.validate_config(cfg: dict) -> None`
  - `scripts.sources.load_config(path) -> dict`（读文件 + 校验 + 应用 `DEX_THRESHOLD` 环境变量覆盖）
  - `scripts.sources.enabled_sources(cfg: dict) -> dict[str, dict]`
  - `scripts.sources.credit_for_day(cfg: dict, ledger_day: dict) -> dict[str, int]`
  - `scripts.sources.day_credit(cfg: dict, ledger_day: dict) -> int`
  - `scripts.sources.credits_for_days(cfg: dict, ledger: dict) -> dict[str, int]`

- [ ] **Step 1: 建源注册表文件**

创建 `data/sources.json`：

```json
{
  "version": 1,
  "perAward": 2,
  "maxPerDay": 1,
  "maxBackfill": 3,
  "ledgerDays": 30,
  "sources": {
    "weread": {
      "name": "微信读书",
      "icon": "📖",
      "unit": "秒",
      "per": 900,
      "mode": "pull",
      "enabled": true,
      "maxPerDay": 86400
    },
    "pom": {
      "name": "番茄钟",
      "icon": "🍅",
      "unit": "分钟",
      "per": 25,
      "mode": "push",
      "enabled": true,
      "maxPerDay": 1440
    },
    "words": {
      "name": "背单词",
      "icon": "🔤",
      "unit": "个",
      "per": 20,
      "mode": "push",
      "enabled": false,
      "maxPerDay": 500
    }
  }
}
```

- [ ] **Step 2: 写失败的测试**

创建 `tests/test_sources.py`：

```python
import unittest

from scripts import sources as src


def cfg(**over):
    base = {
        "version": 1,
        "perAward": 2,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "unit": "秒", "per": 900,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
            "words": {"name": "背单词", "unit": "个", "per": 20,
                      "mode": "push", "enabled": False, "maxPerDay": 500},
        },
    }
    base.update(over)
    return base


class ValidateConfigTest(unittest.TestCase):
    def test_accepts_good_config(self):
        src.validate_config(cfg())  # 不抛异常即通过

    def test_rejects_missing_per_award(self):
        c = cfg()
        del c["perAward"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_zero_per_award(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(perAward=0))

    def test_rejects_non_int_per_award(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(perAward="2"))

    def test_rejects_source_without_per(self):
        c = cfg()
        del c["sources"]["pom"]["per"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_source_with_bad_mode(self):
        c = cfg()
        c["sources"]["pom"]["mode"] = "pushs"
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_empty_sources(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(sources={}))

    def test_rejects_source_per_below_one(self):
        c = cfg()
        c["sources"]["weread"]["per"] = 0
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)


class EnabledSourcesTest(unittest.TestCase):
    def test_only_enabled_returned(self):
        got = src.enabled_sources(cfg())
        self.assertIn("weread", got)
        self.assertIn("pom", got)
        self.assertNotIn("words", got)


class CreditForDayTest(unittest.TestCase):
    def test_floors_amount(self):
        got = src.credit_for_day(cfg(), {"weread": 1799})
        self.assertEqual(got["weread"], 1)

    def test_exact_multiple(self):
        got = src.credit_for_day(cfg(), {"weread": 1800})
        self.assertEqual(got["weread"], 2)

    def test_below_threshold_is_zero(self):
        got = src.credit_for_day(cfg(), {"weread": 899})
        self.assertEqual(got["weread"], 0)

    def test_disabled_source_never_credits(self):
        got = src.credit_for_day(cfg(), {"words": 1000})
        self.assertNotIn("words", got)
        self.assertEqual(got.get("words", 0), 0)

    def test_missing_source_counts_zero(self):
        got = src.credit_for_day(cfg(), {"weread": 900})
        self.assertEqual(got["pom"], 0)

    def test_day_credit_sums_sources(self):
        got = src.day_credit(cfg(), {"weread": 900, "pom": 50})
        self.assertEqual(got, 3)

    def test_day_credit_empty(self):
        self.assertEqual(src.day_credit(cfg(), {}), 0)

    def test_credits_for_days_maps_each_day(self):
        got = src.credits_for_days(cfg(), {"2026-11-16": {"weread": 900},
                                           "2026-11-17": {"weread": 1800}})
        self.assertEqual(got, {"2026-11-16": 1, "2026-11-17": 2})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试，确认失败**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: FAIL，报 `ModuleNotFoundError: No module named 'scripts.sources'`

- [ ] **Step 4: 实现 `scripts/sources.py`**

```python
"""源注册表：读 sources.json、校验结构、把各源的原始量折算成额度。

额度是中枢的中间货币：不同行为的单位不可通约（秒 / 分钟 / 个词），
但「每天掉一只宝可梦」的名额是统一的。每个源自己声明「多少原始单位 = 1 额度」。

本模块不碰网络；读文件只在 load_config 一处。
"""

from __future__ import annotations

import json
import os
import pathlib

ROOT_KEYS = ("perAward", "maxPerDay", "maxBackfill", "ledgerDays")
SOURCE_KEYS = ("name", "unit", "per")


class SourceConfigError(RuntimeError):
    """sources.json 结构不对，或读不出来。"""


def _is_int(v) -> bool:
    # bool 是 int 的子类，但 True 不该被当成 1 接受
    return isinstance(v, int) and not isinstance(v, bool)


def validate_config(cfg: dict) -> None:
    """校验注册表结构。任何不合法的形状都抛 SourceConfigError。"""
    if not isinstance(cfg, dict):
        raise SourceConfigError("sources.json 顶层必须是对象")
    for key in ROOT_KEYS:
        if key not in cfg:
            raise SourceConfigError(f"sources.json 缺少 {key}")
        if not _is_int(cfg[key]) or cfg[key] < 1:
            raise SourceConfigError(f"{key} 必须是 >=1 的整数，实际 {cfg[key]!r}")
    sources = cfg.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise SourceConfigError("sources.json 的 sources 必须是非空对象")
    for sid, s in sources.items():
        if not isinstance(s, dict):
            raise SourceConfigError(f"源 {sid} 必须是对象")
        for key in SOURCE_KEYS:
            if key not in s:
                raise SourceConfigError(f"源 {sid} 缺少 {key}")
        if not _is_int(s["per"]) or s["per"] < 1:
            raise SourceConfigError(f"源 {sid} 的 per 必须是 >=1 的整数，实际 {s['per']!r}")
        if s.get("mode") not in ("pull", "push"):
            raise SourceConfigError(f"源 {sid} 的 mode 必须是 pull 或 push，实际 {s.get('mode')!r}")
        if "maxPerDay" in s and (not _is_int(s["maxPerDay"]) or s["maxPerDay"] < 1):
            raise SourceConfigError(f"源 {sid} 的 maxPerDay 必须是 >=1 的整数")


def load_config(path) -> dict:
    """读 sources.json 并校验。失败抛 SourceConfigError。"""
    p = pathlib.Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SourceConfigError(f"找不到源注册表：{p}") from exc
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceConfigError(f"{p} 不是合法 JSON：{exc}") from exc
    validate_config(cfg)
    # 旧的环境变量旋钮继续有效：覆盖微信读书的 per
    env = os.environ.get("DEX_THRESHOLD")
    if env:
        try:
            cfg["sources"]["weread"]["per"] = int(env)
        except (KeyError, ValueError) as exc:
            raise SourceConfigError(f"DEX_THRESHOLD 不是整数：{env!r}") from exc
    return cfg


def enabled_sources(cfg: dict) -> dict:
    """只返回参与结算的源。"""
    return {sid: s for sid, s in cfg["sources"].items() if s.get("enabled", False)}


def credit_for_day(cfg: dict, ledger_day: dict) -> dict[str, int]:
    """每个启用的源当天折算出多少额度（向下取整，零头不计）。"""
    out: dict[str, int] = {}
    for sid, s in enabled_sources(cfg).items():
        amount = int((ledger_day or {}).get(sid, 0) or 0)
        out[sid] = amount // int(s["per"])
    return out


def day_credit(cfg: dict, ledger_day: dict) -> int:
    """当天全源额度之和。"""
    return sum(credit_for_day(cfg, ledger_day).values())


def credits_for_days(cfg: dict, ledger: dict) -> dict[str, int]:
    """逐日算出额度：{日期: 额度}。"""
    return {day: day_credit(cfg, amounts) for day, amounts in (ledger or {}).items()}
```

- [ ] **Step 5: 跑测试，确认通过**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: 全部 PASS（既有测试 + 新的 `tests/test_sources.py`）

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add data/sources.json scripts/sources.py tests/test_sources.py && git commit -m "feat: 源注册表与额度折算（scripts/sources.py + data/sources.json）"
```

---

## Task 2: 账本（合并 / 裁剪 / 迁移）

**Files:**
- Create: `scripts/ledger.py`
- Create: `tests/test_ledger.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `scripts.ledger.empty_ledger() -> dict`
  - `scripts.ledger.merge_amounts(ledger: dict, source_id: str, days: dict[str, int]) -> dict`
  - `scripts.ledger.prune(ledger: dict, keep_days: int, today: str) -> dict`
  - `scripts.ledger.migrate_state(state: dict) -> dict`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_ledger.py`：

```python
import unittest

from scripts import ledger as lg


class MergeAmountsTest(unittest.TestCase):
    def test_adds_new_day(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        self.assertEqual(got, {"2026-11-16": {"weread": 900}})

    def test_same_day_takes_max_not_sum(self):
        """重复投递是幂等的：取 max，不叠加。"""
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        b = lg.merge_amounts(a, "weread", {"2026-11-16": 1200})
        self.assertEqual(b["2026-11-16"]["weread"], 1200)

    def test_lower_repeat_does_not_overwrite(self):
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 1200})
        b = lg.merge_amounts(a, "weread", {"2026-11-16": 900})
        self.assertEqual(b["2026-11-16"]["weread"], 1200)

    def test_other_sources_coexist(self):
        a = lg.merge_amounts({}, "weread", {"2026-11-16": 900})
        b = lg.merge_amounts(a, "pom", {"2026-11-16": 50})
        self.assertEqual(b["2026-11-16"], {"weread": 900, "pom": 50})

    def test_negative_amount_ignored(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": -5})
        self.assertEqual(got, {})

    def test_none_amount_ignored(self):
        got = lg.merge_amounts({}, "weread", {"2026-11-16": None})
        self.assertEqual(got, {})

    def test_does_not_mutate_input(self):
        original = {"2026-11-16": {"weread": 900}}
        lg.merge_amounts(original, "pom", {"2026-11-16": 50})
        self.assertEqual(original, {"2026-11-16": {"weread": 900}})


class PruneTest(unittest.TestCase):
    def test_keeps_today_and_recent(self):
        led = {"2026-11-14": {"weread": 900},
               "2026-11-16": {"weread": 900},
               "2026-11-17": {"weread": 900}}
        got = lg.prune(led, 3, "2026-11-16")
        self.assertEqual(sorted(got), ["2026-11-14", "2026-11-16"])

    def test_drops_future_days(self):
        led = {"2026-11-16": {"weread": 900}, "2026-11-20": {"weread": 900}}
        got = lg.prune(led, 30, "2026-11-16")
        self.assertEqual(sorted(got), ["2026-11-16"])

    def test_keeps_exactly_keep_days(self):
        led = {f"2026-11-{d:02d}": {"weread": 900} for d in range(1, 11)}
        got = lg.prune(led, 5, "2026-11-10")
        self.assertEqual(sorted(got), ["2026-11-06", "2026-11-07",
                                       "2026-11-08", "2026-11-09", "2026-11-10"])


class MigrateStateTest(unittest.TestCase):
    def test_v1_gets_version_two(self):
        st = lg.migrate_state({"version": 1, "collected": []})
        self.assertEqual(st["version"], 2)

    def test_v1_entries_get_sources_and_form(self):
        st = lg.migrate_state({"version": 1, "collected": [
            {"id": "0563", "nameZh": "迭失棺", "shiny": False, "at": "x", "date": "2026-11-14"}]})
        c = st["collected"][0]
        self.assertEqual(c["sources"], ["weread"])
        self.assertEqual(c["form"], "0563")

    def test_v1_gets_empty_ledger_and_chains(self):
        st = lg.migrate_state({"version": 1, "collected": []})
        self.assertEqual(st["ledger"], {})
        self.assertEqual(st["chains"], {})

    def test_already_v2_keeps_ledger(self):
        st = lg.migrate_state({"version": 2, "collected": [],
                               "ledger": {"2026-11-16": {"weread": 900}},
                               "chains": {"a→b": {"care": 1, "stage": 0}}})
        self.assertEqual(st["ledger"], {"2026-11-16": {"weread": 900}})
        self.assertEqual(st["chains"], {"a→b": {"care": 1, "stage": 0}})

    def test_v2_entry_keeps_explicit_sources(self):
        st = lg.migrate_state({"version": 2, "collected": [
            {"id": "0025", "sources": ["weread", "pom"], "form": "0026"}]})
        self.assertEqual(st["collected"][0]["sources"], ["weread", "pom"])
        self.assertEqual(st["collected"][0]["form"], "0026")

    def test_does_not_mutate_input(self):
        original = {"version": 1, "collected": [{"id": "1"}]}
        lg.migrate_state(original)
        self.assertEqual(original["collected"][0], {"id": "1"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: FAIL，报 `ModuleNotFoundError: No module named 'scripts.ledger'`

- [ ] **Step 3: 实现 `scripts/ledger.py`**

```python
"""账本：每天各源贡献了多少原始量。

账本是「过程」，图鉴（collected）是「结果」。
合并规则是取 max 而不是求和 —— 客户端上报的语义是「该源当天累计总量」，
所以重复投递同一天必须幂等。

本模块只处理 dict，不碰网络也不碰文件。
"""

from __future__ import annotations

import datetime


def empty_ledger() -> dict:
    return {}


def merge_amounts(ledger: dict, source_id: str, days: dict) -> dict:
    """把某个源某几天的量并进账本。同源同一天取 max。"""
    out = {day: dict(amounts) for day, amounts in (ledger or {}).items()}
    for day, amount in (days or {}).items():
        if amount is None:
            continue
        amount = int(amount)
        if amount < 0:
            continue
        row = out.setdefault(day, {})
        row[source_id] = max(int(row.get(source_id, 0)), amount)
    return out


def prune(ledger: dict, keep_days: int, today: str) -> dict:
    """只保留最近 keep_days 天（含 today），未来日期直接丢掉。"""
    today_d = datetime.date.fromisoformat(today)
    keep = {(today_d - datetime.timedelta(days=i)).isoformat() for i in range(keep_days)}
    return {day: dict(amounts) for day, amounts in (ledger or {}).items() if day in keep}


def migrate_state(state: dict) -> dict:
    """v1 → v2：补 sources / form，version 改成 2，初始化 ledger 与 chains。

    对已经是 v2 的文件是幂等的：已有字段一律保留，缺的才补。
    """
    st = dict(state or {})
    collected = []
    for c in st.get("collected") or []:
        c = dict(c)
        c.setdefault("sources", ["weread"])
        c.setdefault("form", c.get("id"))
        collected.append(c)
    st["collected"] = collected
    st["version"] = 2
    st.setdefault("ledger", {})
    st.setdefault("chains", {})
    st.setdefault("streak", 0)
    st.setdefault("lastAwardDate", None)
    return st
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add scripts/ledger.py tests/test_ledger.py && git commit -m "feat: 逐日账本（合并 / 裁剪 / v1→v2 迁移）"
```

---

## Task 3: 结算骨架（`pending_days` / `build_state`）

**Files:**
- Modify: `scripts/dex_core.py`（**只新增** `pending_days` / `build_state`；**不删** `new_state`）
- Create: `tests/test_dex_core_v2.py`
- （`tests/test_dex_core.py` 本 Task **一个字都不动** —— 见下方说明）

> **为什么本 Task 不删 `new_state`**
>
> `scripts/award.py` 目前有 3 处 `dc.new_state(...)` 调用（`award.py:125`、`:146`、`:171`）。
> `new_state` 是 `award.py` 还在用的活状态机 —— **调用方没迁移就先删函数，等于把发布流水线打断**
> （实测删掉后 `tests/test_award_flow.py` 立刻 5 个 error）。
> 而 `award.py` 不在本 Task 的可改文件里，全局约束又要求「提交前 `run_tests.py` 必须全绿」。
>
> 所以：**删 `new_state` + 改写 `tests/test_dex_core.py` 里依赖它的 v1 测试，统一挪到 Task 5** ——
> Task 5 正是把 `award.py` 迁到 v2 的那一刀，迁完 caller 再杀 callee，顺序才是对的。
> 最终状态与原计划完全一致，只是把这一步挪后一个 Task。

**Interfaces:**
- Consumes: `scripts.sources.day_credit(cfg, ledger_day) -> int`
- Produces:
  - `scripts.dex_core.pending_days(cfg: dict, ledger: dict, awarded_dates) -> list[str]`（升序）
  - `scripts.dex_core.build_state(old: dict, cfg: dict, today: str, today_sources: dict, ledger_data: dict, chains: dict, new_awards: list) -> dict`
- **不改动**：`scripts.dex_core.new_state` 本 Task 保留原样（`award.py` 还在用），Task 5 再删

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_dex_core_v2.py`：

```python
import unittest

from scripts import dex_core as dc


def cfg(per_award=2, weread_per=900):
    return {
        "version": 1,
        "perAward": per_award,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "unit": "秒", "per": weread_per,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
        },
    }


class PendingDaysTest(unittest.TestCase):
    def test_day_below_threshold_not_pending(self):
        led = {"2026-11-16": {"weread": 900}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), [])

    def test_day_at_threshold_pending(self):
        led = {"2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), ["2026-11-16"])

    def test_already_awarded_day_excluded(self):
        led = {"2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, {"2026-11-16"}), [])

    def test_multiple_days_sorted_ascending(self):
        led = {"2026-11-17": {"weread": 1800}, "2026-11-15": {"weread": 1800},
               "2026-11-16": {"weread": 1800}}
        self.assertEqual(dc.pending_days(cfg(), led, set()),
                         ["2026-11-15", "2026-11-16", "2026-11-17"])

    def test_mixed_sources_accumulate(self):
        led = {"2026-11-16": {"weread": 900, "pom": 25}}
        self.assertEqual(dc.pending_days(cfg(), led, set()), ["2026-11-16"])


class BuildStateTest(unittest.TestCase):
    def _award(self, pid="0001", day="2026-11-16"):
        return {"id": pid, "nameZh": "妙蛙种子", "shiny": False,
                "at": "2026-11-16T22:00:00+08:00", "date": day,
                "sources": ["weread"], "form": pid}

    def test_shape_is_v2(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["version"], 2)
        for key in ("collected", "streak", "lastAwardDate", "today", "ledger", "chains"):
            self.assertIn(key, st)

    def test_appends_award(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {},
                            {}, {}, [self._award()])
        self.assertEqual(len(st["collected"]), 1)
        self.assertEqual(st["collected"][0]["id"], "0001")
        self.assertEqual(st["lastAwardDate"], "2026-11-16")
        self.assertEqual(st["streak"], 1)

    def test_today_keeps_legacy_fields(self):
        """现有读方依赖 today.seconds / today.threshold —— 不能没有。"""
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["today"]["seconds"], 1800)
        self.assertEqual(st["today"]["threshold"], 1800)
        self.assertEqual(st["today"]["date"], "2026-11-16")

    def test_threshold_is_cost_of_one_pokemon(self):
        """threshold 是「掉一只实际要读多久」：perAward × weread.per。"""
        st = dc.build_state({"collected": []}, cfg(per_award=3), "2026-11-16",
                            {"weread": 1800}, {}, {}, [])
        self.assertEqual(st["today"]["threshold"], 2700)

    def test_threshold_follows_weread_per(self):
        st = dc.build_state({"collected": []}, cfg(weread_per=600), "2026-11-16",
                            {"weread": 600}, {}, {}, [])
        self.assertEqual(st["today"]["threshold"], 1200)

    def test_streak_decays_after_a_gap(self):
        """断签后连读天数归零 —— 相对 v1 的有意变更（v1 一直挂着一个不衰减的旧值）。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-12", "sources": ["weread"], "form": "0001"},
                             {"id": "0002", "nameZh": "b", "shiny": False, "at": "x",
                              "date": "2026-11-13", "sources": ["weread"], "form": "0002"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0003", "nameZh": "c", "shiny": False, "at": "x",
                              "date": "2026-11-14", "sources": ["weread"], "form": "0003"}])
        # 11-15 没过 → 连读断了
        self.assertEqual(st["streak"], 0)

    def test_streak_counts_from_yesterday_when_today_not_awarded(self):
        """今天还没达标，但昨天发过奖 → 连读从昨天数起（不断）。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-15", "sources": ["weread"], "form": "0001"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {}, [])
        self.assertEqual(st["streak"], 1)

    def test_streak_breaks_when_new_day_awarded(self):
        """今天也发了奖 → 断签重算，锚在今天。"""
        old = {"collected": [{"id": "0001", "nameZh": "a", "shiny": False, "at": "x",
                              "date": "2026-11-14", "sources": ["weread"], "form": "0001"}]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0002", "nameZh": "b", "shiny": False, "at": "x",
                              "date": "2026-11-16", "sources": ["weread"], "form": "0002"}])
        self.assertEqual(st["streak"], 1)

    def test_disabled_source_dropped_from_today_sources(self):
        """today.sources 与 credit 口径一致：禁用源不参与。"""
        c = cfg()
        c["sources"]["words"] = {"name": "背单词", "unit": "个", "per": 20,
                                 "mode": "push", "enabled": False}
        st = dc.build_state({"collected": []}, c, "2026-11-16",
                            {"weread": 900, "words": 100}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"weread": 900})
        self.assertEqual(st["today"]["credit"], 1)

    def test_empty_state(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {}, {}, {}, [])
        self.assertIsNone(st["lastAwardDate"])
        self.assertEqual(st["streak"], 0)
        self.assertEqual(st["collected"], [])

    def test_does_not_mutate_inputs(self):
        old = {"collected": [{"id": "0001", "date": "2026-11-14"}]}
        today_sources = {"weread": 900}
        ledger_data = {"2026-11-16": {"weread": 900}}
        chains = {"a→b": {"care": 1, "stage": 0}}
        dc.build_state(old, cfg(), "2026-11-16", today_sources, ledger_data, chains,
                       [{"id": "0002", "date": "2026-11-16"}])
        self.assertEqual(old, {"collected": [{"id": "0001", "date": "2026-11-14"}]})
        self.assertEqual(today_sources, {"weread": 900})
        self.assertEqual(ledger_data, {"2026-11-16": {"weread": 900}})
        self.assertEqual(chains, {"a→b": {"care": 1, "stage": 0}})

    def test_same_day_two_awards_are_kept(self):
        """build_state 不按日期去重 —— 幂等由调用方保证（见 docstring）。"""
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {}, {}, {},
                            [{"id": "0001", "date": "2026-11-16"},
                             {"id": "0002", "date": "2026-11-16"}])
        self.assertEqual(len(st["collected"]), 2)

    def test_today_has_new_fields(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 900, "pom": 50}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"weread": 900, "pom": 50})
        self.assertEqual(st["today"]["credit"], 3)
        self.assertEqual(st["today"]["perAward"], 2)

    def test_zero_amount_sources_dropped_from_today(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16",
                            {"weread": 0, "pom": 50}, {}, {}, [])
        self.assertEqual(st["today"]["sources"], {"pom": 50})

    def test_streak_counts_consecutive_days(self):
        old = {"collected": [self._award("0001", "2026-11-15")]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [self._award("0002", "2026-11-16")])
        self.assertEqual(st["streak"], 2)

    def test_last_award_date_is_max(self):
        old = {"collected": [self._award("0001", "2026-11-15")]}
        st = dc.build_state(old, cfg(), "2026-11-16", {}, {}, {},
                            [self._award("0002", "2026-11-16")])
        self.assertEqual(st["lastAwardDate"], "2026-11-16")

    def test_ledger_and_chains_passed_through(self):
        st = dc.build_state({"collected": []}, cfg(), "2026-11-16", {},
                            {"2026-11-16": {"weread": 900}},
                            {"a→b": {"care": 1, "stage": 0}}, [])
        self.assertEqual(st["ledger"], {"2026-11-16": {"weread": 900}})
        self.assertEqual(st["chains"], {"a→b": {"care": 1, "stage": 0}})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: FAIL，报 `AttributeError: module 'scripts.dex_core' has no attribute 'pending_days'`

- [ ] **Step 3: 在 `scripts/dex_core.py` 里实现**

在文件顶部 import 区加上：

```python
from scripts import sources as src
```

（`scripts/sources.py` 不 import `dex_core`，没有循环依赖。）

在文件末尾追加：

```python
def pending_days(cfg: dict, ledger: dict, awarded_dates) -> list[str]:
    """所有「额度够了但还没发奖」的日期，按时间正序。

    一天最多掉 maxPerDay 只 —— 现在恒为 1 —— 所以每个日期最多出现一次。
    """
    awarded = set(awarded_dates or ())
    out = []
    for day in sorted(ledger or {}):
        if day in awarded:
            continue
        if src.day_credit(cfg, ledger[day]) >= int(cfg["perAward"]):
            out.append(day)
    return out


def build_state(old: dict, cfg: dict, today: str, today_sources: dict,
                ledger_data: dict, chains: dict, new_awards: list) -> dict:
    """组装 v2 状态。

    new_awards 是本次要并入的发奖列表（调用方已经排好序、判过幂等）。
    **这里绝不按日期去重** —— v1 的 new_state 曾静默丢掉重复的卡，
    导致调用方打印了「★ 获得」而状态里没有（旧版的「假消息」坑）。
    幂等由调用方保证。
    """
    collected = [dict(c) for c in (old.get("collected") or [])]
    for a in new_awards or []:
        collected.append(dict(a))
    awarded_dates = sorted({c["date"] for c in collected if c.get("date")})
    # 只保留启用源的原始量，和 credit 的口径保持一致
    enabled = src.enabled_sources(cfg)
    today_map = {
        k: int(v) for k, v in (today_sources or {}).items()
        if k in enabled and int(v) > 0
    }
    weread_per = int(cfg.get("sources", {}).get("weread", {}).get("per", 0))
    per_award = int(cfg["perAward"])
    return {
        "version": 2,
        "collected": collected,
        # 连读天数锚在「今天」，会衰减：断签后归零、闪光概率回到 1/96。
        # 这是相对 v1 的有意变更 —— v1 锚在发奖日，会一直挂着一个不衰减的旧值。
        "streak": compute_streak(awarded_dates, today),
        "lastAwardDate": awarded_dates[-1] if awarded_dates else None,
        "today": {
            "date": today,
            "seconds": int(today_map.get("weread", 0)),
            # 读方（site/app.js、plugin）拿 threshold 当「掉一只要读多久」用，
            # 所以这里发的是「掉一只实际需要的微信读书秒数」，不是 1 个额度的量。
            "threshold": per_award * weread_per,
            "sources": today_map,
            "credit": src.day_credit(cfg, today_map),
            "perAward": per_award,
        },
        "ledger": dict(ledger_data or {}),
        "chains": dict(chains or {}),
    }
```

- [ ] **Step 4: 保留旧的 `new_state`（本 Task 不删）**

`scripts/dex_core.py` 里的 `new_state` **原样留着**，一个字都不改。
理由见上面「为什么本 Task 不删 `new_state`」：它是 `award.py` 还在用的活状态机，删了就打断流水线。
删它的那一刀在 Task 5。

- [ ] **Step 5: `tests/test_dex_core.py` 本 Task 不动**

v1 的 `NewStateTest` / `CatchUpTest` 依赖 `new_state`，现在还能跑，**保持原样**。
它们在 Task 5 里随 `new_state` 一起被替换成 `BuildStateRegressionTest`（那个类的完整代码在 Task 5 的简报里）。

- [ ] **Step 6: 跑测试，确认通过**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: 全部 PASS。注意 `tests/test_award_flow.py` **这时应该还是绿的**（它测的是 award.py，Task 5 才改 award.py）。

- [ ] **Step 7: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add scripts/dex_core.py tests/test_dex_core.py tests/test_dex_core_v2.py && git commit -m "refactor: 状态机换成 v2（build_state 取代 new_state），加待发奖日判定"
```

---

## Task 4: 进化链分组与养成

**Files:**
- Modify: `scripts/dex_core.py`（追加 `chain_key` / `advance_chain`）
- Create: `tests/test_chains.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `scripts.dex_core.chain_key(pokemon: dict) -> str` —— 进化链的唯一标识（基础形态名）
  - `scripts.dex_core.advance_chain(chains: dict, pokemon: dict) -> dict` —— **原地修改** `chains` 并返回它

链状态结构：`{"care": int, "pendingShiny": int, "nextShinyAt": int}`

> **★ 这一层为什么只做「分组 + 养成 + 保底闪光」，不做形态改写**
>
> 真实数据（`data/pokedex.json`，1028 只，42arch 抓的）把**分叉的进化关系拍平成了直线**：
> 伊布的 8 个分支被拍成一条 9 级的直线、走路草链的 3 条支线混在一起，
> 而且**同一条链的成员拿到的 `evolution` 数组并不一致**
> （走路草是 6 个名字 `['走路草','臭臭花','霸王花','走路草','臭臭花','美丽花']`，
> 霸王花只有 3 个 `['走路草','臭臭花','霸王花']`）。
>
> 后果（已实测）：
> - 用「整条数组拼起来」当链 key → **543 条生物链被拆成 588 个 key**，19 条链被拆开，
>   care 被摊薄到不同 key 上；
> - 「这一阶对应哪只宝可梦」在分叉链上**没有唯一答案**（美丽花和霸王花是兄弟形态，
>   但在拍平的数据里被排成同一级）。
>
> 所以：**链分组用「基础形态名」**（同链所有成员共有的第一个名字，本地可算、不需要全局上下文；
> 实测分组结果 = 543 组，正好等于生物链数），
> **形态改写（`form` 字段随阶数变化）整层挪到 S6** —— S6 届时要么修数据管线重生成
> `pokedex.json`，要么改成「按基础形态分组、显示组内代表形态」的简化规则。
>
> 本层保留的是真正值钱的那半：**养 → 保底出闪**。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_chains.py`：

```python
import json
import pathlib
import unittest

from scripts import dex_core as dc

BULBASAUR = {"id": "0001", "nameZh": "妙蛙种子",
             "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
IVYSAUR = {"id": "0002", "nameZh": "妙蛙草",
           "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
VENUSAUR = {"id": "0003", "nameZh": "妙蛙花",
            "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
PIKACHU = {"id": "0025", "nameZh": "皮卡丘",
           "evolution": ["皮丘", "皮卡丘", "雷丘", "皮丘", "皮卡丘", "雷丘"]}
PIKACHU_DUPE = {"id": "0025b", "nameZh": "皮卡丘",
                "evolution": ["皮丘", "皮卡丘", "雷丘"]}
# 真实数据里同链成员的 evolution 数组并不一致 —— 这正是旧 key 算法的坑
WALKING_GRASS = {"id": "0004", "nameZh": "走路草",
                 "evolution": ["走路草", "臭臭花", "霸王花", "走路草", "臭臭花", "美丽花"]}
BEAUTIFUL_FLOWER = {"id": "0005", "nameZh": "霸王花",
                     "evolution": ["走路草", "臭臭花", "霸王花"]}


class ChainKeyTest(unittest.TestCase):
    def test_uses_base_form_name(self):
        self.assertEqual(dc.chain_key(BULBASAUR), "妙蛙种子")

    def test_dedups_repeated_names(self):
        self.assertEqual(dc.chain_key(PIKACHU), "皮丘")

    def test_missing_evolution_is_empty(self):
        self.assertEqual(dc.chain_key({"id": "x", "nameZh": "独角兽"}), "")

    def test_members_with_different_arrays_share_one_key(self):
        """真实数据的坑：同链成员的 evolution 数组不一致，key 必须仍然一致。"""
        self.assertEqual(dc.chain_key(WALKING_GRASS), dc.chain_key(BEAUTIFUL_FLOWER))

    def test_different_chains_differ(self):
        self.assertNotEqual(dc.chain_key(BULBASAUR), dc.chain_key(PIKACHU))


class AdvanceChainTest(unittest.TestCase):
    def test_first_care_creates_entry(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["care"], 1)
        self.assertEqual(st["pendingShiny"], 0)
        self.assertEqual(st["nextShinyAt"], 30)

    def test_mutates_in_place_and_returns_same_object(self):
        chains = {}
        got = dc.advance_chain(chains, BULBASAUR)
        self.assertIs(got, chains)

    def test_care_accumulates(self):
        chains = {}
        for _ in range(5):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子"]["care"], 5)

    def test_thirty_care_grants_one_guarantee(self):
        chains = {}
        for _ in range(30):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 1)
        self.assertEqual(st["nextShinyAt"], 60)

    def test_sixty_care_grants_second_guarantee(self):
        chains = {}
        for _ in range(60):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 2)
        self.assertEqual(st["nextShinyAt"], 90)

    def test_guarantee_rhythm_is_fixed_not_sliding(self):
        """care 跳变时保底节奏不该整体后移：nextShinyAt 每次 +30，不是 care+30。"""
        chains = {"妙蛙种子": {"care": 35, "pendingShiny": 0, "nextShinyAt": 30}}
        dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 1)
        self.assertEqual(st["nextShinyAt"], 60)

    def test_other_chain_is_independent(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        dc.advance_chain(chains, PIKACHU)
        self.assertEqual(chains["妙蛙种子"]["care"], 1)
        self.assertEqual(chains["皮丘"]["care"], 1)

    def test_no_evolution_creates_no_entry(self):
        chains = {}
        dc.advance_chain(chains, {"id": "x", "nameZh": "独角兽"})
        self.assertEqual(chains, {})


class RealPokedexTest(unittest.TestCase):
    """真实数据护栏 —— 合成数据会掩盖「同链成员 evolution 数组不一致」这个坑。"""

    @classmethod
    def setUpClass(cls):
        p = pathlib.Path(__file__).resolve().parent.parent / "data" / "pokedex.json"
        cls.pokedex = json.loads(p.read_text(encoding="utf-8"))["pokemon"]
        cls.by_name = {x["nameZh"]: x for x in cls.pokedex}

    def test_walking_grass_line_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n])
                for n in ("走路草", "臭臭花", "霸王花", "美丽花")}
        self.assertEqual(len(keys), 1)

    def test_eevee_family_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n])
                for n in ("伊布", "水伊布", "雷伊布", "火伊布", "太阳伊布",
                          "月亮伊布", "叶伊布", "冰伊布", "仙子伊布")}
        self.assertEqual(len(keys), 1)

    def test_pikachu_line_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n]) for n in ("皮丘", "皮卡丘", "雷丘")}
        self.assertEqual(len(keys), 1)

    def test_chain_key_is_base_form_name(self):
        self.assertEqual(dc.chain_key(self.by_name["皮卡丘"]), "皮丘")

    def test_grouping_matches_biological_chain_count(self):
        """用基础形态名分组 → 543 组，正好等于生物链数。"""
        groups = {}
        for x in self.pokedex:
            groups.setdefault(dc.chain_key(x), []).append(x["nameZh"])
        self.assertEqual(len(groups), 543)
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: FAIL，报 `AttributeError: module 'scripts.dex_core' has no attribute 'chain_key'`

- [ ] **Step 3: 在 `scripts/dex_core.py` 里实现**

追加到 `scripts/dex_core.py` 末尾：

```python
def _chain_names(pokemon: dict) -> list[str]:
    """进化链上的名字，去重并保留顺序。

    原始数据里皮卡丘那条是 ["皮丘","皮卡丘","雷丘","皮丘","皮卡丘","雷丘"]，
    去重后才是真正的链。
    """
    names: list[str] = []
    for n in pokemon.get("evolution") or []:
        if n and n not in names:
            names.append(n)
    return names


def chain_key(pokemon: dict) -> str:
    """进化链的唯一标识 —— **基础形态名**（evolution 的第一个名字）。

    为什么不用整条数组拼起来当 key：真实数据（42arch）把分叉的进化关系拍平成了直线，
    而且同一条链的成员拿到的 evolution 数组并不一致（走路草 6 个名字，霸王花只有 3 个）。
    用整条数组当 key 会把同一条链拆成多份（实测 543 条生物链被拆成 588 个 key，
    19 条链被拆开，care 被摊薄到不同 key 上）。

    基础形态名是同链所有成员共有的，本地就能算，不需要全局上下文。
    实测：用基础形态名分组 → 543 组，正好等于生物链数。
    """
    names = _chain_names(pokemon)
    return names[0] if names else ""


def advance_chain(chains: dict, pokemon: dict) -> dict:
    """给这条链加一天培养。原地修改并返回 chains。

    care 每多 30 天给一次保底闪光（pendingShiny += 1）——
    下次抽到这个链的宝可梦时可以强制闪光。把「一次性运气」换成「持续投入」。

    注：形态改写（进化阶数 → collected[].form）不在本层做 ——
    分叉的进化关系在拍平的数据里没有唯一的「这一阶对应哪只」，那是 S6 的活。
    """
    key = chain_key(pokemon)
    if not key:
        return chains
    st = chains.setdefault(key, {"care": 0, "pendingShiny": 0, "nextShinyAt": 30})
    st["care"] = int(st.get("care", 0)) + 1
    if st["care"] >= int(st.get("nextShinyAt", 30)):
        st["pendingShiny"] = int(st.get("pendingShiny", 0)) + 1
        # 节奏固定：每次 +30，而不是 care + 30 —— care 跳变时不该整体后移
        st["nextShinyAt"] = int(st.get("nextShinyAt", 30)) + 30
    return chains
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add scripts/dex_core.py tests/test_chains.py && git commit -m "feat: 进化链分组与养成（基础形态名分组 + 保底闪光）"
```

---

## Task 5: 发放器编排改造

> **承上：Task 2 审阅遗留（本任务必须一并修掉）**
>
> 1. `scripts/ledger.py` 的 `migrate_state` 只浅拷贝顶层，返回的 `ledger` / `chains` 与入参**是同一个对象**
>    （实测 `out["ledger"] is in["ledger"]` → True）。这与本模块自己对 `merge_amounts` / `prune` 定的
>    「两层拷贝」标准不一致，而账本是共享状态，就地改很容易出难查的 bug。**Task 5 要把账本接进发放器，
>    正是最容易踩到它的地方。** 修法：
>    `st["ledger"] = {d: dict(v) for d, v in (st.get("ledger") or {}).items()}`，`chains` 同理。
> 2. `tests/test_ledger.py` 的 `test_does_not_mutate_input` 只断言 `collected[0]`，对上面那条别名零覆盖。
>    补一条断言：改返回值不得回写入参。
> 3. `scripts/ledger.py` 的 `empty_ledger` 是四个公开函数里唯一没有 docstring 的，与全局约束
>    「中文注释与中文 docstring」不一致。补一行中文 docstring。
> 4. `scripts/ledger.py` 的 `merge_amounts` 注解写成 `days: dict`，简报 Produces 声明的是
>    `days: dict[str, int]`。补上类型参数。
>
> **另外，Task 3 挪过来的两件事（也要在本 Task 做掉）**：
>
> 5. **删掉 `scripts/dex_core.py` 的 `new_state`** —— 本 Task 把 `award.py` 迁到 v2 之后，
>    它就没有调用方了。留两套状态机会分叉（v2 的形状已经变了）。
> 6. **改写 `tests/test_dex_core.py` 里依赖 `new_state` 的 v1 测试** ——
>    删掉 `NewStateTest` 和 `CatchUpTest` 两个类，换成 `BuildStateRegressionTest`：
>
>    ```python
>    class BuildStateRegressionTest(unittest.TestCase):
>        """v1 的 new_state 已被 build_state 取代，这些是它的回归等价物。"""
>
>        def _cfg(self):
>            return {
>                "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
>                "ledgerDays": 30,
>                "sources": {"weread": {"name": "微信读书", "unit": "秒", "per": 900,
>                                       "mode": "pull", "enabled": True,
>                                       "maxPerDay": 86400}},
>            }
>
>        def _award(self, pid="0001", day="2026-11-14", shiny=False):
>            return {"id": pid, "nameZh": "妙蛙种子", "shiny": shiny,
>                    "at": f"{day}T22:00:00+08:00", "date": day,
>                    "sources": ["weread"], "form": pid}
>
>        def test_appends_award(self):
>            st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
>                                {"weread": 1800}, {}, {}, [self._award()])
>            self.assertEqual(len(st["collected"]), 1)
>            self.assertEqual(st["collected"][0]["id"], "0001")
>            self.assertEqual(st["lastAwardDate"], "2026-11-14")
>            self.assertEqual(st["streak"], 1)
>            self.assertEqual(st["today"]["seconds"], 1800)
>            self.assertEqual(st["today"]["threshold"], 1800)
>
>        def test_is_idempotent_same_day(self):
>            """同一天重复结算不会多出一条 —— 幂等由调用方保证，这里验证状态组装。"""
>            once = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
>                                  {"weread": 1800}, {}, {}, [self._award()])
>            twice = dc.build_state(once, self._cfg(), "2026-11-14",
>                                   {"weread": 2400}, {}, {}, [])
>            self.assertEqual(len(twice["collected"]), 1)
>            self.assertEqual(twice["today"]["seconds"], 2400)
>
>        def test_awards_for_past_date_not_today(self):
>            old = {"collected": []}
>            st = dc.build_state(old, self._cfg(), "2026-11-15", {},
>                                {}, {}, [self._award("0001", "2026-11-14")])
>            self.assertEqual(st["collected"][0]["date"], "2026-11-14")
>            self.assertEqual(st["lastAwardDate"], "2026-11-14")
>            self.assertEqual(st["today"]["seconds"], 0)
>            self.assertEqual(st["today"]["date"], "2026-11-15")
>
>        def test_shiny_flag_preserved(self):
>            st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
>                                {}, {}, {}, [self._award(shiny=True)])
>            self.assertTrue(st["collected"][0]["shiny"])
>
>        def test_collected_entries_carry_date(self):
>            st = dc.build_state({"collected": []}, self._cfg(), "2026-11-14",
>                                {}, {}, {}, [self._award()])
>            self.assertEqual(st["collected"][0]["date"], "2026-11-14")
>
>        def test_streak_decays_after_a_gap(self):
>            """断签后连读天数归零 —— 相对 v1 的有意变更。"""
>            old = {"collected": [self._award("0001", "2026-11-12"),
>                                 self._award("0002", "2026-11-13")]}
>            st = dc.build_state(old, self._cfg(), "2026-11-16", {}, {}, {},
>                                [self._award("0003", "2026-11-14")])
>            self.assertEqual(st["streak"], 0)
>    ```
>
>    **注意排列陷阱**：`tests/test_dex_core.py` 里 `if __name__ == "__main__":` 那一段夹在
>    `NewStateTest` 和 `CatchUpTest` **中间**。删 `NewStateTest` 时不要连带删掉后面的 `CatchUpTest`。
>    完成后 `grep -n "class .*Test" tests/test_dex_core.py` 应该只看到 `BuildStateRegressionTest`
>    （以及它前面那些原有的小测试类）。

**Files:**
- Modify: `scripts/award.py`
- Modify: `scripts/dex_core.py`（**删掉 `new_state`** —— award.py 迁到 v2 之后就能杀它了）
- Modify: `tests/test_dex_core.py`（删掉 `NewStateTest` / `CatchUpTest`，换成 `BuildStateRegressionTest`）
- Modify: `tests/test_award_flow.py`
- Create: `tests/test_award_multi.py`

**Interfaces:**
- Consumes:
  - `scripts.sources.load_config(path) -> dict`
  - `scripts.ledger.migrate_state(state) -> dict`
  - `scripts.ledger.merge_amounts(ledger, source_id, days) -> dict`
  - `scripts.ledger.prune(ledger, keep_days, today) -> dict`
  - `scripts.dex_core.pending_days(cfg, ledger, awarded_dates) -> list[str]`
  - `scripts.dex_core.build_state(old, cfg, today, today_sources, ledger_data, chains, new_awards) -> dict`
  - `scripts.dex_core.advance_chain(chains, pokemon) -> dict`
  - `scripts.dex_core.pick(owned_ids, pool, streak, rng) -> dict | None`
- Produces:
  - `scripts/award.py` 新增模块级常量 `SOURCES_PATH`
  - `scripts/award.py` 新增 CLI 参数 `--migrate-only`（只迁移不抽卡，用于一次性把真实 dex.json 升到 v2）
  - 保持既有输出字符串不变：`★ 获得`、`今天已经发过奖了`、`补发 YYYY-MM-DD`

- [ ] **Step 1: 改写 `tests/test_award_flow.py`**

新模型下 `perAward = 2`、`weread.per = 900`，所以达标要 **1800 秒**（旧的测试用的是 1782 / 1000，只有 1 个额度，达不到 2）。

把整个 `tests/test_award_flow.py` 替换成：

```python
#!/usr/bin/env python3
"""发放器的「同一天只发一张」回归测试。

背景：新_state 按日期去重，一天只该发一张。但 award.py 原先在「今天已经
发过奖」时仍然会抽一张、打印「★ 获得」，最后被 new_state 悄悄丢掉 —— 假消息。
定时任务每 3 小时跑一次，当天会连报好几次（甚至报出好几只不存在的宝可梦）。

v2 模型下 perAward=2、weread.per=900，所以达标线是 1800 秒。
"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import award

# 2026-11-13 / 11-14 00:00 Asia/Shanghai 的桶 key
KEY_20261113 = 1794499200
KEY_20261114 = 1794585600
DAY = "2026-11-14"

CONFIG = {
    "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
    "ledgerDays": 30,
    "sources": {
        "weread": {"name": "微信读书", "icon": "📖", "unit": "秒", "per": 900,
                   "mode": "pull", "enabled": True, "maxPerDay": 86400},
        "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                "mode": "push", "enabled": True, "maxPerDay": 1440},
    },
}


class SameDayAwardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        award.DEX_PATH = self.dex_path

    def _run(self, buckets, argv=()):
        out = io.StringIO()
        with mock.patch.object(award, "fetch_buckets", return_value=buckets), \
             mock.patch.object(award, "load_config", return_value=CONFIG):
            with contextlib.redirect_stdout(out):
                rc = award.main(["--date", DAY, "--no-push", *argv])
        return rc, out.getvalue()

    def _state(self):
        return json.loads(self.dex_path.read_text(encoding="utf-8"))

    def test_first_run_awards_once(self):
        rc, out = self._run({KEY_20261114: 1800})
        self.assertEqual(rc, 0)
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 1)
        self.assertEqual(state["collected"][0]["date"], DAY)
        self.assertEqual(state["streak"], 1)

    def test_second_run_same_day_does_not_award_again(self):
        self._run({KEY_20261114: 1800})
        rc, out = self._run({KEY_20261114: 2400})
        self.assertEqual(rc, 0)
        self.assertIn("今天已经发过奖了", out)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_second_run_still_refreshes_today_seconds(self):
        """不重复发奖，但「今天已读多久」要继续更新（图鉴页要显示）。"""
        self._run({KEY_20261114: 1800})
        self._run({KEY_20261114: 2400})
        state = self._state()
        self.assertEqual(state["today"]["seconds"], 2400)
        self.assertEqual(state["today"]["date"], DAY)

    def test_force_does_not_duplicate_same_day(self):
        """--force 只无视达标线，不该违反「一天一张」。"""
        self._run({KEY_20261114: 1800})
        rc, out = self._run({KEY_20261114: 2400}, argv=["--force"])
        self.assertEqual(rc, 0)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_catch_up_awards_past_day_not_today(self):
        """延迟上报：昨天读满的数据今天才到 → 补发昨天，今天已发过的不再抽。"""
        self._run({KEY_20261114: 1800})
        rc, out = self._run({KEY_20261113: 1800, KEY_20261114: 2400})
        self.assertEqual(rc, 0)
        self.assertIn("补发 2026-11-13", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual(state["collected"][1]["date"], "2026-11-13")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 写新的多源测试**

创建 `tests/test_award_multi.py`：

```python
#!/usr/bin/env python3
"""多源结算：额度合并、一次结清、链养成、保底闪光。"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import award


def make_config(per_award=2, per=900):
    return {
        "version": 1, "perAward": per_award, "maxPerDay": 1, "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "icon": "📖", "unit": "秒", "per": per,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
        },
    }


KEY_20261114 = 1794585600
DAY = "2026-11-14"


class MultiSourceAwardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        award.DEX_PATH = self.dex_path

    def _run(self, buckets, config=None, argv=()):
        out = io.StringIO()
        with mock.patch.object(award, "fetch_buckets", return_value=buckets), \
             mock.patch.object(award, "load_config", return_value=config or make_config()):
            with contextlib.redirect_stdout(out):
                rc = award.main(["--date", DAY, "--no-push", *argv])
        return rc, out.getvalue()

    def _state(self):
        return json.loads(self.dex_path.read_text(encoding="utf-8"))

    def test_single_source_below_threshold_awards_nothing(self):
        rc, out = self._run({KEY_20261114: 900})
        self.assertEqual(rc, 0)
        self.assertNotIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 0)

    def test_single_source_at_threshold_awards(self):
        rc, out = self._run({KEY_20261114: 1800})
        self.assertIn("★ 获得", out)
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_today_credit_recorded(self):
        self._run({KEY_20261114: 1800})
        today = self._state()["today"]
        self.assertEqual(today["sources"], {"weread": 1800})
        self.assertEqual(today["credit"], 2)
        self.assertEqual(today["perAward"], 2)

    def test_ledger_records_the_day(self):
        self._run({KEY_20261114: 1800})
        self.assertEqual(self._state()["ledger"][DAY], {"weread": 1800})

    def test_collected_entry_records_source(self):
        self._run({KEY_20261114: 1800})
        self.assertEqual(self._state()["collected"][0]["sources"], ["weread"])

    def test_collected_entry_records_form(self):
        self._run({KEY_20261114: 1800})
        entry = self._state()["collected"][0]
        self.assertEqual(entry["form"], entry["id"])

    def test_two_days_catch_up_in_one_run(self):
        """11-13 和 11-14 都达标 → 一次跑完，发两张，按日期升序。"""
        KEY_20261113 = 1794499200
        rc, out = self._run({KEY_20261113: 1800, KEY_20261114: 1800})
        self.assertIn("★ 获得", out)
        state = self._state()
        self.assertEqual(len(state["collected"]), 2)
        self.assertEqual([c["date"] for c in state["collected"]],
                         ["2026-11-13", "2026-11-14"])
        self.assertEqual(state["streak"], 2)

    def test_backfill_capped_at_max_backfill(self):
        """五天都达标，但 maxBackfill=3 → 只补三天。

        桶 key 必须是北京时间午夜。已核实的对照表：
        2026-11-10 → 1794240000
        2026-11-11 → 1794326400
        2026-11-12 → 1794412800
        2026-11-13 → 1794499200
        2026-11-14 → 1794585600
        2026-11-15 → 1794672000
        """
        keys = {1794240000: 1800,   # 11-10
                1794326400: 1800,   # 11-11
                1794412800: 1800,   # 11-12
                1794499200: 1800,   # 11-13
                1794585600: 1800}   # 11-14
        rc, out = self._run(keys)
        state = self._state()
        self.assertEqual(len(state["collected"]), 3)
        self.assertEqual([c["date"] for c in state["collected"]],
                         ["2026-11-10", "2026-11-11", "2026-11-12"])

    def test_one_day_one_award_even_with_many_sources(self):
        rc, out = self._run({KEY_20261114: 3600})
        self.assertEqual(len(self._state()["collected"]), 1)

    def test_chain_state_created_after_award(self):
        self._run({KEY_20261114: 1800})
        chains = self._state()["chains"]
        self.assertEqual(len(chains), 1)
        entry = list(chains.values())[0]
        self.assertEqual(entry["care"], 1)
        self.assertEqual(entry["stage"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试，确认失败**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: FAIL —— `award.py` 还没有 `load_config`，且旧逻辑不认额度模型。

- [ ] **Step 4: 改造 `scripts/award.py`**

在 import 区加上：

```python
from scripts import ledger as lg   # noqa: E402
from scripts import sources as src  # noqa: E402
```

在 `DEX_PATH` 旁边加上：

```python
SOURCES_PATH = pathlib.Path(os.environ.get("SOURCES_PATH", ROOT / "data" / "sources.json"))
```

把 `main()` 整体替换成下面这版（其余函数 `load_pokedex` / `pokedex_total` / `load_state` / `save_state` / `fetch_buckets` / `pending_award_days` 保留不动）：

```python
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


def today_sources_from(state: dict) -> dict:
    """从旧状态里推出今天的各源量。

    v2 走 today.sources；v1 只有 today.seconds，按微信读书口径还原。
    """
    t = (state or {}).get("today") or {}
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
                    help="只把 dex.json 升到 v2，不抽卡（一次性迁移用）")
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

    if args.migrate_only:
        state = dc.build_state(old, cfg, today, today_sources_from(old),
                               old.get("ledger") or {}, old.get("chains") or {}, [])
        if not args.dry_run:
            save_state(state)
        print(f"已迁移到 v2：{len(state['collected'])} 只已收服   "
              f"（未抽卡）")
        return 0

    try:
        buckets = fetch_buckets()
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 取数失败，本轮不发奖：{exc}", file=sys.stderr)
        return 1

    ledger_data = lg.merge_amounts(old.get("ledger") or {}, "weread",
                                   weread_days(buckets))
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
                                      ledger_data, old.get("chains") or {}, []))
        return 0

    pool = load_pokedex()
    chains = old.get("chains") or {}
    new_awards = []
    for day in pending:
        streak = dc.compute_streak(sorted(awarded_dates | {day}), day)
        award = dc.pick(owned, pool, streak, random.Random())
        if award is None:
            print(f"池子空了 —— {pokedex_total()} 只全部收集完成！")
            break

        key = dc.chain_key(award)
        chain_state = chains.get(key)
        if chain_state and int(chain_state.get("pendingShiny", 0)) > 0:
            award["shiny"] = True
            chain_state["pendingShiny"] = int(chain_state["pendingShiny"]) - 1
            print(f"[保底] {key} 养到 {chain_state['care']} 天，这次必闪")

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
        dc.advance_chain(chains, award)

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
```

**注意**：`pending_award_days` 这个旧函数在新版里没人用了，**删掉它**（连同它的测试如果有）。
旧 `main()` 里引用 `dc.THRESHOLD_SECONDS` 的地方全部换成 `cfg`。

- [ ] **Step 5: 跑测试，确认通过**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py
```

Expected: 全部 PASS

- [ ] **Step 6: 本地干跑一次，真实验证**

```bash
cd /workspace/pokemon-reading-dex && cp data/dex.json /tmp/dex.backup.json && python3 scripts/award.py --dry-run
```

Expected: 打印今天的各源原始量、额度、已收集数；**不写文件**（`--dry-run`）。
如果 `WEREAD_API_KEY` 没配，`fetch_buckets` 会失败并打印 `[失败] 取数失败` —— 这时用
`python3 scripts/award.py --migrate-only --dry-run` 验证迁移路径。

- [ ] **Step 7: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add scripts/award.py tests/test_award_flow.py tests/test_award_multi.py && git commit -m "feat: 发放器改成多源额度结算（一次结清补发 + 链养成 + 保底闪光）"
```

---

## Task 6: 状态迁移 · 自检脚本 · README · workflow · 端到端演练

**Files:**
- Modify: `scripts/check_today.py`
- Modify: `data/dex.json`
- Modify: `site/dex.json`
- Modify: `README.md`
- Modify: `.github/workflows/daily-award.yml`

**Interfaces:**
- Consumes: Task 1-5 的全部产出
- Produces: `data/dex.json` 与 `site/dex.json` 都是 v2；`check_today.py` 打印额度；workflow 会把 `sources.json` 一起发布

- [ ] **Step 1: 改 `scripts/check_today.py`**

**整体替换**这个文件（不要做局部替换 —— 局部替换容易把 import 区和函数体搞串）：

```python
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
    weread_per = int(cfg["sources"]["weread"]["per"])
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
        "thresholdSeconds": weread_per,
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
```

关键变化：阈值不再读 `dc.THRESHOLD_SECONDS`，而是读 `sources.json`；
自检会打印**今日额度** `credit/perAward`，不再只说「达标线多少秒」。

- [ ] **Step 2: 跑自检，确认能跑**

```bash
cd /workspace/pokemon-reading-dex && python3 scripts/check_today.py --json | head -30
```

Expected: 打印 JSON，里面有 `thresholdSeconds: 900`、`perAward: 2`、`credit: <某个数>`。
（`WEREAD_API_KEY` 没配的话这一步会失败 —— 那就只验证脚本能 import：
`python3 -c "import scripts.check_today"`。）

- [ ] **Step 3: 迁移真实状态**

```bash
cd /workspace/pokemon-reading-dex && cp data/dex.json /tmp/dex.v1.backup.json && python3 scripts/award.py --migrate-only --no-push && python3 -c "
import json
d = json.load(open('data/dex.json', encoding='utf-8'))
assert d['version'] == 2, d['version']
assert all('sources' in c and 'form' in c for c in d['collected'])
assert 'ledger' in d and 'chains' in d
print('迁移 OK：version', d['version'], '已收服', len(d['collected']))
"
```

Expected: `迁移 OK：version 2 已收服 3`

- [ ] **Step 4: 同步 site 副本**

```bash
cd /workspace/pokemon-reading-dex && cp data/dex.json site/dex.json && python3 -c "
import json
a = json.load(open('data/dex.json', encoding='utf-8'))
b = json.load(open('site/dex.json', encoding='utf-8'))
assert a == b, 'site/dex.json 与 data/dex.json 不一致'
print('site 副本已同步')
"
```

- [ ] **Step 5: 改 workflow**

在 `.github/workflows/daily-award.yml` 的「同步状态并发布图鉴页」步骤里，
把 `cp data/pokedex.json site/pokedex.json` 后面加一行：

```bash
          cp data/sources.json site/sources.json
```

并且把 `git add data/dex.json site/dex.json` 改成：

```bash
          git add data/dex.json site/dex.json site/sources.json
```

（`site/sources.json` 现在还没有，workflow 跑的时候会生成 —— 这没关系，
第一次跑完就有了。但为了这个提交本身是干净的，本步骤结束后手动执行一次
`cp data/sources.json site/sources.json` 并把它一起 add 进本次提交。）

- [ ] **Step 6: 更新 README**

在 `README.md` 里，把「玩法规则」那张表换成多源额度版本，并新增一节说明源注册表。
新增内容（放在「玩法规则」之后）：

```markdown
### 多源额度模型

图鉴不只认微信读书。每个「源」自己声明「多少原始单位 = 1 额度」，存在 `data/sources.json`：

| 源 | 单位 | 1 额度 = | 怎么来的 |
|---|---|---|---|
| 微信读书 | 秒 | 900 秒 | 服务端定时拉官方 API |
| 番茄钟 | 分钟 | 25 分钟 | 客户端投递（GitHub Issue 投递箱） |
| 背单词 | 个 | 20 个 | 客户端投递 |

规则：

- 某源当天额度 = `floor(该源当天原始量 / 该源 per)`，零头不计
- 当天总额度 = 各启用源额度之和
- 总额度 ≥ `perAward`（当前 2）且当天还没发过奖 → 掉 1 只
- **一天最多 1 只**，额度跨天清零

> `perAward = 2` 意味着微信读书单独要 1800 秒官方口径（约 39 分钟体感）才掉 1 只。
> 想恢复旧体验（15 分钟一只），把 `data/sources.json` 的 `perAward` 改成 1 即可，代码不用动。

额度不是新增的负担，是把「读多少」和「掉多少」解耦：番茄钟满 25 分钟也是 1 个额度，
和微信读书的 15 分钟等价 —— 谁先凑够谁说了算。
```

同时把 `README.md` 的目录树里加上：

```
data/sources.json       源注册表：每个源的单位 / per / mode
scripts/sources.py      配置校验 + 额度折算
scripts/ledger.py       逐日账本：合并 / 裁剪 / 迁移
```

- [ ] **Step 7: 端到端演练**

```bash
cd /workspace/pokemon-reading-dex && python3 run_tests.py && python3 scripts/award.py --migrate-only --no-push && python3 scripts/check_today.py --json > /tmp/check.json && python3 -c "
import json
d = json.load(open('/tmp/check.json'))
print('thresholdSeconds', d['thresholdSeconds'], 'perAward', d['perAward'], 'credit', d['credit'])
" && git status --short
```

Expected: 测试全绿；迁移成功；`git status` 只剩待提交的 README / workflow / site 改动。

- [ ] **Step 8: 提交**

```bash
cd /workspace/pokemon-reading-dex && git add README.md .github/workflows/daily-award.yml data/dex.json site/dex.json site/sources.json scripts/check_today.py && git commit -m "feat: 状态迁移到 v2 + 自检显示额度 + README 与 workflow 更新"
```

- [ ] **Step 9: 推到 main 并验证线上没炸**

```bash
cd /workspace/pokemon-reading-dex && export GIT_SSL_NO_VERIFY=1 && git -c http.expectContinue=false push origin HEAD:main
```

推完等下一次 cron（每 3 小时，UTC `17 */3 * * *`）跑完，去 GitHub Actions 看
「每日宝可梦判定」这一步是不是绿的，再看 `data/dex.json` 有没有多出一条 `collected`。

如果 cron 红了，**不要自己猜着改** —— 把 Actions 日志贴出来，按日志改。

---

## Self-Review

**0. 本计划已在沙箱里原样跑过一遍**

把计划里的代码块抽出来，在 `/workspace/.pv`（仓库副本）里按 Task 顺序执行：

- `python3 -m py_compile scripts/*.py tests/*.py` → 语法 OK
- `python3 run_tests.py` → **Ran 125 tests, OK**
- `python3 scripts/award.py --migrate-only --no-push` → `已迁移到 v2：5 只已收服`
- `python3 scripts/award.py --dry-run` → 打印 `各源原始量 {'weread': 1776}  额度 1/2`，正常退出
- 老字段兼容性抽查 → `collected[].id/nameZh/shiny/at` + `streak` + `lastAwardDate` + `today.seconds/threshold` 全在

跑通过程中改掉了计划里四处真问题：

1. **桶 key 手算错了** —— `1794403200` 其实是 11-11 21:20，不是北京时间午夜。已换成实测值（11-10 → `1794240000` … 11-15 → `1794672000`），并把对照表写进 `test_backfill_capped_at_max_backfill` 的 docstring。
2. **补发日志格式对不上断言** —— 原来打印 `★ 补发 获得`，测试断言的是 `补发 2026-11-13`。改成 `★ 补发 2026-11-13 获得`。
3. **`--force` 会重复加当天** —— 加了 `today not in pending` 的判断。
4. **`--migrate-only` 会抹掉今天的秒数** —— 新增 `today_sources_from()`，v1 状态从 `today.seconds` 还原。

另外发现并修掉了**计划写法上的两个坑**：

5. **Task 6 原来用「片段替换」改 `check_today.py`** —— 一应用就把 import 区搞串行（`IndentationError`）。改成给完整文件。
6. **仓库本来就有一个红着的测试** —— `tests/test_weread_client.py` 的三个用例硬编码「今天 = 2026-11-14」，而 `today_seconds` 按设计比较真实时钟，一过那天必红（`Ran 53 tests → FAILED (failures=2, errors=1)`）。cron 不跑单测，所以一直没人发现。已加成 **Task 0**，先把地基修平。

**1. Spec 覆盖**

| spec 章节 | 落在哪个 Task |
|---|---|
| §3 额度模型（floor、各源之和） | Task 1 |
| §5.1 源注册表 | Task 1 |
| §5.2 dex.json v2 数据模型 | Task 3（`build_state`）、Task 5（写盘） |
| §5.3 v1→v2 迁移 | Task 2（`migrate_state`）、Task 6（真实文件迁移） |
| §5.4 `collected[].form` | **S6**（S1 期间恒等于 `id`，只占位） |
| §6.1 额度折算 | Task 1 |
| §6.2 一次结清所有待发奖日 + maxBackfill | Task 3（`pending_days`）、Task 5 |
| §6.3 streak 与闪光概率 | Task 5（逐日重算 streak） |
| §6.4 进化链（分组 / care / 保底） | Task 4（分组 + 养成）、Task 5（保底触发）；**形态改写 → S6** |
| §6.5 账本裁剪 | Task 2（`prune`） |
| §7 上报协议 | **不在 S1** —— 传输层属 S2。S1 只留 `merge_amounts` 这个接口 |
| §8.1 S1 文件划分 | 全部 Task |
| §9 错误处理 | Task 1（配置错误）、Task 5（取数失败）、Task 2（未来的日期） |
| §10 验收标准 | 全部 Task 的测试 + Task 6 的端到端演练 |

**缺口（明确不在 S1）**：§7 的 Issue 投递箱、`collect_reports.py`、图鉴页多源展示、
番茄钟网页版、KOReader 插件、备考源 —— 全部属于 S2-S6，各自有独立 spec。

**2. 占位符扫描**：本计划无 TBD / TODO / 「类似 Task N」。所有代码块都是完整可粘贴的实现，
所有测试函数都有实际断言，并且已在沙箱里跑过。

**3. 类型一致性**

- `sources.load_config` 返回 `dict`；`validate_config` / `enabled_sources` / `credit_for_day` /
  `day_credit` / `credits_for_days` 全部收 `cfg: dict` —— 各 Task 里用法一致。
- `ledger.merge_amounts(ledger, source_id, days)` 返回**新** dict，不改入参 —— Task 2 的测试锁定。
- `dex_core.pending_days(cfg, ledger, awarded_dates)` 返回 `list[str]`（升序）—— Task 3 定义，Task 5 用。
- `dex_core.build_state(old, cfg, today, today_sources, ledger_data, chains, new_awards)`
  —— 参数顺序在 Task 3 的测试与 Task 5 的调用里一致。
- `dex_core.advance_chain(chains, pokemon)` **原地修改** `chains` 并返回它 —— Task 4 的测试锁定。
- 链状态键：`care` / `stage` / `pendingShiny` / `nextShinyAt` —— Task 4 定义，Task 5 读 `pendingShiny`，
  Task 3 的 `build_state` 原样透传 `chains`。三处一致。
- `collected[]` 条目字段：`id` / `nameZh` / `shiny` / `at` / `date` / `sources` / `form` —— Task 3、4、5 一致。
