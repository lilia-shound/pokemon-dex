# 训练家图鉴 S7 · 日常闭环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补上三个日常闭环的洞：掉宝可梦发邮件通知、番茄钟/KOReader 能补录最近 7 天、图鉴页加最近 30 天历史（热力图 + 堆叠柱）。

**Architecture:** 三条**互不依赖**的 track，各自能独立跑通、独立提交、独立部署：

| Track | 交付物 | 碰结算核心吗 |
|---|---|---|
| **S7a 邮件通知** | `scripts/notify.py`（旁路脚本） | ❌ 只读 `data/dex.json`，只写 `data/notify_state.json` |
| **S7b 补录** | 番茄钟页 + KOReader 落地页的日期格子 | ❌ 服务端零改动（Issue 通道本来就支持多天） |
| **S7c 历史** | `scripts/history.py` + `data/history.json` + 图鉴页历史块 | ❌ 只写 `data/history.json`，**不进账本** |

**Tech Stack:** Python 3.11 标准库 only（`unittest`）／纯 HTML+CSS+JS（无框架、无构建）／Resend HTTP API／Playwright（真实浏览器断言）

**上游文档：** `docs/superpowers/specs/2026-11-23-pokemon-dex-hub-s7-daily-loop-design.md`

## Global Constraints

- **Python 3.11，标准库 only** —— 不装任何第三方包
- 测试放在 `tests/test_*.py`，类名 `XxxTest`，方法名 `test_xxx`，**中文注释与中文 docstring**
- 跑测试一律 `python3 -B run_tests.py`（**NAS 的 `__pycache__` 属性缓存会坑人**，必须带 `-B`）
- **提交前**：`touch <文件> && git update-index --really-refresh && git status --short`
  —— 这台机 `git status` 与 `git add -A` 都会漏报改动
- **推送前**：`git fetch origin main && git rebase origin/main` —— 远端 main 会被 cron 推 commit
- `git -c http.expectContinue=false push origin HEAD:main`，`export GIT_SSL_NO_VERIFY=1`
- **一次提交只做一件事**；commit message 中文、动词开头
- 子页有「**只许发一个网络请求**」的测试纪律 ⇒ **不要为小功能往子页引额外脚本**
- 页面里凡是拉 JSON，都要有**超时 + 重试 + 可见错误**
- `scripts/dex_core.py` / `scripts/ledger.py` / `scripts/sources.py` **一行不改**（`sources.py` 的 `load_config` 是唯一的 IO 口）
- 现有读方依赖的字段**一个都不能删**（`collected[].id/nameZh/shiny/at`、`streak`、`today.seconds`、`today.threshold`）
- 抽卡权只在服务端 —— 客户端只报「我投入了多少」
- 每个任务结束都要 `python3 -B run_tests.py` 全绿

---

# Track S7a · 邮件通知（Resend）

## Task 1: `scripts/notify.py` 的纯逻辑（判定 + 幂等状态）

**Files:**
- Create: `scripts/notify.py`
- Create: `tests/test_notify.py`

**Interfaces:**
- Consumes: 无（纯逻辑，不依赖别的模块）
- Produces:
  - `shanghai_now(now=None) -> datetime.datetime`
  - `load_state(path=STATE_PATH) -> dict` —— `{"awarded": [...], "streak": {...}}`
  - `save_state(state, path=STATE_PATH) -> None`
  - `award_key(entry: dict) -> str` —— `"2026-11-22#0163"`
  - `pending_awards(collected: list, notified: list) -> list[dict]`
  - `should_warn_streak(streak, credit, per_award, now, warned_today) -> bool`

- [ ] **Step 1: 写失败的测试**

```python
"""邮件通知（`scripts/notify.py`）的测试。

重点盯三件事：
1. **补发的宝可梦也要通知** —— `award.py` 会一次结清所有待发奖日，
   所以「今天」这轮可能收服的是前天甚至大前天那只 ⇒ 判定不能只看 date == 今天。
2. **幂等** —— 同一条目（同一天同一只）永远只通知一次。
3. **连读提醒的四个条件** —— streak≥2、今天没达标、≥21:00、今天没发过。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile
import unittest

from scripts import notify


class AwardDetectTest(unittest.TestCase):
    """判定「哪些条目还没通知过」。"""

    def test_key_is_date_and_id(self):
        self.assertEqual(notify.award_key({"date": "2026-11-22", "id": "0163"}),
                         "2026-11-22#0163")

    def test_a_fresh_entry_is_pending(self):
        got = notify.pending_awards([{"date": "2026-11-22", "id": "0163"}], [])
        self.assertEqual(len(got), 1)

    def test_an_already_notified_entry_is_not_pending(self):
        entries = [{"date": "2026-11-22", "id": "0163"}]
        got = notify.pending_awards(entries, ["2026-11-22#0163"])
        self.assertEqual(got, [])

    def test_a_backfilled_older_day_is_still_pending(self):
        """★ 补发的也要通知 —— 不能按「是不是今天」筛。"""
        entries = [{"date": "2026-11-20", "id": "0471"}]
        got = notify.pending_awards(entries, [])
        self.assertEqual([e["id"] for e in got], ["0471"])

    def test_the_same_id_on_two_days_counts_twice(self):
        entries = [{"date": "2026-11-20", "id": "0471"},
                   {"date": "2026-11-22", "id": "0471"}]
        got = notify.pending_awards(entries, ["2026-11-20#0471"])
        self.assertEqual([e["date"] for e in got], ["2026-11-22"])

    def test_empty_input_gives_empty_output(self):
        self.assertEqual(notify.pending_awards([], []), [])


class StreakWarnTest(unittest.TestCase):
    """判定「连读快断了」。"""

    def _now(self, hour):
        return datetime.datetime(2026, 11, 23, hour, 0, 0)

    def test_warns_when_streak_is_two_and_not_reached(self):
        self.assertTrue(notify.should_warn_streak(2, 1, 2, self._now(21), False))

    def test_does_not_warn_when_streak_is_one(self):
        """连读只有 1 天时断了无所谓 —— 不打扰。"""
        self.assertFalse(notify.should_warn_streak(1, 0, 2, self._now(21), False))

    def test_does_not_warn_before_nine_pm(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(20), False))

    def test_does_not_warn_after_reaching_the_goal(self):
        self.assertFalse(notify.should_warn_streak(4, 2, 2, self._now(21), False))

    def test_does_not_warn_twice_in_one_day(self):
        self.assertFalse(notify.should_warn_streak(4, 0, 2, self._now(22), True))

    def test_warns_at_exactly_nine_pm(self):
        self.assertTrue(notify.should_warn_streak(4, 0, 2, self._now(21), False))


class StateFileTest(unittest.TestCase):
    """幂等标记存在哪儿。"""

    def test_missing_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            st = notify.load_state(os.path.join(d, "nope.json"))
            self.assertEqual(st, {"awarded": [], "streak": {}})

    def test_broken_file_gives_an_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            pathlib.Path(p).write_text("{ not json", encoding="utf-8")
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], [])

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": ["a#b"], "streak": {"2026-11-23": True}}, p)
            st = notify.load_state(p)
            self.assertEqual(st["awarded"], ["a#b"])
            self.assertTrue(st["streak"]["2026-11-23"])

    def test_state_file_is_written_as_utf8_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            notify.save_state({"awarded": [], "streak": {"2026-11-23": True}}, p)
            raw = pathlib.Path(p).read_text(encoding="utf-8")
            self.assertIn("2026-11-23", raw)
            self.assertEqual(json.loads(raw)["streak"]["2026-11-23"], True)
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.notify'`

- [ ] **Step 3: 写实现**

`scripts/notify.py`：

```python
"""邮件通知：收服了新的宝可梦 / 连读快断了。

**旁路脚本** —— 它只读 `data/dex.json`、只写 `data/notify_state.json`，
**绝不碰结算**（`dex_core` / `award` / `ledger` / `sources` 一行不改）。
任何失败都不能挡住发奖。

为什么状态不放 `dex.json`：`scripts/dex_core.py` 的 `build_state()` 返回的是
**重新构造的 dict**（只含固定字段），`award.py` 写回时会冲掉任何没被它
显式带上的字段 ⇒ 「发过没发过」只能放独立文件。

没配 `RESEND_API_KEY` 时静默跳过。
"""

from __future__ import annotations

import datetime
import json
import os

STATE_PATH = "data/notify_state.json"
# 「连读快断了」的提醒时刻（北京时间）
WARN_HOUR = 21
# 微信读书 1 额度 = 900 秒官方口径，但官方计数漏计约三成 ⇒ 体感约 ×1.4
WARN_MINUTES = 20

# 北京时间（UTC+8，无夏令时）
TZ_SH = datetime.timezone(datetime.timedelta(hours=8))


def shanghai_now(now: datetime.datetime | None = None) -> datetime.datetime:
    """当前北京时间。测试可以传 now 进来。"""
    if now is not None:
        return now
    return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_SH)


def load_state(path: str = STATE_PATH) -> dict:
    """读幂等标记。文件不存在或坏了都当空的。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"awarded": [], "streak": {}}
    if not isinstance(data, dict):
        return {"awarded": [], "streak": {}}
    data.setdefault("awarded", [])
    data.setdefault("streak", {})
    return data


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """写幂等标记。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def award_key(entry: dict) -> str:
    """条目身份：`日期#编号` —— 稳定、可重复算。"""
    return "%s#%s" % (entry.get("date", ""), entry.get("id", ""))


