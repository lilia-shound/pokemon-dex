"""宝可梦番茄钟（S2）的观感断言：真实浏览器 + Playwright。

跑法：先在 site/ 起一个静态服务器（8899 端口），然后
    python3 -B tests/pomodoro_browser_test.py
"""
import json
import pathlib
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8899/pomodoro/index.html"
CHROME = "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome"
ROOT = pathlib.Path(__file__).resolve().parent.parent

FAILS = []
PASSES = []


def check(name, cond, detail=""):
    (PASSES if cond else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(("  ✓ " if cond else "  ✗ ") + name + (f" — {detail}" if detail else ""))


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 430, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append("pageerror:" + str(e)))
        pg.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)

        # ── A 页面加载 ───────────────────────────────
        pg.goto(BASE, wait_until="commit")
        pg.wait_for_selector(".ring", timeout=30000)
        pg.wait_for_function("() => document.querySelector('#netStat') && "
                             "document.querySelector('#netStat').innerText.includes('1028')",
                             timeout=30000)
        print("A 页面加载")
        check("无控制台错误", not errs, str(errs[:2]))
        check("清单加载 1028 只",
              pg.evaluate("() => POKEMON.length") == 1028)
        check("额度口径取到 25 分钟",
              pg.evaluate("() => UNIT_PER_CREDIT") == 25)
        check("计时环在", pg.locator(".ring").count() == 1)

        # ── B 探头计划公式 ───────────────────────────
        print("B 探头计划公式")
        plans = pg.evaluate("""() => {
          const out = [];
          for (const min of [10, 25, 40, 60]) {
            const dur = min * 60000;
            const plan = planPeeks(dur);
            const n = plan.length;
            const gap = dur / (n + 2);
            out.push({
              min, n,
              first: plan[0].at / dur, last: plan[n-1].at / dur,
              timesOk: plan.every(p => p.at >= dur*0.07 - 1e-6 && p.at <= dur*0.93 + 1e-6),
              gapsOk: plan.every((p, i) => i === 0 || p.at - plan[i-1].at >= gap - 1e-6),
              distinct: new Set(plan.map(p => p.pkm.id)).size === n,
            });
          }
          return out;
        }""")
        expect = {10: (1, 2), 25: (2, 3), 40: (3, 4), 60: (3, 6)}
        for row in plans:
            lo, hi = expect[row["min"]]
            check(f'{row["min"]} 分钟的探头数在 {lo}~{hi}',
                  lo <= row["n"] <= hi, f'实际 {row["n"]}')
            check(f'{row["min"]} 分钟的探头时刻在 7%~93%',
                  row["timesOk"] and 0.07 - 1e-6 <= row["first"] and row["last"] <= 0.93 + 1e-6,
                  f'首 {row["first"]:.3f} 末 {row["last"]:.3f}')
            check(f'{row["min"]} 分钟的探头间隔 ≥ 时长/(n+2)', row["gapsOk"])
            check(f'{row["min"]} 分钟的探头不重复（优先派没收集过的）', row["distinct"])

        # ── C 优先派没收集过的 ───────────────────────
        print("C 优先派没收集过的")
        fresh = pg.evaluate("""() => {
          SERVER_OWNED = new Set(POKEMON.slice(0, 50).map(p => p.id));
          const plan = planPeeks(25*60000);
          const owned = new Set([...Object.keys(S().dex), ...SERVER_OWNED]);
          return {picked: plan.map(p => p.pkm.id), hits: plan.filter(p => owned.has(p.pkm.id)).length};
        }""")
        check("派出的都不在「已收服 50 只」里", fresh["hits"] == 0, str(fresh["picked"]))

        # ── D 图片拉不到退回剪影 ─────────────────────
        print("D 图片拉不到退回剪影")
        pg.route("**/pokemon-dex/img/**", lambda route: route.abort())
        pg.evaluate("() => { spawnPeek(POKEMON[0]); }")
        pg.wait_for_timeout(2500)
        src = pg.evaluate("() => { const i = document.querySelector('.peek img'); return i ? i.getAttribute('src') : ''; }")
        check("探头图换成剪影 data URI", src.startswith("data:image/svg"), src[:40])
        check("换剪影后计时没被卡住", pg.evaluate("() => typeof T.phase === 'string'"))
        pg.unroute("**/pokemon-dex/img/**")

        # ── E 正常完成一轮 → 分钟数 +1 ───────────────
        print("E 完成一轮累计分钟")
        before = pg.evaluate("() => (S().days[todayStr()] || {minutes:0}).minutes")
        pg.evaluate("""() => {
          S().settings.focusMin = 1;
          startFocus();
          T.endAt = Date.now() + 60;
        }""")
        pg.wait_for_timeout(2000)
        after = pg.evaluate("() => (S().days[todayStr()] || {minutes:0}).minutes")
        check("完成一轮后当天分钟 +1", after == before + 1, f"{before} → {after}")
        check("番茄数也 +1", pg.evaluate("() => (S().days[todayStr()]||{poms:0}).poms") >= 1)

        # ── F 摸鱼检测：切走 >3 秒记一次 ─────────────
        print("F 摸鱼检测")
        pg.evaluate("""() => {
          T.phase = 'focus'; T.total = 60000; T.elapsed = 0; T.endAt = Date.now() + 60000;
          T.superOn = true;
        }""")
        slacks_before = pg.evaluate("() => S().slacks || 0")
        pg.evaluate("""() => {
          Object.defineProperty(document, 'hidden', {value: true, configurable: true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        pg.wait_for_timeout(3600)
        pg.evaluate("""() => {
          Object.defineProperty(document, 'hidden', {value: false, configurable: true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        pg.wait_for_timeout(500)
        slacks_after = pg.evaluate("() => S().slacks || 0")
        check("切走 3.6 秒记一次摸鱼", slacks_after == slacks_before + 1,
              f"{slacks_before} → {slacks_after}")

        # ── G 三次摸鱼 → 整轮作废（分钟数不加） ───────
        print("G 三次摸鱼整轮作废")
        mins_before = pg.evaluate("() => (S().days[todayStr()] || {minutes:0}).minutes")
        for _ in range(2):
            pg.evaluate("""() => {
              T.phase = 'focus'; T.total = 60000; T.elapsed = 0; T.endAt = Date.now() + 60000;
              T.superOn = true;
              Object.defineProperty(document, 'hidden', {value: true, configurable: true});
              document.dispatchEvent(new Event('visibilitychange'));
            }""")
            pg.wait_for_timeout(3600)
            pg.evaluate("""() => {
              Object.defineProperty(document, 'hidden', {value: false, configurable: true});
              document.dispatchEvent(new Event('visibilitychange'));
            }""")
            pg.wait_for_timeout(600)
        phase = pg.evaluate("() => T.phase")
        mins_after = pg.evaluate("() => (S().days[todayStr()] || {minutes:0}).minutes")
        check("第三次摸鱼后整轮作废（回到 idle）", phase == "idle", f"phase={phase}")
        check("作废的那轮不计入分钟数", mins_after == mins_before,
              f"{mins_before} → {mins_after}")

        # ── H 同步链接 ───────────────────────────────
        print("H 同步链接")
        url = pg.evaluate("""() => {
          S().days['2026-11-19'] = {minutes: 50, poms: 2};
          return buildIssueUrl();
        }""")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        body = json.loads(q["body"][0])
        check("链接指向本仓库的 issues/new",
              url.startswith("https://github.com/lilia-shound/pokemon-dex/issues/new?"), url[:60])
        check("标题是 [report] pom 日期", q["title"][0].startswith("[report] pom "), q["title"][0])
        check("body 是合法 JSON", isinstance(body, dict))
        check("body v=1", body.get("v") == 1)
        check("body source=pom", body.get("source") == "pom")
        check("body 带的是当天累计总量", body.get("days", {}).get("2026-11-19") == 50,
              str(body.get("days")))
        check("body 不带没分钟的日期",
              all(v > 0 for v in body.get("days", {}).values()))

        # ── I Service Worker 不缓存图片 ──────────────
        print("I Service Worker")
        sw_src = (ROOT / "site" / "pomodoro" / "sw.js").read_text(encoding="utf-8")
        cache_block = sw_src.split("const SHELL = [")[1].split("];")[0]
        check("SHELL 名单里没有 .webp", ".webp" not in cache_block)
        check("SHELL 名单里没有 img/", "/img/" not in cache_block)
        pg.wait_for_timeout(1500)
        keys = pg.evaluate("""async () => {
          if (!('caches' in window)) return null;
          const names = await caches.keys();
          const out = [];
          for (const n of names) {
            const c = await caches.open(n);
            for (const k of await c.keys()) out.push(k.url);
          }
          return out;
        }""")
        if keys is None:
            check("（浏览器无 CacheStorage，跳过运行时检查）", True)
        else:
            check("运行时缓存里没有图片", not any(".webp" in k for k in keys),
                  str([k for k in keys if ".webp" in k][:2]))

        # ── 截图 ─────────────────────────────────────
        pg.goto(BASE, wait_until="commit")
        pg.wait_for_selector(".ring", timeout=30000)
        pg.wait_for_timeout(3000)
        pg.screenshot(path="/workspace/site-check/pomodoro-timer.png")
        pg.click('#tabs button[data-tab="dex"]')
        pg.wait_for_timeout(1200)
        pg.screenshot(path="/workspace/site-check/pomodoro-dex.png")

        b.close()

    print()
    print(f"通过 {len(PASSES)} / 失败 {len(FAILS)}")
    for f in FAILS:
        print("  ✗ " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
