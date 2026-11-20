// ==UserScript==
// @name         微信读书 · 训练家图鉴
// @namespace    https://lilia-shound.github.io/pokemon-dex/
// @version      1.0.0
// @description  在微信读书网页版显示今日阅读进度与宝可梦图鉴墙
// @author       Mavis
// @match        https://weread.qq.com/web/reader*
// @match        https://weread.qq.com/web/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(() => {
  "use strict";

  const BASE = "https://lilia-shound.github.io/pokemon-dex";
  const REFRESH_MS = 10 * 60 * 1000;

  let DEX = { collected: [], streak: 0, today: {} };
  let POKEDEX = { pokemon: [] };
  let SOURCES = null;   // 源注册表（sources.json）—— 阈值的权威来源
  let MODAL = null;
  let lastError = "";
  let OWNED_ONLY = false;   // 图鉴墙只看「已获得」—— 收服的数量少，翻全图鉴很松散

  const CSS = `
  .tx-caps{
    position:fixed;right:20px;top:18px;z-index:2147483000;
    display:flex;gap:9px;align-items:center;padding:8px 14px;border-radius:999px;
    background:rgba(255,253,248,.94);border:1px solid #e9e0d0;
    box-shadow:0 8px 24px rgba(90,64,40,.14);
    font:13px/1 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    color:#2f2823;cursor:pointer;user-select:none;transition:transform .18s ease;
  }
  .tx-caps:hover{transform:translateY(-2px)}
  .tx-caps .lbl{white-space:nowrap}
  .tx-caps .bar{width:70px;height:6px;border-radius:3px;background:#e9e0d0;overflow:hidden;flex:0 0 auto}
  .tx-caps .bar i{display:block;height:100%;width:0;border-radius:3px;
    background:linear-gradient(90deg,#e8825a,#c8623a);transition:width .5s ease}
  .tx-caps .num{font-weight:600;font-variant-numeric:tabular-nums}

  .tx-modal{position:fixed;inset:0;z-index:2147483001;display:none;align-items:center;justify-content:center;
    background:rgba(28,20,14,.5);backdrop-filter:blur(4px);font-family:inherit}
  .tx-modal.on{display:flex}
  .tx-panel{background:#f6f1e7;color:#2f2823;width:min(940px,94vw);height:min(80vh,780px);
    border-radius:22px;display:flex;flex-direction:column;overflow:hidden;
    box-shadow:0 24px 70px rgba(0,0,0,.4);animation:txpop .26s cubic-bezier(.34,1.56,.64,1)}
  @keyframes txpop{from{transform:scale(.95);opacity:0}to{transform:scale(1);opacity:1}}
  .tx-head{display:flex;justify-content:space-between;align-items:center;gap:12px;
    padding:14px 20px;border-bottom:1px solid #e9e0d0;flex:0 0 auto}
  .tx-head b{font-size:16px}
  .tx-head .dim{color:#9a8d80;font-size:12px}

  .tx-open{margin:14px 18px 0;background:#fffdf8;border:1px solid #e9e0d0;border-radius:18px;
    padding:14px 16px;display:flex;gap:16px;align-items:center;flex:0 0 auto;
    position:relative;overflow:hidden}
  .tx-open::before{content:"";position:absolute;inset:0;pointer-events:none;
    background:radial-gradient(120% 80% at 8% 0%,#f3d9cc,transparent 62%);opacity:.55}
  .tx-open img{width:96px;height:96px;object-fit:contain;position:relative;flex:0 0 auto}
  .tx-open .nm{font-size:19px;font-weight:600;position:relative}
  .tx-open .sub{color:#9a8d80;font-size:12px;margin-top:2px;position:relative}
  .tx-open .fl{color:#2f2823;font-size:13px;margin-top:6px;opacity:.88;position:relative}
  .tx-shiny{display:inline-block;margin-left:6px;padding:2px 8px;border-radius:999px;font-size:10px;
    font-weight:600;color:#fff;background:linear-gradient(120deg,#e3c05a,#c9a227);vertical-align:middle}

  .tx-grid{overflow:auto;padding:14px 18px 20px;display:grid;
    grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:10px;align-content:start}
  .tx-tabs{display:flex;gap:7px;padding:12px 18px 0;flex-wrap:wrap}
  .tx-tabs button{border:1px solid #e9e0d0;background:#fffdf8;color:#9a8d80;border-radius:999px;
    padding:4px 13px;font-size:12px;cursor:pointer;font-family:inherit;transition:all .18s ease}
  .tx-tabs button:hover{color:#2f2823}
  .tx-tabs button.on{background:#2f2823;color:#f6f1e7;border-color:#2f2823}
  .tx-tabs button.owned{color:#c8623a;border-color:#c8623a;font-weight:600}
  .tx-tabs button.owned.on{background:#c8623a;color:#fffdf8;border-color:#c8623a}
  .tx-cell{background:#fffdf8;border:1px solid #e9e0d0;border-radius:14px;padding:8px 6px 10px;
    text-align:center;cursor:pointer;transition:transform .18s cubic-bezier(.34,1.56,.64,1)}
  .tx-cell:hover{transform:translateY(-3px)}
  .tx-cell img{width:70px;height:70px;object-fit:contain;display:block;margin:2px auto 0}
  .tx-cell.locked img{filter:grayscale(1) opacity(.18)}
  .tx-cell .no{font-size:10px;color:#9a8d80}
  .tx-cell .nm{font-size:12px;margin-top:2px;min-height:1.4em}
  .tx-cell .dt{font-size:10px;color:#c8623a;margin-top:2px}
  .tx-cell.shiny{box-shadow:0 0 0 2px #c9a227 inset}
  img.broken{opacity:.1}
  .tx-empty{padding:30px;text-align:center;color:#9a8d80;font-size:13px}
  `;

  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function artUrl(fname) {
    return fname ? `${BASE}/img/${fname}` : "";
  }

  // 「掉一只宝可梦」要读多久（秒）。
  // 权威口径在注册表：perAward × sources.weread.per；dex.json 的 today.threshold
  // 是发放器按同一口径写进状态文件的快照。两者都读不到就不显示进度 ——
  // 不再硬编码一个猜的数（那个数会是第三个真相源：perAward 改了它也不跟着变）。
  function registryThreshold() {
    const reg = SOURCES && SOURCES.sources && SOURCES.sources.weread;
    if (!reg) return 0;
    const perAward = Number(SOURCES.perAward);
    const per = Number(reg.per);
    if (!perAward || !per) return 0;
    return perAward * per;
  }

  function thresholdSeconds() {
    const t = DEX.today || {};
    const fromState = Number(t.threshold);
    if (fromState > 0) return fromState;
    return registryThreshold();
  }

  function todayInfo() {
    const t = DEX.today || {};
    const thr = thresholdSeconds();
    const sec = t.seconds || 0;
    return {
      sec, thr,
      pct: thr > 0 ? Math.min(100, Math.round((sec / thr) * 100)) : 0,
      date: t.date,
    };
  }

  // 今日读数文案 —— 阈值拿不到时只显示已读分钟数，不显示猜的进度
  // 分子夹到阈值：perAward=1 时阈值是 900 秒（15 分），而她今天已读 1776 秒（29 分）
  // ⇒ 不夹会显示成「29/15 分」（超过 100%）。满额时显示「15/15 分」。
  function todayText(t) {
    const read = Math.floor(t.sec / 60);
    if (t.thr <= 0) return `${read} 分`;
    const thr = Math.floor(t.thr / 60);
    return `${Math.min(read, thr)}/${thr} 分`;
  }

  function ownedMap() {
    const m = new Map();
    for (const c of DEX.collected || []) {
      const prev = m.get(c.id);
      if (!prev) { m.set(c.id, c); continue; }
      if (c.shiny && !prev.shiny) m.set(c.id, c);
    }
    return m;
  }

  async function load() {
    try {
      const [d, p] = await Promise.all([
        fetch(`${BASE}/dex.json`, { cache: "no-store" }).then((r) => {
          if (!r.ok) throw new Error("dex.json " + r.status);
          return r.json();
        }),
        fetch(`${BASE}/pokedex.json`, { cache: "no-store" }).then((r) => {
          if (!r.ok) throw new Error("pokedex.json " + r.status);
          return r.json();
        }),
      ]);
      DEX = d;
      POKEDEX = p;
      lastError = "";
    } catch (e) {
      lastError = e.message || String(e);
      console.warn("[训练家图鉴] 数据拉取失败", e);
    }
    // 源注册表单独拉：只用来兜底阈值，拉不到只影响进度显示，
    // 不该让整个插件显示「连不上，稍后重试」
    SOURCES = await loadSources();
    paintCaps();
    if (MODAL) paintModal();
  }

  async function loadSources() {
    try {
      const r = await fetch(`${BASE}/sources.json`, { cache: "no-store" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  function paintCaps() {
    let el = $("tx-caps");
    if (!el) {
      el = document.createElement("div");
      el.id = "tx-caps";
      el.title = "训练家图鉴";
      el.onclick = openModal;
      document.body.appendChild(el);
    }
    const i = todayInfo();
    if (lastError) {
      el.innerHTML = `<span class="num">图鉴</span><span class="lbl">连不上，稍后重试</span>`;
      return;
    }
    const n = ownedMap().size;
    // 阈值拿不到就不显示进度条与数字 —— 不显示猜的数
    const bar = i.thr > 0 ? `<span class="bar"><i style="width:${i.pct}%"></i></span>` : "";
    // 向下取整：不虚报进度（1776 秒四舍五入会显示成「30/30」，看着像已达标）
    // 分子还要夹到阈值：阈值 15 分而今天读了 29 分 ⇒ 不夹会显示成「29/15」
    const thrMin = i.thr > 0 ? Math.floor(i.thr / 60) : 0;
    const num = i.thr > 0
      ? `<span class="num">${Math.min(Math.floor(i.sec / 60), thrMin)}/${thrMin}</span>`
      : "";
    el.innerHTML = `<span class="num">${n}</span><span class="lbl">图鉴</span>` + bar + num;
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
    if (!MODAL) return;
    const owned = ownedMap();
    const total = POKEDEX.pokemon.length;
    const n = owned.size;
    const t = todayInfo();
    const collected = DEX.collected || [];
    // 按日期找今天那一只，而不是假设它在数组最后
    const todays = collected.filter((c) => c.date === t.date);
    const last = todays.length ? todays[todays.length - 1]
      : (collected.length ? collected[collected.length - 1] : null);
    const lp = last ? POKEDEX.pokemon.find((x) => x.id === last.id) : null;

    const head = `<div class="tx-head">
      <b>训练家图鉴 ${n}/${total}</b>
      <span class="dim">连读 ${DEX.streak || 0} 天 · 今日 ${todayText(t)} · 点空白处关闭</span>
    </div>`;

    const open = last && lp && DEX.lastAwardDate === t.date ? `
      <div class="tx-open">
        <img src="${artUrl(last.shiny && lp.artShiny ? lp.artShiny : lp.art)}" onerror="this.classList.add('broken')">
        <div>
          <div class="nm">${esc(lp.nameZh)}${last.shiny ? '<span class="tx-shiny">✦ 闪光</span>' : ""}</div>
          <div class="sub">No.${esc(lp.id)} · ${esc((lp.types || []).join(" / "))} · ${esc(lp.category)}
            · 特性 ${esc((lp.abilities || []).join("、"))}</div>
          <div class="fl">${esc(lp.flavor)}</div>
        </div>
      </div>` : "";

    const tabs = `<div class="tx-tabs">
      <button data-tab="all" class="${OWNED_ONLY ? "" : "on"}">全部</button>
      <button data-tab="owned" class="owned ${OWNED_ONLY ? "on" : ""}">已获得 ${n}</button>
    </div>`;

    // 「已获得」视图：只列收服过的那几只，按获得日期倒序（最新收服的排最前）
    const rows = OWNED_ONLY
      ? POKEDEX.pokemon.filter((p) => owned.has(p.id)).sort((a, b) =>
          String(owned.get(b.id).date || "").localeCompare(String(owned.get(a.id).date || "")))
      : POKEDEX.pokemon;
    const cells = rows.length
      ? rows.map((p) => {
          const c = owned.get(p.id);
          const art = c && c.shiny && p.artShiny ? p.artShiny : p.art;
          return `<div class="tx-cell ${c ? "" : "locked"} ${c && c.shiny ? "shiny" : ""}" data-id="${esc(p.id)}">
            <div class="no">No.${esc(p.id)}</div>
            <img src="${artUrl(art)}" loading="lazy" onerror="this.classList.add('broken')">
            <div class="nm">${c ? esc(p.nameZh) : "???"}</div>
            ${OWNED_ONLY && c && c.date ? `<div class="dt">${esc(String(c.date).slice(5))} 获得</div>` : ""}
          </div>`;
        }).join("")
      : `<div class="tx-empty">${OWNED_ONLY ? "图鉴还是空的 —— 读满今天的额度就会掉一只进来" : (lastError ? "连不上图鉴服务，稍后再试试" : "正在载入…")}</div>`;

    MODAL.innerHTML = `<div class="tx-panel">${head}${open}${tabs}<div class="tx-grid">${cells}</div></div>`;
    MODAL.querySelectorAll(".tx-tabs button").forEach((b) => {
      b.onclick = () => { OWNED_ONLY = b.dataset.tab === "owned"; paintModal(); };
    });
    MODAL.querySelectorAll(".tx-cell").forEach((el) => {
      el.onclick = () => {
        const p = POKEDEX.pokemon.find((x) => x.id === el.dataset.id);
        if (p) alert(`${p.nameZh}（No.${p.id}）\n${(p.types || []).join(" / ")} · ${p.category}\n`
          + `特性：${(p.abilities || []).join("、")}\n${p.flavor || ""}`);
      };
    });
  }

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  const style = document.createElement("style");
  style.textContent = CSS;
  (document.head || document.documentElement).appendChild(style);

  load();
  setInterval(load, REFRESH_MS);
})();