def pending_awards(collected: list, notified: list) -> list[dict]:
    """还没通知过的条目。

    ★ **不看日期** —— `award.py` 会一次结清所有待发奖日，
    所以「今天」这轮可能收服的是前天补发的那只 ⇒ 凡是没通知过的都要算。
    """
    seen = set(notified or [])
    out = []
    for entry in collected or []:
        key = award_key(entry)
        if not key or key == "#":
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def should_warn_streak(streak, credit, per_award, now: datetime.datetime,
                       warned_today: bool) -> bool:
    """今天要不要发「连读快断了」的提醒。

    四个条件全满足才发：
    - 连读 ≥ 2 天（连 1 天就断无所谓，不打扰）
    - 今天还没达标
    - 北京时间已经 ≥ 21:00（早上别催）
    - 今天还没发过（一天最多一封）
    """
    if warned_today:
        return False
    if int(streak or 0) < 2:
        return False
    if int(credit or 0) >= int(per_award or 1):
        return False
    if now.hour < WARN_HOUR:
        return False
    return True
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: PASS —— 14 tests OK

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch scripts/notify.py tests/test_notify.py && git update-index --really-refresh
git add scripts/notify.py tests/test_notify.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 邮件通知的判定逻辑与幂等状态文件"
```

---

## Task 2: 邮件正文 + Resend 发送

**Files:**
- Modify: `scripts/notify.py`
- Modify: `tests/test_notify.py`

**Interfaces:**
- Consumes: Task 1 的 `pending_awards` / `should_warn_streak`
- Produces:
  - `load_pokedex(path="site/pokedex.json") -> dict[str, dict]` —— `{编号: 条目}`
  - `build_award_email(entries, pokedex, streak, total, source_text) -> tuple[str, str]`
  - `build_streak_email(missing, streak, per_award) -> tuple[str, str]`
  - `send_email(subject, html, api_key=None, to_addr=None, opener=None) -> bool`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_notify.py`：

```python
class EmailContentTest(unittest.TestCase):
    """邮件正文：要有立绘、名字、闪光标、连读天数、连读提醒的安慰话。"""

    POKEDEX = {
        "0961": {"id": "0961", "nameZh": "三海地鼠", "types": ["水", "地面"],
                 "art": "0961.webp", "artShiny": "0961-shiny.webp"},
    }

    def test_award_email_names_the_pokemon(self):
        subject, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟 + 番茄钟 25 分钟")
        self.assertIn("三海地鼠", subject)
        self.assertIn("三海地鼠", html)
        self.assertIn("0961.webp", html)

    def test_award_email_marks_shiny(self):
        _, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": True}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("闪光", html)
        self.assertIn("0961-shiny.webp", html)

    def test_award_email_shows_the_award_day(self):
        """补发的要标出是哪天的 —— 不能让人以为是今天收的。"""
        _, html = notify.build_award_email(
            [{"date": "2026-11-20", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("2026-11-20", html)

    def test_award_email_shows_progress(self):
        _, html = notify.build_award_email(
            [{"date": "2026-11-23", "id": "0961", "nameZh": "三海地鼠", "shiny": False}],
            self.POKEDEX, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("9", html)
        self.assertIn("1028", html)

    def test_award_email_for_many_pokemon(self):
        subject, html = notify.build_award_email(
            [{"date": "2026-11-21", "id": "0471", "nameZh": "冰伊布", "shiny": False},
             {"date": "2026-11-22", "id": "0163", "nameZh": "咕咕", "shiny": False}],
            {}, streak=4, total=9, source_text="微信读书 30 分钟")
        self.assertIn("2", subject)
        self.assertIn("冰伊布", html)
        self.assertIn("咕咕", html)

    def test_streak_email_states_the_streak(self):
        subject, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("连读", subject)
        self.assertIn("4", html)
        self.assertIn("1", html)

    def test_streak_email_says_it_is_not_a_punishment(self):
        """对齐「未达标 = 没有惩罚」的设计 —— 别吓她。"""
        _, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("没有惩罚", html)

    def test_streak_email_gives_a_readable_minutes_figure(self):
        """官方 900 秒 = 1 额度，但官方漏计三成 ⇒ 文案写 20 分钟不是 15。"""
        _, html = notify.build_streak_email(missing=1, streak=4, per_award=2)
        self.assertIn("20 分钟", html)


class SendMailTest(unittest.TestCase):
    """发信：不配 key 就跳过；网络炸了不许抛异常。"""

    def test_without_api_key_it_skips_quietly(self):
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="", to_addr="a@b.c"))

    def test_without_recipient_it_skips_quietly(self):
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="re_x", to_addr=""))

    def test_it_posts_to_resend(self):
        """用 opener 假造一次发送，检查请求体。"""
        captured = {}

        class FakeResp:
            def read(self):
                return b'{"id":"abc"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_opener(req):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["headers"] = req.headers
            return FakeResp()

        ok = notify.send_email("标题", "<p>正文</p>", api_key="re_x",
                                to_addr="me@example.com", opener=fake_opener)
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "https://api.resend.com/emails")
        self.assertEqual(captured["body"]["subject"], "标题")
        self.assertEqual(captured["body"]["to"], ["me@example.com"])
        self.assertIn("re_x", str(captured["headers"]))
        # ★ 必须带浏览器 UA —— 否则 Cloudflare 403（error code 1010）
        self.assertIn("Mozilla/5.0", str(captured["headers"]))

    def test_a_network_error_does_not_raise(self):
        def boom(req):
            raise OSError("network down")
        self.assertFalse(notify.send_email("t", "<p>h</p>", api_key="re_x",
                                            to_addr="me@example.com", opener=boom))
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: FAIL —— `AttributeError: module 'scripts.notify' has no attribute 'build_award_email'`

- [ ] **Step 3: 写实现**

追加到 `scripts/notify.py`：

```python
# ---------- 邮件正文 ----------

SITE_URL = "https://lilia-shound.github.io/pokemon-dex/"
IMG_BASE = SITE_URL + "img/"
FROM_ADDRESS = "训练家图鉴 <onboarding@resend.dev>"
RESEND_URL = "https://api.resend.com/emails"
# Resend 前面有 Cloudflare：urllib 默认 UA（Python-urllib/x.y）会被 403
# （`error code: 1010` = 按浏览器签名封的）⇒ 必须带一个像浏览器的 UA。
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TOTAL_POKEMON = 1028

# 邮件里用得到的一小段样式 —— 客户端大多会忽略 <style>，所以关键样式都写成内联。
_MAIL_CSS = (
    "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Noto Sans CJK SC',"
    "'Microsoft YaHei',sans-serif;color:#2f2823;background:#f6f1e7;"
    "padding:24px;border-radius:16px;"
)
_BADGE = ("display:inline-block;padding:4px 14px;border-radius:999px;"
          "font-size:13px;font-weight:600;")


