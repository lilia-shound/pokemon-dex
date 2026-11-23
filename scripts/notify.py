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
import urllib.request

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
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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


# ---------- 邮件正文 ----------

SITE_URL = "https://lilia-shound.github.io/pokemon-dex/"
IMG_BASE = SITE_URL + "img/"
FROM_ADDRESS = "训练家图鉴 <onboarding@resend.dev>"
RESEND_URL = "https://api.resend.com/emails"
# Resend 前面有 Cloudflare：urllib 默认 UA（`Python-urllib/x.y`）会被 403
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


def _pokemon_card(entry: dict, pokedex: dict, today: str = "") -> str:
    """一只宝可梦的邮件卡片。

    ★ **当天收服的不显示日期** —— QQ 邮箱会把「2026-11-23」这种格式**自动变成
      蓝色下划线链接**（客户端自己的功能，HTML 拦不住）⇒ 看着像排版坏了。
      日期只有「补发」那种情况才需要标 ⇒ 那时写成「补发 · 11 月 21 日」
      （中文格式 QQ 一般不会自动加链）。
    """
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
        badge = ('<span style="%sbackground:#c9a227;color:#fffdf8;">\u2728 \u95ea\u5149</span>'
                 % _BADGE)
    # 日期行：只有「不是今天收的」才需要标出来
    day_line = ""
    if day and day != today:
        try:
            y, m, d = day.split("-")
            day_line = ('<div style="font-size:12px;color:#9a8d80;margin-top:8px;">'
                        '补发 · %s 月 %s 日</div>' % (int(m), int(d)))
        except ValueError:
            day_line = ('<div style="font-size:12px;color:#9a8d80;margin-top:8px;">'
                        '补发 · %s</div>' % day)
    return (
        '<div style="background:#fffdf8;border:1px solid #e9e0d0;border-radius:16px;'
        'padding:16px;margin:12px 0;text-align:center;">'
        '<img src="%s%s" alt="%s" width="180" style="display:block;margin:0 auto 8px;"/>'
        '<div style="font-size:20px;font-weight:700;">%s</div>'
        '<div style="font-size:13px;color:#9a8d80;margin-top:4px;">No.%s%s</div>'
        '<div style="font-size:13px;color:#9a8d80;margin-top:2px;">%s</div>'
        '<div style="margin-top:8px;">%s</div>'
        '%s'
        '</div>'
        % (IMG_BASE, art, name, name, pid, (" · " + types) if types else "",
           info.get("category", ""), badge, day_line)
    )


def build_award_email(entries: list, pokedex: dict, streak: int, total: int,
                       source_text: str = "", today: str = "") -> tuple[str, str]:
    """收服邮件：主题 + HTML。

    today 传今天（YYYY-MM-DD）⇒ 当天收服的不显示日期行（QQ 会把日期自动加链）。
    """
    entries = list(entries or [])
    if not entries:
        return ("", "")
    names = [e.get("nameZh") or e.get("id") for e in entries]
    if len(entries) == 1:
        subject = "训练家图鉴 · 收服了 %s No.%s" % (names[0], entries[0].get("id", ""))
    else:
        subject = "训练家图鉴 · 收服了 %d 只宝可梦" % len(entries)
    cards = "".join(_pokemon_card(e, pokedex or {}, today) for e in entries)
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
                 # ★ 必须带浏览器样的 UA —— Resend 前面挂着 Cloudflare，
                 #   urllib 默认 UA 是 `Python-urllib/3.11` ⇒ 直接 403
                 #   （`error code: 1010` = 按浏览器签名封的）。2026-11-23 实测：
                 #   不带 UA → 403；带上 → 200。
                 "User-Agent": BROWSER_UA},
        method="POST",
    )
    try:
        op = opener or urllib.request.urlopen
        with op(req, timeout=20) as resp:
            resp.read()
        print("[通知] 已发送: " + subject)
        return True
    except Exception as exc:  # noqa: BLE001 —— 旁路不许把整个流程带崩
        print("[通知] 失败: %s" % exc)
        return False


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
    # 今天是哪天（邮件里判断「当天收服」用）
    today = (dex.get("today") or {}).get("date") or now.strftime("%Y-%m-%d")

    # ---- 收服了新宝可梦 ----
    pending = pending_awards(dex.get("collected") or [], state.get("awarded") or [])
    if pending:
        pokedex = load_pokedex()
        subject, html = build_award_email(
            pending, pokedex,
            streak=int(dex.get("streak") or 0),
            total=len(dex.get("collected") or []),
            source_text=_source_text(dex),
            today=today,
        )
        if sender(subject, html):
            # 先发后记 —— 发失败就不记，下轮还会重发
            state.setdefault("awarded", []).extend(award_key(e) for e in pending)
            sent.append(subject)
        else:
            skipped.append(subject)

    # ---- 连读快断了 ----
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
