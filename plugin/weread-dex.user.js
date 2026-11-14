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
  let MODAL = null;
  let lastError = "";

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
  .tx-cell{background:#fffdf8;border:1px solid #e9e0d0;border-radius:14px;padding:8px 6px 10px;
    text-align:center;cursor:pointer;transition:transform .18s cubic-bezier(.34,1.56,.64,1)}
  .tx-cell:hover{transform:translateY(-3px)}
  .tx-cell img{width:70px;height:70px;object-fit:contain;display:block;margin:2px auto 0}
  .tx-cell.locked img{filter:grayscale(1) opacity(.18)}
  .tx-cell .no{font-size:10px;color:#9a8d80}
  .tx-cell .nm{font-size:12px;margin-top:2px;min-height:1.4em}
  .tx-cell.shiny{box-shadow:0 0 0 2px #c9a227 inset}
  img.broken{opacity:.1}
  .tx-empty{padding:30px;text-align:center;color:#9a8d80;font-size:13px}
  `;

  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function artUrl(u) {
    return u || "";   // pokedex.json 里已经是完整直链
  }

  function todayInfo() {
    const t = DEX.today || {};
    const thr = t.threshold || 1800;
    const sec = t.seconds || 0;
    return { sec, thr, pct: Math.min(100, Math.round((sec / thr) * 100)), date: t.date };
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
    paintCaps();
    if (MODAL) paintModal();
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
    el.innerHTML = `<span class="num">${n}</span><span class="lbl">图鉴</span>`
      + `<span class="bar"><i style="width:${i.pct}%"></i></span>`
      + `<span class="num">${Math.round(i.sec / 60)}/${Math.round(i.thr / 60)}</span>`;
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
      <span class="dim">连读 ${DEX.streak || 0} 天 · 今日 ${Math.round(t.sec / 60)}/${Math.round(t.thr / 60)} 分 · 点空白处关闭</span>
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

    const cells = POKEDEX.pokemon.length
      ? POKEDEX.pokemon.map((p) => {
          const c = owned.get(p.id);
          const art = c && c.shiny && p.artShiny ? p.artShiny : p.art;
          return `<div class="tx-cell ${c ? "" : "locked"} ${c && c.shiny ? "shiny" : ""}" data-id="${esc(p.id)}">
            <div class="no">No.${esc(p.id)}</div>
            <img src="${artUrl(art)}" loading="lazy" onerror="this.classList.add('broken')">
            <div class="nm">${c ? esc(p.nameZh) : "???"}</div>
          </div>`;
        }).join("")
      : `<div class="tx-empty">${lastError ? "连不上图鉴服务，稍后再试试" : "正在载入…"}</div>`;

    MODAL.innerHTML = `<div class="tx-panel">${head}${open}<div class="tx-grid">${cells}</div></div>`;
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