def load_pokedex(path: str = "site/pokedex.json") -> dict:
    """{编号: 条目} —— 拿不到就返回空 dict（邮件里少一行属性而已，不影响发信）。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for item in (raw or {}).get("pokemon") or []:
        pid = item.get("id")
        if pid:
            out[pid] = item
    return out


def _pokemon_card(entry: dict, pokedex: dict) -> str:
    """一只宝可梦的邮件卡片。"""
    pid = str(entry.get("id", ""))
    info = pokedex.get(pid) or {}
    name = entry.get("nameZh") or info.get("nameZh") or pid
    shiny = bool(entry.get("shiny"))
    art = info.get("artShiny") if shiny else info.get("art")
    if not art:
        art = (pid or "0000") + ("-shiny.webp" if shiny else ".webp")
    types = " · ".join(info.get("types") or [])
    day = entry.get("date", "")
    badge = ""
    if shiny:
        badge = ('<span style="%sbackground:#c9a227;color:#fffdf8;">✨ 闪光</span>'
                 % _BADGE)
    return (
        '<div style="background:#fffdf8;border:1px solid #e9e0d0;border-radius:16px;'
        'padding:16px;margin:12px 0;text-align:center;">'
        '<img src="%s%s" alt="%s" width="180" style="display:block;margin:0 auto 8px;"/>'
        '<div style="font-size:20px;font-weight:700;">%s</div>'
        '<div style="font-size:13px;color:#9a8d80;margin-top:4px;">No.%s%s</div>'
        '<div style="font-size:13px;color:#9a8d80;margin-top:2px;">%s</div>'
        '<div style="margin-top:8px;">%s</div>'
        '<div style="font-size:12px;color:#9a8d80;margin-top:8px;">收服于 %s</div>'
        '</div>'
        % (IMG_BASE, art, name, name, pid, (" · " + types) if types else "",
           info.get("category", ""), badge, day)
    )


def build_award_email(entries: list, pokedex: dict, streak: int, total: int,
                       source_text: str = "") -> tuple[str, str]:
    """收服邮件：主题 + HTML。"""
    entries = list(entries or [])
    if not entries:
        return ("", "")
    names = [e.get("nameZh") or e.get("id") for e in entries]
    if len(entries) == 1:
        subject = "训练家图鉴 · 收服了 %s No.%s" % (names[0], entries[0].get("id", ""))
    else:
        subject = "训练家图鉴 · 收服了 %d 只宝可梦" % len(entries)
    cards = "".join(_pokemon_card(e, pokedex or {}) for e in entries)
    tail = ('<div style="font-size:12px;color:#9a8d80;margin-top:6px;">%s</div>'
            % source_text) if source_text else ""
    html = (
        '<div style="%s">'
        '<div style="font-size:18px;font-weight:700;">训练家图鉴</div>'
        '<div style="font-size:13px;color:#9a8d80;margin:4px 0 12px;">'
        '连读 %d 天 · 图鉴 %d/%d</div>'
        '%s'
        '<a href="%s" style="display:inline-block;background:#c8623a;color:#fffdf8;'
        'padding:10px 22px;border-radius:999px;text-decoration:none;font-size:14px;'
        'font-weight:600;">去看图鉴</a>'
        '%s'
        '</div>'
        % (_MAIL_CSS, int(streak or 0), int(total or 0), TOTAL_POKEMON, cards,
           SITE_URL, tail)
    )
    return (subject, html)


def build_streak_email(missing: int, streak: int, per_award: int) -> tuple[str, str]:
    """连读提醒：主题 + HTML。

    语气要温和 —— 这个项目的规则是「未达标 = 没有惩罚」，别把它写成警报。
    """
    missing = max(1, int(missing or 1))
    subject = "今天的连读还差 %d 个额度" % missing
    html = (
        '<div style="%s">'
        '<div style="font-size:18px;font-weight:700;">今天的连读还差 %d 个额度</div>'
        '<div style="font-size:14px;margin-top:10px;line-height:1.7;">'
        '你今天还没凑够 %d 个额度，<b>%d 天连读</b>会在今晚归零。<br/>'
        '再读 <b>%d 分钟</b>就够了 —— 或者来一轮番茄钟。<br/>'
        '<span style="color:#9a8d80;font-size:13px;">'
        '断了也没有惩罚，明天照样能重新开始。</span></div>'
        '<a href="%s" style="display:inline-block;margin-top:14px;background:#c8623a;'
        'color:#fffdf8;padding:10px 22px;border-radius:999px;text-decoration:none;'
        'font-size:14px;font-weight:600;">去图鉴</a>'
        '</div>'
        % (_MAIL_CSS, missing, int(per_award or 2), int(streak or 0),
           missing * WARN_MINUTES, SITE_URL)
    )
    return (subject, html)


# ---------- 发送 ----------

def send_email(subject: str, html: str, api_key: str | None = None,
               to_addr: str | None = None, opener=None) -> bool:
    """发一封邮件。**任何失败都只返回 False，不抛异常** —— 通知是旁路。

    api_key / to_addr 不传时从环境变量 RESEND_API_KEY / RESEND_TO 取。
    opener 是 urllib.request.urlopen 的替身（测试用）。
    """
    api_key = api_key if api_key is not None else os.environ.get("RESEND_API_KEY", "")
    to_addr = to_addr if to_addr is not None else os.environ.get("RESEND_TO", "")
    if not api_key or not to_addr:
        print("[通知] 未配置 RESEND_API_KEY / RESEND_TO，跳过")
        return False
    payload = {
        "from": FROM_ADDRESS,
        "to": [to_addr],
        "subject": subject,
        "html": html,
    }
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key,
                 # ★ 必须带浏览器样的 UA —— Resend 前面有 Cloudflare，
                 #   urllib 默认 UA 是 `Python-urllib/3.11` ⇒ 被 403
                 #   （`error code: 1010` = 按浏览器签名封的）。实测加 UA 就 200 了。
                 "User-Agent": BROWSER_UA},
        method="POST",
    )
    try:
        op = opener or urllib.request.urlopen
        with op(req, timeout=20) as resp:
            body = resp.read()
        print("[通知] 已发送: " + subject)
        return True
    except Exception as exc:  # noqa: BLE001 —— 旁路不许把整个流程带崩
        print("[通知] 失败: %s" % exc)
        return False
```

并在文件顶部补上 `import urllib.request`。

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: PASS —— 24 tests OK

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch scripts/notify.py tests/test_notify.py && git update-index --really-refresh
git add scripts/notify.py tests/test_notify.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 邮件正文与 Resend 发送（旁路，失败不阻塞结算）"
```

---

## Task 3: `main()` —— 读状态、判定、发信、记幂等

**Files:**
- Modify: `scripts/notify.py`
- Modify: `tests/test_notify.py`
- Modify: `README.md`（加「邮件通知」一节）

**Interfaces:**
- Consumes: Task 1 的 `pending_awards` / `should_warn_streak` / `load_state` / `save_state`；Task 2 的 `build_award_email` / `build_streak_email` / `send_email`
- Produces: `notify.main(dex_path=..., state_path=..., now=None, sender=send_email) -> dict`

**返回的 dict**（给 cron 汇报用）：`{"sent": [...], "skipped": [...], "reason": str}`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_notify.py`：

```python
class MainFlowTest(unittest.TestCase):
    """端到端：读 dex.json → 判定 → 发 → 记幂等。"""

    def _dex(self, **kw):
        base = {
            "version": 2,
            "collected": [],
            "streak": 0,
            "today": {"date": "2026-11-23", "seconds": 0, "threshold": 1800,
                      "sources": {}, "credit": 0, "perAward": 2},
        }
        base.update(kw)
        return base

    def _run(self, dex, now, state=None):
        d = tempfile.mkdtemp()
        dex_p = os.path.join(d, "dex.json")
        st_p = os.path.join(d, "state.json")
        pathlib.Path(dex_p).write_text(json.dumps(dex, ensure_ascii=False),
                                        encoding="utf-8")
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        res = notify.main(dex_path=dex_p, state_path=st_p, now=now, sender=fake_sender)
        return res, sent

    def test_a_new_pokemon_triggers_one_email(self):
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        res, sent = self._run(dex, datetime.datetime(2026, 11, 23, 10, 0))
        self.assertEqual(len(sent), 1)
        self.assertIn("三海地鼠", sent[0])
        self.assertEqual(res["sent"], sent)

    def test_running_twice_does_not_send_twice(self):
        """★ 幂等 —— 第二轮不该再发一遍。"""
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        d = tempfile.mkdtemp()
        dex_p = os.path.join(d, "dex.json")
        st_p = os.path.join(d, "state.json")
        pathlib.Path(dex_p).write_text(json.dumps(dex, ensure_ascii=False),
                                        encoding="utf-8")
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 0), sender=fake_sender)
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 30), sender=fake_sender)
        self.assertEqual(len(sent), 1)

    def test_streak_warning_is_sent_after_nine_pm(self):
        dex = self._dex(streak=4, today={"date": "2026-11-23", "seconds": 0,
                                         "threshold": 1800, "sources": {},
                                         "credit": 0, "perAward": 2})
        res, sent = self._run(dex, datetime.datetime(2026, 11, 23, 21, 30))
        self.assertEqual(len(sent), 1)
        self.assertIn("连读", sent[0])

    def test_streak_warning_is_not_sent_in_the_morning(self):
        dex = self._dex(streak=4)
        res, sent = self._run(dex, datetime.datetime(2026, 11, 23, 9, 0))
        self.assertEqual(sent, [])

    def test_streak_warning_only_once_per_day(self):
        dex = self._dex(streak=4)
        d = tempfile.mkdtemp()
        dex_p = os.path.join(d, "dex.json")
        st_p = os.path.join(d, "state.json")
        pathlib.Path(dex_p).write_text(json.dumps(dex, ensure_ascii=False),
                                        encoding="utf-8")
        sent = []
        def fake_sender(subject, html, **kw):
            sent.append(subject)
            return True
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 21, 30), sender=fake_sender)
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 22, 30), sender=fake_sender)
        self.assertEqual(len(sent), 1)

    def test_nothing_to_say_sends_nothing(self):
        res, sent = self._run(self._dex(), datetime.datetime(2026, 11, 23, 21, 30))
        self.assertEqual(sent, [])

    def test_a_failed_send_does_not_mark_it_as_notified(self):
        """发失败 ⇒ 下轮还要重发（宁可重发不要漏发）。"""
        dex = self._dex(collected=[{"date": "2026-11-23", "id": "0961",
                                    "nameZh": "三海地鼠", "shiny": False}])
        d = tempfile.mkdtemp()
        dex_p = os.path.join(d, "dex.json")
        st_p = os.path.join(d, "state.json")
        pathlib.Path(dex_p).write_text(json.dumps(dex, ensure_ascii=False),
                                        encoding="utf-8")
        sent = []
        def failing(subject, html, **kw):
            sent.append(subject)
            return False
        notify.main(dex_path=dex_p, state_path=st_p,
                    now=datetime.datetime(2026, 11, 23, 10, 0), sender=failing)
        st = notify.load_state(st_p)
        self.assertEqual(st["awarded"], [])
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: FAIL —— `AttributeError: module 'scripts.notify' has no attribute 'main'`

- [ ] **Step 3: 写实现**

追加到 `scripts/notify.py`：

```python
# ---------- 主流程 ----------

def _load_dex(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _source_text(dex: dict) -> str:
    """「这轮的投入：微信读书 30 分钟 + 番茄钟 25 分钟」—— 让她知道是哪些凑的。"""
    today = dex.get("today") or {}
    sources = today.get("sources") or {}
    names = {"weread": "微信读书", "pom": "番茄钟", "koreader": "KOReader",
             "words": "背单词"}
    units = {"weread": "秒", "pom": "分钟", "koreader": "分钟", "words": "个"}
    bits = []
    for sid, amount in sorted(sources.items()):
        if not amount:
            continue
        bits.append("%s %s %s" % (names.get(sid, sid), amount, units.get(sid, "")))
    return " + ".join(bits)


def main(dex_path: str = "data/dex.json", state_path: str = STATE_PATH,
         now: datetime.datetime | None = None, sender=None) -> dict:
    """跑一轮通知。返回 {sent: [...], skipped: [...], reason: str}。

    sender 传进来的话用它发信（测试用）；不传就用 send_email。
    """
    sender = sender or send_email
    now = now or shanghai_now()
    dex = _load_dex(dex_path)
    state = load_state(state_path)
    sent = []
    skipped = []

    # ---- 收服了新宝可梦 ----
    pending = pending_awards(dex.get("collected") or [], state.get("awarded") or [])
    if pending:
        pokedex = load_pokedex()
        subject, html = build_award_email(
            pending, pokedex,
            streak=int(dex.get("streak") or 0),
            total=len(dex.get("collected") or []),
            source_text=_source_text(dex),
        )
        if sender(subject, html):
            # 先发后记 —— 发失败就不记，下轮还会重发
            state.setdefault("awarded", []).extend(award_key(e) for e in pending)
            sent.append(subject)
        else:
            skipped.append(subject)

    # ---- 连读快断了 ----
    today = (dex.get("today") or {}).get("date") or now.strftime("%Y-%m-%d")
    per_award = int((dex.get("today") or {}).get("perAward") or 2)
    credit = int((dex.get("today") or {}).get("credit") or 0)
    warned = bool((state.get("streak") or {}).get(today))
    if should_warn_streak(int(dex.get("streak") or 0), credit, per_award, now, warned):
        missing = max(1, per_award - credit)
        subject, html = build_streak_email(missing, int(dex.get("streak") or 0), per_award)
        if sender(subject, html):
            state.setdefault("streak", {})[today] = True
            sent.append(subject)
        else:
            skipped.append(subject)

    if sent or skipped:
        save_state(state, state_path)
    reason = "无" if not (sent or skipped) else ("已发 %d 封" % len(sent))
    return {"sent": sent, "skipped": skipped, "reason": reason}


if __name__ == "__main__":
    result = main()
    for s in result["sent"]:
        print("[通知] 已发送: " + s)
    for s in result["skipped"]:
        print("[通知] 发送失败（下一轮会重试）: " + s)
    print("[通知] " + result["reason"])
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_notify
```

Expected: PASS —— 31 tests OK

- [ ] **Step 5: 跑全套测试**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: 全绿（现有 575 + 新增 31）

- [ ] **Step 6: README 加一节**

在 `README.md` 的「谁在跑结算」之后插一段：

```markdown
### 邮件通知

掉宝可梦时给你发一封邮件；连读快断了（连读 ≥ 2 天、当天没达标、晚上 9 点还没读够）
也会发一封。

- 走 **Resend**（`RESEND_API_KEY` + `RESEND_TO`，配在 Mavis 的 secret 里，不进仓库）
- 脚本是 `scripts/notify.py`，**旁路** —— 它只读 `data/dex.json`、只写 `data/notify_state.json`，
  结算核心一行不改；发信失败也不会影响发奖
- 幂等靠 `data/notify_state.json`：同一条宝可梦只通知一次，一天最多一封连读提醒
- ★ **补发的宝可梦也会通知** —— `award.py` 会一次结清所有待发奖日，
  所以判定看的是「有没有通知过」，不是「是不是今天」
- 没配 key 时静默跳过（cron 汇报里会如实说一句）
```

- [ ] **Step 7: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch scripts/notify.py tests/test_notify.py README.md && git update-index --really-refresh
git add scripts/notify.py tests/test_notify.py README.md
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 邮件通知主流程与文档"
```

---

## Task 4: 同步 cron 提示词（口径的权威副本）

**Files:**
- 无文件（改 Mavis cron 任务 `463191742394721` 的提示词）

**Interfaces:**
- Consumes: Task 3 的 `scripts/notify.py`
- Produces: cron 每轮在 `award.py` 之后跑 `python3 scripts/notify.py`

- [ ] **Step 1: 读现有 cron 提示词**

```bash
mavis({ command: "cron help" })   # 或 tool_search 找 cron_read
```

用 `cron_read`（`view="detail"`, `task_id="463191742394721"`）拿到完整提示词。

- [ ] **Step 2: 在提示词的「判定步骤」里加第 3.5 步**

在 `3.` 与 `4.` 之间插入（**只加这一段，别重写整个提示词**）：

```
   3.5 `cd /workspace/pokemon-reading-dex && python3 scripts/notify.py` —— 发邮件通知
      - 输出含「[通知] 已发送」= 发出去了一封（收服了新宝可梦 / 连读快断了）
      - 输出含「[通知] 未配置」= 姜来还没配 Resend 的 key ⇒ **如实说一句「邮件通知没开」**，
        不要假装发了
      - 输出含「[通知] 失败」= 发信失败 ⇒ 如实说失败，**不要重试、不要改代码**
      - 这个脚本是**旁路**：它失败也不影响前面已经发过的奖
```

并在「汇报规则」里加一条：

```
   - 邮件发出去了（notify.py 说已发送）：在汇报里提一句「邮件也发了」
```

- [ ] **Step 3: 更新 cron 提示词**

用 `cron_update`（`task_id="463191742394721"`, `prompt=<完整新提示词>`）。
**必须传完整提示词**（它是覆盖，不是追加）—— 所以先 `cron_read` 拿到原文，在原文上改。

- [ ] **Step 4: 验证**

```bash
# 确认提示词里确实多了 notify.py 这一步
mavis({ command: "cron help" })
```

用 `cron_read view="detail"` 重新读一遍，确认 `scripts/notify.py` 出现在提示词里。

- [ ] **Step 5: 告诉姜来需要她提供的**

Resend 的 API key（`re_` 开头）和收件人邮箱 —— 用 `secret` 工具存成
`RESEND_API_KEY` / `RESEND_TO`。**先跟她说清楚要什么**，别自己瞎编。

---

# Track S7b · 补录最近 7 天

> **服务端零改动** —— GitHub Issue 投递箱本来就支持一次最多 30 天、同源同日取 max。
> 要做的只是页面上的「选日期」入口。

## Task 5: 番茄钟页的日期格子

**Files:**
- Modify: `site/pomodoro/app.js`
- Modify: `site/pomodoro/index.html`
- Modify: `site/pomodoro/style.css`
- Create: `tests/test_pomodoro_backfill.py`

**Interfaces:**
- Consumes: `site/words/app.js` 的「最近 7 天」模式（照抄）
- Produces: 番茄钟页能编辑过去某一天的分钟数；`Store.st.days[day].minutes` 是唯一真值
  （`syncDays()` 已经会把它一起投上去 ⇒ **投递逻辑零改动**）

- [ ] **Step 1: 写失败的测试**

`tests/test_pomodoro_backfill.py`：

```python
"""番茄钟页的补录（改过去某一天的分钟数）。

真值只有一处：`Store.st.days[day].minutes`。
`syncDays()` 已经会把所有「有分钟的天」一起投上去（最近 30 天）⇒
补录**不需要动投递逻辑**，只要能把某一天的分钟数写进去。

跑法：python3 -B run_tests.py
"""

from __future__ import annotations

import http.server
import pathlib
import re
import shutil
import socketserver
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
POM_DIR = ROOT / "site" / "pomodoro"
APP_JS = POM_DIR / "app.js"
INDEX_HTML = POM_DIR / "index.html"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"


class PomodoroBackfillTest(unittest.TestCase):
    """静态断言：页面有日期格子、有写回 Store 的入口。"""

    def test_the_page_has_a_day_picker(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("dayStrip", html)

    def test_the_day_strip_is_empty_in_the_markup(self):
        """格子由 JS 渲染 —— 静态 HTML 里不该有写死的天数。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotRegex(html, r'id="dayStrip"[^>]*>\s*<div[^>]*class="day"')

    def test_the_script_writes_back_to_the_store(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("dayStrip", js)
        self.assertRegex(js, r"\.days\[")
        self.assertRegex(js, r"minutes\s*=")

    def test_the_script_limits_the_picker_to_seven_days(self):
        js = APP_JS.read_text(encoding="utf-8")
        m = re.search(r"var\s+PICK_DAYS\s*=\s*(\d+)", js)
        self.assertIsNotNone(m, "页面里要有一个写死的 PICK_DAYS 常量")
        self.assertEqual(int(m.group(1)), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_pomodoro_backfill
```

Expected: FAIL —— `AssertionError: 'dayStrip' not found in '...'`

- [ ] **Step 3: 写实现**

**`site/pomodoro/index.html`** —— 在「交上去」那节（`id="syncCard"`）**之前**插入：

```html
    <!-- 补录：改过去某一天的分钟数（额度按天算，漏记一天那天就归零） -->
    <section class="card" id="dayCard">
      <h3>补记某一天</h3>
      <p class="hint">点某一天，填那天的专注分钟数。填完一起「同步到图鉴」—— 额度按天算，漏记一天那天就归零。</p>
      <div class="days" id="dayStrip"></div>
      <div id="dayEditor" class="dayEditor" hidden>
        <div class="dayEditorHead"><b id="dayEditorTitle"></b><button class="btn mini ghost" id="dayClose">收起</button></div>
        <div class="row" style="gap:8px;margin-top:8px">
          <input class="num" id="dayMinutes" type="number" min="0" max="1440" step="5" inputmode="numeric" placeholder="分钟">
          <button class="btn" id="daySave">记下</button>
        </div>
        <p class="hint" id="dayHint"></p>
      </div>
    </section>
```

**`site/pomodoro/app.js`** —— 在文件末尾追加（并在 `window` 上暴露 `window.PomodoroApp` 方便测试）：

```javascript
/* ---------- 补录：改过去某一天的分钟数 ---------- */
/** 页面上能点选的天数（与服务端一次最多收的天数 30 对齐，这里只看最近 7 天） */
var PICK_DAYS = 7;
/** 最近 N 天的日期串（今天在前）。 */
function pickDays(n) {
  var out = [];
  for (var i = 0; i < n; i++) {
    var d = new Date();
    d.setDate(d.getDate() - i);
    out.push(d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()));
  }
  return out;
}

/** 画那排日期格子。已记过的显示分钟数。 */
function renderDayStrip() {
  var el = document.getElementById('dayStrip');
  if (!el) return;
  var days = pickDays(PICK_DAYS);
  el.innerHTML = days.map(function (k) {
    var v = (S().days[k] || {}).minutes || 0;
    return '<button class="day' + (v > 0 ? ' has' : '') + '" data-day="' + k + '">' +
      '<span class="d">' + k.slice(5) + '</span>' +
      '<span class="w">' + ['日', '一', '二', '三', '四', '五', '六'][new Date(k).getDay()] + '</span>' +
      '<span class="v">' + (v > 0 ? v + ' 分' : '—') + '</span>' +
      '</button>';
  }).join('');
  for (var i = 0; i < el.querySelectorAll('.day').length; i++) {
    (function (btn) {
      btn.addEventListener('click', function () { openDayEditor(btn.getAttribute('data-day')); });
    })(el.querySelectorAll('.day')[i]);
  }
}

/** 展开某一天的编辑器。 */
function openDayEditor(day) {
  var ed = document.getElementById('dayEditor');
  var title = document.getElementById('dayEditorTitle');
  var input = document.getElementById('dayMinutes');
  var hint = document.getElementById('dayHint');
  if (!ed) return;
  ed.hidden = false;
  title.textContent = day + ' 专注了多久？';
  input.value = (S().days[day] || {}).minutes || '';
  hint.textContent = day === todayStr() ? '就是今天。' : '补记过去的日子 —— 额度按天算，不跨天凑。';
  input.focus();
}

/** 把这一天的分钟数写回 Store（唯一真值）。 */
function saveDay() {
  var day = document.getElementById('dayEditor').getAttribute('data-day');
  var input = document.getElementById('dayMinutes');
  var n = Math.max(0, Math.min(1440, parseInt(input.value, 10) || 0));
  var row = S().days[day] || (S().days[day] = { minutes: 0, poms: 0 });
  row.minutes = n;
  if (n === 0) { row.poms = 0; }
  Store.save();
  renderDayStrip();
  renderSync();
  document.getElementById('dayEditor').hidden = true;
}

/** 编辑器要记住是哪一天。 */
function bindDayEditor() {
  var ed = document.getElementById('dayEditor');
  var save = document.getElementById('daySave');
  var close = document.getElementById('dayClose');
  if (!ed || !save || !close) return;
  save.addEventListener('click', saveDay);
  close.addEventListener('click', function () { ed.hidden = true; });
}
```

**接线**：在现有的初始化函数里（找 `renderSync()` 被调用的那个地方）加两行：

```javascript
  renderDayStrip();
  bindDayEditor();
```

> ⚠️ **`MAX_SYNC_DAYS` 番茄钟页本来就有**（`syncDays()` 用它限制投递天数）⇒
> 上面只新增 `PICK_DAYS`，**不要重复声明 `MAX_SYNC_DAYS`**。

**`site/pomodoro/style.css`** —— 追加：

```css
/* ---------- 补录：日期格子 ---------- */
.days{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-top:10px}
.day{border:1px solid var(--line);background:var(--card);border-radius:12px;padding:8px 2px;
     display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer;
     font-family:inherit;color:var(--ink);min-width:0}
.day:hover{border-color:var(--accent)}
.day.has{background:var(--accent-soft);border-color:var(--accent)}
.day .d{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}
.day .w{font-size:10px;color:var(--dim)}
.day .v{font-size:10px;color:var(--accent)}
.dayEditor{margin-top:12px;padding:12px;border:1px dashed var(--line);border-radius:12px}
.dayEditorHead{display:flex;align-items:center;justify-content:space-between;gap:8px}
.num{flex:1;min-width:0;border:1px solid var(--line);border-radius:10px;padding:8px 10px;
     font-size:16px;background:var(--bg);color:var(--ink);font-family:inherit}
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_pomodoro_backfill
```

Expected: PASS —— 4 tests OK

- [ ] **Step 5: 用真浏览器验一遍（手工）**

```bash
cd /workspace/pokemon-reading-dex/site/pomodoro && python3 -m http.server 8901 &
```

用 Playwright 打开 `http://127.0.0.1:8901/`，点日期格子 → 填分钟 → 点「记下」⇒
格子上的数字要变；刷新页面后数字还在（localStorage）。

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch site/pomodoro/app.js site/pomodoro/index.html site/pomodoro/style.css tests/test_pomodoro_backfill.py
git update-index --really-refresh
git add site/pomodoro/app.js site/pomodoro/index.html site/pomodoro/style.css tests/test_pomodoro_backfill.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 番茄钟页支持补记过去某一天的专注分钟数"
```

---

## Task 6: KOReader 落地页的日期格子

**Files:**
- Modify: `site/koreader/app.js`
- Modify: `site/koreader/index.html`
- Modify: `site/koreader/style.css`
- Create: `tests/test_koreader_backfill.py`

**Interfaces:**
- Consumes: KOReader 落地页已有的 `parseDays` / `encodeDays` / `buildReport`
- Produces: 能手动加一天（日期 + 分钟），合并进 `state.days` ⇒
  `buildReport(state.reportDays)` 自动带上它

**★ 关键**：KOReader 落地页的 `state.days` 来自**短链**（插件生成的 `?days=...`）。
手动补的天要**合并进 `state.days`**，然后重新走一遍 `clampDays` → `render`。
页面已有 `state` 对象与 `render()` 流程 ⇒ 复用，不新造。

- [ ] **Step 1: 写失败的测试**

`tests/test_koreader_backfill.py`：

```python
"""KOReader 落地页的补录（手动加一天）。"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "site" / "koreader" / "app.js"
INDEX_HTML = ROOT / "site" / "koreader" / "index.html"


class KoreaderBackfillTest(unittest.TestCase):
    def test_the_page_has_a_manual_day_form(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("manualDay", html)

    def test_the_script_merges_manual_days_into_state(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("manualDay", js)
        self.assertRegex(js, r"state\.days\[")

    def test_manual_days_are_clamped_too(self):
        """手动加的天也要过单日上限 —— 不然能被刷额度。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertRegex(js, r"clampDays\(state\.days")

    def test_the_report_still_only_sends_one_request(self):
        """子页纪律：只许发一个网络请求 —— 别为补录引新脚本。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
        self.assertLessEqual(len(scripts), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_koreader_backfill
```

Expected: FAIL —— `AssertionError: 'manualDay' not found in '...'`

- [ ] **Step 3: 写实现**

**`site/koreader/index.html`** —— 在输入区之后插一段：

```html
    <!-- 补录：手动加一天（短链里没带的那天） -->
    <div class="manual" id="manualDay">
      <label for="manualDate">漏了哪一天？手动补上</label>
      <div class="row">
        <input id="manualDate" type="date">
        <input id="manualMinutes" type="number" min="0" max="1440" step="5" inputmode="numeric" placeholder="分钟">
        <button id="manualAdd" type="button">加上</button>
      </div>
      <p class="hint" id="manualHint"></p>
    </div>
```

**`site/koreader/app.js`** —— 在 `state` 定义之后追加：

```javascript
  /* ---------- 补录：手动加一天 ---------- */

  /**
   * 手动加一天：把日期与分钟并进 `state.days`，再走一遍「夹上限 → 重算额度 → 重画」。
   * ★ 手动加的天**也要过单日上限** —— 不然能被刷额度。
   */
  function addManualDay() {
    var dateEl = document.getElementById('manualDate');
    var minEl = document.getElementById('manualMinutes');
    var hint = document.getElementById('manualHint');
    var day = (dateEl && dateEl.value || '').trim();
    var minutes = parseInt(minEl && minEl.value || '', 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
      if (hint) hint.textContent = '先选一个日期。';
      return false;
    }
    if (!(minutes >= 0) || isNaN(minutes)) {
      if (hint) hint.textContent = '填一下那天的分钟数。';
      return false;
    }
    state.days[day] = minutes;
    if (hint) hint.textContent = '记下了 ' + day + ' —— ' + minutes + ' 分钟。';
    if (minEl) minEl.value = '';
    render();
    return true;
  }

  function bindManualDay() {
    var btn = document.getElementById('manualAdd');
    if (btn) btn.addEventListener('click', addManualDay);
  }
```

并在初始化流程里（`bind...` 那一串旁边）加 `bindManualDay();`，
在 `render()` 里（算 `state.reportDays = clamped.days;` 之前）确保走的是
`clampDays(state.days, ...)` ⇒ 现有代码已经是 `state.reportDays = clamped.days;`，
只要 `clamped` 是由 `state.days` 算出来的就自动覆盖 ⇒ **确认 `render()` 里
`clampDays` 的入参是 `state.days` 而不是别的**；若是别的，改成 `state.days`。

**`site/koreader/style.css`** —— 追加：

```css
/* ---------- 补录 ---------- */
.manual{margin-top:14px;padding:12px;border:1px dashed var(--line);border-radius:12px}
.manual label{display:block;font-size:12px;color:var(--dim);margin-bottom:6px}
.manual .row{display:flex;gap:8px;flex-wrap:wrap}
.manual input{flex:1;min-width:110px;border:1px solid var(--line);border-radius:10px;
              padding:8px 10px;font-size:16px;background:var(--bg);color:var(--ink);
              font-family:inherit}
.manual button{border:0;border-radius:10px;background:var(--accent);color:#fffdf8;
               padding:8px 16px;font-size:14px;font-family:inherit;cursor:pointer}
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_koreader_backfill
```

Expected: PASS —— 4 tests OK

- [ ] **Step 5: 跑全套测试（子页「只许一个网络请求」的纪律可能被打破）**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: 全绿。若 `tests/test_koreader_landing.py::…::test_the_page_sends_exactly_one_network_request` 红了 ⇒
说明引了额外脚本 ⇒ **回退成把逻辑写进已有的 `app.js`**。

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch site/koreader/app.js site/koreader/index.html site/koreader/style.css tests/test_koreader_backfill.py
git update-index --really-refresh
git add site/koreader/app.js site/koreader/index.html site/koreader/style.css tests/test_koreader_backfill.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: KOReader 落地页支持手动补记某一天"
```

---

# Track S7c · 最近 30 天历史

## Task 7: `scripts/history.py` —— 回填 + 维护 `data/history.json`

**Files:**
- Create: `scripts/history.py`
- Create: `data/history.json`
- Create: `tests/test_history.py`

**Interfaces:**
- Consumes: `scripts.weread_client.read_times("monthly")`（桶 = 本月每一天的秒数）；
  `data/dex.json` 的 `ledger`
- Produces:
  - `history_day(ts: int) -> str` —— 桶时间戳 → `YYYY-MM-DD`（北京时间）
  - `merge_history(old: dict, days: dict) -> dict` —— 同源同日取 max
  - `prune_history(history: dict, keep_days: int, today: str) -> dict`
  - `build_history(dex: dict, buckets: dict, today: str, keep_days: int = 30) -> dict`
  - `main(...) -> dict`

**★ 铁律**：`scripts/history.py` **只写 `data/history.json`**，
**永远不碰 `data/dex.json`** ⇒ 回填历史**不会**触发补发。

- [ ] **Step 1: 写失败的测试**

`tests/test_history.py`：

```python
"""历史数据（`scripts/history.py`）的测试。

★ 最重要的那条：**往 history 里回填历史量，不能影响发奖判定**。
账本（ledger）是结算用的 —— 往里塞历史会让 `pending_days()` 以为那些天「该发奖」，
一口气补发十几只。所以历史必须单独存（`data/history.json`）。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile
import unittest

from scripts import dex_core as dc
from scripts import history as hs


class BucketDayTest(unittest.TestCase):
    """桶时间戳 → 日期（北京时间）。"""

    def test_a_bucket_at_local_midnight_maps_to_that_day(self):
        # 1795276800 = 2026-11-22 00:00 +08:00（桶 key 就是北京时间的当天零点）
        self.assertEqual(hs.history_day(1795276800), "2026-11-22")
        self.assertEqual(hs.history_day(1795363200), "2026-11-23")


class MergeTest(unittest.TestCase):
    def test_same_source_same_day_takes_max(self):
        got = hs.merge_history({"2026-11-23": {"weread": 100}},
                                {"2026-11-23": {"weread": 300}})
        self.assertEqual(got["2026-11-23"]["weread"], 300)

    def test_a_new_source_is_added(self):
        got = hs.merge_history({"2026-11-23": {"weread": 100}},
                                {"2026-11-23": {"pom": 25}})
        self.assertEqual(got["2026-11-23"], {"weread": 100, "pom": 25})

    def test_negative_amounts_are_dropped(self):
        got = hs.merge_history({}, {"2026-11-23": {"weread": -5}})
        self.assertEqual(got, {})


class PruneTest(unittest.TestCase):
    def test_days_inside_the_window_are_kept(self):
        old = {"2026-11-01": {"weread": 100}, "2026-11-23": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertIn("2026-11-23", got)
        self.assertIn("2026-11-01", got)   # 22 天前，还在窗口里

    def test_days_beyond_the_window_are_dropped(self):
        old = {"2026-09-01": {"weread": 100}, "2026-11-23": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertNotIn("2026-09-01", got)

    def test_future_days_are_dropped(self):
        old = {"2026-12-01": {"weread": 100}}
        got = hs.prune_history(old, 30, "2026-11-23")
        self.assertNotIn("2026-12-01", got)


class BuildHistoryTest(unittest.TestCase):
    def test_buckets_become_days(self):
        dex = {"ledger": {"2026-11-22": {"weread": 1753, "pom": 25}}}
        buckets = {1795276800: 1800}   # = 2026-11-22
        got = hs.build_history(dex, buckets, "2026-11-23")
        self.assertEqual(got["days"]["2026-11-22"]["weread"], 1800)

    def test_ledger_sources_are_copied_in(self):
        dex = {"ledger": {"2026-11-22": {"weread": 1753, "pom": 25}}}
        got = hs.build_history(dex, {}, "2026-11-23")
        self.assertEqual(got["days"]["2026-11-22"]["pom"], 25)

    def test_building_history_does_not_change_the_award_decision(self):
        """★★ 守门测试：回填历史**绝不能**改变 `pending_days()` 的结果。"""
        cfg = {
            "perAward": 2,
            "maxBackfill": 3,
            "ledgerDays": 30,
            "sources": {
                "weread": {"per": 900, "enabled": True, "maxPerDay": 86400},
                "pom": {"per": 25, "enabled": True, "maxPerDay": 1440},
            },
        }
        ledger = {"2026-11-20": {"weread": 1800}}
        awarded = []
        before = dc.pending_days(cfg, ledger, awarded)
        # 回填 13 天历史进 history —— 不碰 ledger
        hs.build_history({"ledger": ledger}, {1795104000: 1800}, "2026-11-23")
        after = dc.pending_days(cfg, ledger, awarded)
        self.assertEqual(before, after)
        self.assertEqual(len(after), 1)   # 只有 11-20 那天待发奖


class HistoryFileTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history.json")
            hs.write_history({"version": 1, "days": {}}, p)
            got = hs.load_history(p)
            self.assertEqual(got["days"], {})

    def test_a_broken_file_gives_an_empty_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history.json")
            pathlib.Path(p).write_text("{oops", encoding="utf-8")
            got = hs.load_history(p)
            self.assertEqual(got["days"], {})

    def test_the_committed_history_file_is_valid(self):
        p = pathlib.Path(__file__).resolve().parent.parent / "data" / "history.json"
        self.assertTrue(p.exists(), "data/history.json 要入库（哪怕是空的）")
        got = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("days", got)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_history
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'scripts.history'`

- [ ] **Step 3: 写实现**

`scripts/history.py`：

```python
"""最近 30 天的历史（**只展示，不结算**）。

为什么要单独存一个文件：账本（`data/dex.json` 的 `ledger`）是**结算用**的 ——
`award.py` 的 `pending_days()` 会把「账本里有量、但还没发过奖」的日子当成**待补发日**。
所以往账本里回填历史 = 一次性补发十几只宝可梦。

⇒ 历史单独放 `data/history.json`，**永远不碰 `data/dex.json`**。
回填历史不影响任何发奖判定（有测试守着）。

微信读书的部分从**月桶**回填（`read_times("monthly")` 给的是本月每一天的秒数），
三个 push 源的部分从账本里抄。
"""

from __future__ import annotations

import datetime
import json
import os

TZ_SH = datetime.timezone(datetime.timedelta(hours=8))
HISTORY_PATH = "data/history.json"
DEFAULT_KEEP_DAYS = 30


def history_day(ts: int) -> str:
    """桶起始时间戳 → `YYYY-MM-DD`（北京时间）。

    桶 key 就是北京时间的当天零点（上游按天分桶，已用月桶/年桶反推验证过）。
    """
    return datetime.datetime.fromtimestamp(int(ts), tz=TZ_SH).strftime("%Y-%m-%d")


def merge_history(old: dict, days: dict) -> dict:
    """把新的一天并进历史。同源同一天取 max（跟账本一个规矩 ⇒ 幂等）。"""
    out = {day: dict(amounts) for day, amounts in (old or {}).items()}
    for day, amounts in (days or {}).items():
        row = out.setdefault(day, {})
        for source, amount in (amounts or {}).items():
            amount = int(amount)
            if amount < 0:
                continue
            row[source] = max(int(row.get(source, 0)), amount)
    return out


def prune_history(history: dict, keep_days: int, today: str) -> dict:
    """只保留最近 keep_days 天（含今天）；未来的日子丢掉。"""
    today_d = datetime.date.fromisoformat(today)
    keep = {(today_d - datetime.timedelta(days=i)).isoformat()
            for i in range(int(keep_days))}
    return {day: dict(amounts)
            for day, amounts in (history or {}).items() if day in keep}


def build_history(dex: dict, buckets: dict, today: str,
                  keep_days: int = DEFAULT_KEEP_DAYS) -> dict:
    """组装历史：账本里的 push 源 + 月桶里的微信读书。"""
    days = {day: dict(amounts)
            for day, amounts in ((dex.get("ledger") or {})).items()}
    for ts, seconds in (buckets or {}).items():
        day = history_day(ts)
        row = days.setdefault(day, {})
        row["weread"] = max(int(row.get("weread", 0)), int(seconds))
    days = prune_history(days, keep_days, today)
    return {"version": 1, "updatedAt": today, "days": days}


def load_history(path: str = HISTORY_PATH) -> dict:
    """读历史。文件不存在或坏了都当空的。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "updatedAt": "", "days": {}}
    if not isinstance(data, dict):
        return {"version": 1, "updatedAt": "", "days": {}}
    data.setdefault("days", {})
    return data


def write_history(history: dict, path: str = HISTORY_PATH) -> None:
    """写历史。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main(dex_path: str = "data/dex.json", history_path: str = HISTORY_PATH,
         buckets=None, today: str | None = None) -> dict:
    """跑一轮历史维护。**只写 history_path**，绝不碰 dex_path。"""
    if today is None:
        today = datetime.datetime.now(TZ_SH).strftime("%Y-%m-%d")
    try:
        with open(dex_path, encoding="utf-8") as f:
            dex = json.load(f)
    except (OSError, ValueError):
        dex = {}
    if buckets is None:
        try:
            from scripts import weread_client as wc
            buckets = wc.read_times("monthly")
        except Exception as exc:  # noqa: BLE001 —— 取不到就只留账本里那些
            print("[历史] 月桶取数失败: %s" % exc)
            buckets = {}
    old = load_history(history_path)
    merged = merge_history(old.get("days") or {},
                            build_history(dex, buckets, today)["days"])
    # ★ merge 之后再剪一次 —— 老文件里可能有早就超出窗口的天
    merged = prune_history(merged, DEFAULT_KEEP_DAYS, today)
    history = {"version": 1, "updatedAt": today, "days": merged}
    write_history(history, history_path)
    print("[历史] %d 天" % len(history["days"]))
    return history


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_history
```

Expected: PASS —— 11 tests OK

- [ ] **Step 5: 建一个空的 `data/history.json` 并跑一次真实回填**

```bash
cd /workspace/pokemon-reading-dex
printf '{\n  "version": 1,\n  "updatedAt": "",\n  "days": {}\n}\n' > data/history.json
python3 -B scripts/history.py
python3 -B -c "
import json; h=json.load(open('data/history.json'))
print('天数:', len(h['days']))
print('样例:', sorted(h['days'].items())[:3])"
```

Expected: 打印天数 ≥ 10（账本里已有的 10 天 + 月桶里本月的其它天）

- [ ] **Step 6: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch scripts/history.py tests/test_history.py data/history.json && git update-index --really-refresh
git add scripts/history.py tests/test_history.py data/history.json
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 历史数据单独存，回填历史不影响发奖判定"
```

---

## Task 8: 部署时同步 `site/history.json`

**Files:**
- Modify: `scripts/deploy.sh`
- Modify: `tests/test_history.py`（加一条断言）

**Interfaces:**
- Consumes: Task 7 的 `data/history.json`
- Produces: `site/history.json`（图鉴页拉的那份）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_history.py` 的 `HistoryFileTest` 里加：

```python
    def test_the_deploy_script_syncs_the_history_file(self):
        p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
        text = p.read_text(encoding="utf-8")
        self.assertIn("data/history.json", text)
        self.assertIn("site/history.json", text)
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_history
```

Expected: FAIL —— `AssertionError: 'data/history.json' not found in '...'`

- [ ] **Step 3: 改 `scripts/deploy.sh`**

在 `cp data/sources.json site/sources.json` 那一行后面加一行：

```bash
cp data/history.json site/history.json
```

（`data/history.json` 不存在时 `cp` 会报错 ⇒ 用 `cp ... || true` 兜底，
或者在 `[1/4] 同步数据到 site/` 那节开头加一句
`[ -f data/history.json ] && cp data/history.json site/history.json || true`。）

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_history && python3 -B run_tests.py
```

Expected: 全绿

- [ ] **Step 5: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch scripts/deploy.sh tests/test_history.py && git update-index --really-refresh
git add scripts/deploy.sh tests/test_history.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "chore: 部署时同步历史数据到站点"
```

---

## Task 9: 图鉴页的历史块（热力图 + 堆叠柱）

**Files:**
- Modify: `site/app.js`
- Modify: `site/index.html`
- Modify: `site/style.css`
- Modify: `tests/test_dex_page_s7.py`（Create）

**Interfaces:**
- Consumes: `site/history.json`（`{"version":1,"updatedAt":"…","days":{"YYYY-MM-DD":{"weread":秒,"pom":分钟,…}}}`）、
  `site/sources.json`（每源的 `per`，用来把原始量折成额度）
- Produces:
  - `window.DexPage.historyDays()` → `{ "YYYY-MM-DD": {源 id: 原始量} }`
  - `window.DexPage.creditForDay(dayMap)` → 当天总额度（`dayMap` = `{源 id: 原始量}`；
    **复用现有的 `creditForSource()`，不另写一份**）
  - `window.DexPage.renderHistory()` → 画热力图 + 堆叠柱

**★ 口径铁律**：堆叠柱的 Y 轴 = **额度**，不是原始单位。
每个源按自己的 `per` 折算后 floor，再相加 ⇒ 与顶部「今日额度」面板**同一个口径**
（`creditForSource` 已有，实现要跟它逐字一致）。

- [ ] **Step 1: 写失败的测试**

`tests/test_dex_page_s7.py`：

```python
"""图鉴页的历史块（S7c）。

两块：
- **热力图** —— 最近 30 天，每天一格，颜色 = 当天微信读书的秒数（分档按额度刻度）
- **堆叠柱** —— 每天一根，四个源堆叠，**Y 轴 = 额度**（跟今日额度面板同口径）

跑法：python3 -B run_tests.py
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = ROOT / "site" / "app.js"
INDEX_HTML = ROOT / "site" / "index.html"
STYLE_CSS = ROOT / "site" / "style.css"


class HistoryMarkupTest(unittest.TestCase):
    def test_the_page_has_a_history_section(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("historyCard", html)

    def test_the_page_has_placeholders_for_the_two_charts(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("heatMap", html)
        self.assertIn("creditBars", html)

    def test_the_script_fetches_the_history_file(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("history.json", js)

    def test_the_script_exposes_the_history_helpers(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("historyDays", js)
        self.assertIn("creditForDay", js)

    def test_the_credit_uses_the_registry_per(self):
        """Y 轴是额度 ⇒ 必须用注册表里的 per，不能写死。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("per", js)
        self.assertRegex(js, r"function\s+creditForDay")

    def test_the_heat_map_has_thirty_cells(self):
        """30 天 = 30 格。"""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertRegex(js, r"HISTORY_DAYS\s*=\s*30")


class HistoryStyleTest(unittest.TestCase):
    def test_the_style_has_heat_map_and_bar_rules(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        self.assertIn(".heat", css)
        self.assertIn(".cbar", css)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认它失败**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_dex_page_s7
```

Expected: FAIL —— `AssertionError: 'historyCard' not found in '...'`

- [ ] **Step 3: 写实现**

**`site/index.html`** —— 在「今日额度」卡片之后、「今日捕获」之前插入：

```html
  <!-- 最近 30 天 -->
  <section class="card" id="historyCard">
    <div class="cpTitle">最近 30 天</div>
    <div class="historyRow">
      <div class="heatWrap">
        <div class="heat" id="heatMap"></div>
        <div class="heatLegend"><span>少</span>
          <i class="h0"></i><i class="h1"></i><i class="h2"></i><i class="h3"></i><i class="h4"></i>
          <span>多</span></div>
      </div>
      <div class="barWrap">
        <div class="cbar" id="creditBars"></div>
        <div class="cbarLegend">
          <span><i style="background:#c8623a"></i>微信读书</span>
          <span><i style="background:#e2a06a"></i>番茄钟</span>
          <span><i style="background:#7f9a8e"></i>KOReader</span>
          <span><i style="background:#c9a227"></i>背单词</span>
        </div>
        <p class="dim" style="margin-top:8px">Y 轴是<b>额度</b> —— 跟上面的「今日额度」一个口径。</p>
      </div>
    </div>
    <p class="dim" id="historyNote" style="margin-top:10px"></p>
  </section>
```

**`site/app.js`** —— 在 `window.DexPage = {…}` 之前追加（并把新函数挂进 `window.DexPage`）：

```javascript
/* ---------- 最近 30 天历史 ---------- */
/** 窗口大小：与服务器 ledgerDays / history 的保留天数一致 */
const HISTORY_DAYS = 30;
/** 历史的原始量：{ 'YYYY-MM-DD': {源 id: 原始量} } */
let historyDays = {};

/** 写进历史原始量。 */
function setHistoryDays(days) { historyDays = days || {}; }

/**
 * 某天每个源折出来的额度（堆叠柱用）。
 * @param {Object<string, number>} dayMap {源 id: 原始量}
 * @returns {Object<string, number>} {源 id: 额度}
 */
function creditsBySource(dayMap) {
  const map = dayMap || {};
  const out = {};
  for (const s of registrySources()) {
    out[s.id] = creditForSource(map[s.id] || 0, s.cfg);
  }
  return out;
}

/**
 * 某天的总额度。
 * ★ **必须复用现有的 `creditForSource()`**（`site/app.js:39`）——
 *   它与服务端 `scripts/sources.py` 的 `credit_for_day` 逐字同构，
 *   评审用 3731 组跨语言对拍守着。**另写一份并行实现，那条对拍会立刻炸。**
 * @param {Object<string, number>} dayMap {源 id: 原始量}
 * @returns {number}
 */
function creditForDay(dayMap) {
  const map = dayMap || {};
  let total = 0;
  for (const s of registrySources()) {
    total += creditForSource(map[s.id] || 0, s.cfg);
  }
  return total;
}

/** 最近 N 天的日期串（今天在前）。 */
function lastDayKeys(n) {
  const out = [];
  const now = new Date();
  for (let i = 0; i < n; i++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    const p = (x) => (x < 10 ? '0' + x : '' + x);
    out.push(d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()));
  }
  return out;
}

/** 热力图那一格的颜色档（按额度刻度）。 */
function heatLevel(seconds) {
  const s = Number(seconds || 0);
  if (s <= 0) return 0;
  if (s < 900) return 1;
  if (s < 1800) return 2;
  if (s < 2700) return 3;
  return 4;
}

/** 画热力图 + 堆叠柱。 */
function renderHistory() {
  const heat = document.getElementById('heatMap');
  const bars = document.getElementById('creditBars');
  const note = document.getElementById('historyNote');
  if (!heat || !bars) return;
  const keys = lastDayKeys(HISTORY_DAYS);
  // —— 热力图：颜色 = 当天微信读书的秒数 ——
  heat.innerHTML = keys.map((k) => {
    const sec = (historyDays[k] || {}).weread || 0;
    const lv = heatLevel(sec);
    const label = k.slice(5);
    return '<div class="hcell h' + lv + '" title="' + k + ' · ' + sec + ' 秒">' + label + '</div>';
  }).join('');
  // —— 堆叠柱：Y 轴 = 额度（用 creditForSource 折，与面板同口径）——
  const colors = { weread: '#c8623a', pom: '#e2a06a', koreader: '#7f9a8e', words: '#c9a227' };
  const srcs = registrySources();
  const rows = keys.map((k) => {
    const per = creditsBySource(historyDays[k] || {});
    const parts = [];
    let total = 0;
    for (const s of srcs) {
      const c = per[s.id] || 0;
      if (c > 0) {
        parts.push('<i style="height:' + (c * 14) + 'px;background:' + (colors[s.id] || '#c8623a') + '"></i>');
      }
      total += c;
    }
    return { key: k, total: total, parts: parts.join('') };
  });
  const max = Math.max(1, ...rows.map((r) => r.total));
  bars.innerHTML = rows.map((r) =>
    '<div class="cbcol" title="' + r.key + ' · ' + r.total + ' 个额度"><span class="cbs" style="height:' +
    Math.round((r.total / max) * 130) + 'px">' + r.parts + '</span></div>').join('');
  if (note) {
    const days = Object.keys(historyDays).length;
    note.textContent = days ? ('共 ' + days + ' 天的记录。空白格 = 那天没读。')
                            : '还没有历史数据。';
  }
}
```

**接线**：改 `site/app.js` 的 `load()`（`site/app.js:156`）—— 把历史一起拉进来：

```javascript
  const [pd, dx, reg, hist] = await Promise.all([
    fetchJSON("pokedex.json").catch(() => null),
    fetchJSON("dex.json").catch(() => null),
    fetchJSON("sources.json").catch(() => null),
    fetchJSON("history.json").catch(() => null),   // ★ 新增
  ]);
  ...
  SOURCES = reg;
  setHistoryDays((hist && hist.days) || {});       // ★ 新增
```

`render()` 里调 `renderHistory()`（放在 `renderTodayPanel()` 之后）。

> ⚠️ **复用现有的 `fetchJSON(name, timeout = 30000, retries = 2)`**
> （`site/app.js:137`，自带超时 + 重试 + `cache: "no-cache"`）——
> 别自己再写一个裸 `fetch`。

并在 `window.DexPage = {…}` 里加上：

```javascript
  historyDays: () => historyDays,
  creditForDay: creditForDay,
  renderHistory: renderHistory,
```

**`site/style.css`** —— 追加：

```css
/* ---------- 最近 30 天 ---------- */
.historyRow{display:flex;gap:16px;align-items:flex-start;margin-top:12px;flex-wrap:wrap}
.heatWrap{flex:1 1 260px;min-width:0}
.heat{display:grid;grid-template-columns:repeat(6,1fr);gap:4px}
.hcell{aspect-ratio:1;border-radius:7px;display:flex;align-items:center;justify-content:center;
       font-size:10px;font-variant-numeric:tabular-nums;background:var(--line);color:var(--dim)}
.h0{background:var(--line);color:var(--dim)}
.h1{background:rgba(200,98,58,.22);color:var(--ink)}
.h2{background:rgba(200,98,58,.45)}
.h3{background:rgba(200,98,58,.68)}
.h4{background:var(--accent);color:#fffdf8}
.heatLegend{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim);margin-top:8px}
.heatLegend i{width:13px;height:13px;border-radius:4px;display:inline-block}
.heatLegend .h0{background:var(--line)}.heatLegend .h1{background:rgba(200,98,58,.22)}
.heatLegend .h2{background:rgba(200,98,58,.45)}.heatLegend .h3{background:rgba(200,98,58,.68)}
.heatLegend .h4{background:var(--accent)}
.barWrap{flex:1 1 260px;min-width:0}
.cbar{display:flex;align-items:flex-end;gap:4px;height:140px}
.cbcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0}
.cbs{display:flex;flex-direction:column;justify-content:flex-end;width:100%;border-radius:3px 3px 0 0;
     overflow:hidden;background:transparent}
.cbs i{display:block;width:100%}
.cbarLegend{display:flex;gap:10px;font-size:11px;color:var(--dim);margin-top:8px;flex-wrap:wrap}
.cbarLegend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
```

- [ ] **Step 4: 跑测试，确认它过了**

```bash
cd /workspace/pokemon-reading-dex && python3 -B -m tests.test_dex_page_s7
```

Expected: PASS —— 7 tests OK

- [ ] **Step 5: 跑全套测试**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: 全绿

- [ ] **Step 6: 真浏览器验一遍**

用 Playwright 打开本地站点（`cd site && python3 -m http.server 8899`），
确认：热力图 30 格、颜色分档对、堆叠柱 30 根且高度随额度变、
`history.json` 拉不到时不白屏、**窄屏（390px）不横向溢出**。

- [ ] **Step 7: 提交**

```bash
cd /workspace/pokemon-reading-dex
touch site/app.js site/index.html site/style.css tests/test_dex_page_s7.py && git update-index --really-refresh
git add site/app.js site/index.html site/style.css tests/test_dex_page_s7.py
git -c user.name="Mavis" -c user.email="mavis@local" commit -m "feat: 图鉴页加最近 30 天历史（热力图 + 额度堆叠柱）"
```

---

# Track S7d · 收尾（部署 + 文档）

## Task 10: 部署 + README + cron 提示词同步

**Files:**
- Modify: `README.md`
- Modify: Mavis cron `463191742394721` 的提示词

**Interfaces:**
- Consumes: 全部三条 track 的产物
- Produces: 线上可用的三个功能

- [ ] **Step 1: README 补三节**

在 README 里加：

```markdown
### 补录（漏记的那天）

番茄钟页和 KOReader 落地页现在都能**补记过去某一天**：

- 番茄钟页：「补记某一天」→ 点日期格子 → 填那天的专注分钟数
- KOReader 落地页：「漏了哪一天？手动补上」→ 选日期 + 填分钟

额度**按天**算、零头不跨天凑 ⇒ 漏记一天，那天就永久归零 ⇒ 这两个入口就是为这个准备的。
（背单词页本来就有这套。）

### 最近 30 天

图鉴页顶部「今日额度」下面多了一块「最近 30 天」：

- **左边日历热力图** —— 每格一天，颜色 = 当天微信读书的秒数（分档按额度刻度），空白 = 那天没读
- **右边堆叠柱** —— 每天一根，四个源堆叠，**Y 轴是额度**（跟「今日额度」一个口径）

历史数据单独存在 `data/history.json` —— **只展示，不参与结算**。
所以回填历史**不会**触发补发（账本才是结算用的，往里塞历史会一口气发十几只）。

### 邮件通知

见上面的「邮件通知」一节。
```

- [ ] **Step 2: 同步 cron 提示词**

在 Task 4 加的那一步后面，再加一句历史回填：

```
   3.6 `cd /workspace/pokemon-reading-dex && python3 scripts/history.py` —— 回填最近 30 天的历史
      - 输出含「[历史] N 天」= 正常
      - 输出含「[历史] 月桶取数失败」= 微信读书那段没取到（不影响发奖，如实说一句即可）
      - **这个脚本只写 data/history.json，不碰 data/dex.json** —— 跑它不会影响发奖
```

⚠️ 改 cron 提示词必须**传完整提示词**（覆盖式，不是追加）。

- [ ] **Step 3: 跑全套测试 + 部署**

```bash
cd /workspace/pokemon-reading-dex && python3 -B run_tests.py
```

Expected: 全绿

```bash
cd /workspace/pokemon-reading-dex
export GIT_SSL_NO_VERIFY=1
git fetch origin main && git rebase origin/main
bash scripts/deploy.sh
```

- [ ] **Step 4: 线上验证**

- `https://lilia-shound.github.io/pokemon-dex/` —— 热力图 + 堆叠柱出来了，390px 不横向溢出
- `https://lilia-shound.github.io/pokemon-dex/pomodoro/` —— 「补记某一天」在
- `https://lilia-shound.github.io/pokemon-dex/koreader/` —— 「漏了哪一天」在
- `https://lilia-shound.github.io/pokemon-dex/history.json` —— 能拉到

- [ ] **Step 5: 告诉姜来结果**

- 一句话总结三个功能
- **提醒她给 Resend 的 key 和邮箱**（否则邮件那半边是关着的）
- 背单词口径 / 百词斩截图 / KOReader 真机 —— 那三件仍然挂着，但**不阻塞 S7**

---

## 附：跟这条计划无关、但欠着的事（等她拍板）

| 事 | 状态 |
|---|---|
| 背单词口径：新词还是新词+复习 → `per` 取 10 还是 100 | ⏳ 等她拍板（**S7 不依赖它**） |
| 百词斩脚本的关键词数组 | ⏳ 等她截图（**S7 不依赖它**） |
| KOReader 插件真机验证 | ⏳ 等她试（**S7 不依赖它**） |
| `site/app.js` 已经 800+ 行 | ⚠️ 本轮**不拆**（动了风险大），但记着 |
| `weread-pokemon-dex` 记忆 topic 超限（30994/30720） | ⚠️ 需要拆成两个 topic |
| 我本地落后远端 4 个 commit | 推送前 rebase |
