let POKEDEX = null;
let DEX = { collected: [], streak: 0, today: {} };
let FILTER = "all";
const IMG = "img/";

const $ = (id) => document.getElementById(id);
const setText = (id, txt) => { const el = $(id); if (el) el.textContent = txt; };

// 世代边界
const GENS = [
  [1, 151], [152, 251], [252, 386], [387, 493], [494, 649],
  [650, 721], [722, 809], [810, 905], [906, 1025], [1026, 9999],
];
// 预告世代：游戏还没发售，资料来自公开预告，编号暂定
const PREVIEW_GENS = new Set([10]);
function genOf(id) {
  const n = Number(id);
  for (let i = 0; i < GENS.length; i++) if (n >= GENS[i][0] && n <= GENS[i][1]) return i + 1;
  return GENS.length;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function load() {
  const [pd, dx] = await Promise.all([
    fetch("pokedex.json").then((r) => r.json()),
    fetch("dex.json").then((r) => r.json()).catch(() => ({ collected: [], streak: 0, today: {} })),
  ]);
  POKEDEX = pd.pokemon;
  DEX = dx;
  render();
}

function ownedMap() {
  const m = new Map();
  for (const c of DEX.collected || []) {
    const prev = m.get(c.id);
    if (!prev) { m.set(c.id, c); continue; }
    // 同一只收集过多次时，保留闪光的那次（闪光更值钱，不能被后来的普通版盖掉）
    if (c.shiny && !prev.shiny) m.set(c.id, c);
  }
  return m;
}

function todayInfo() {
  const t = DEX.today || {};
  const thr = t.threshold || 1800;
  const sec = t.seconds || 0;
  return { sec, thr, pct: Math.min(100, Math.round((sec / thr) * 100)), date: t.date };
}

function render() {
  const owned = ownedMap();
  const n = owned.size;
  const total = POKEDEX.length;

  $("ring").style.setProperty("--p", total ? (n / total) * 100 : 0);
  setText("ringText", `${n}/${total}`);
  setText("summary", `图鉴 ${n}/${total} · 完成 ${((n / total) * 100).toFixed(1)}%`);
  setText("streak", `连读 ${DEX.streak || 0} 天 · 闪光概率 ${
    DEX.streak >= 100 ? "100%" :
    DEX.streak >= 30 ? "1/16" :
    DEX.streak >= 7 ? "1/32" :
    DEX.streak >= 3 ? "1/48" : "1/96"}`);

  const t = todayInfo();
  $("todayMini").hidden = false;
  setText("todayMiniVal", `${Math.round(t.sec / 60)}/${Math.round(t.thr / 60)} 分`);

  // 今日开箱卡
  const collected = DEX.collected || [];
  const todays = collected.filter((c) => c.date === t.date);
  const last = todays.length ? todays[todays.length - 1]
    : (collected.length ? collected[collected.length - 1] : null);
  const lp = last ? POKEDEX.find((x) => x.id === last.id) : null;
  if (last && lp && DEX.lastAwardDate === t.date) {
    $("today").hidden = false;
    setText("todayLabel", `今日捕获 · ${t.date}`);
    const art = last.shiny && lp.artShiny ? lp.artShiny : lp.art;
    $("todayCard").innerHTML = `
      <img src="${IMG}${art}" alt="${esc(lp.nameZh)}" onerror="this.classList.add('broken')">
      <div class="body">
        <div class="nm">${esc(lp.nameZh)}${last.shiny ? '<span class="shinyTag">✦ 闪光</span>' : ""}</div>
        <div class="sub">No.${esc(lp.id)} · ${esc((lp.types || []).join(" / "))} · ${esc(lp.category)}
          · 特性 ${esc((lp.abilities || []).join("、"))}</div>
        <div class="flavor">${esc(lp.flavor)}</div>
      </div>`;
  } else {
    $("today").hidden = true;
  }

  // 筛选条
  $("filters").innerHTML =
    [["all", "全部"], ...GENS.map((g, i) => [
      i + 1, `第 ${i + 1} 世代${PREVIEW_GENS.has(i + 1) ? " · 预告" : ""}`])]
      .map(([v, label]) =>
        `<button data-f="${v}" class="${String(v) === String(FILTER) ? "on" : ""}">${label}</button>`)
      .join("");
  $("filters").querySelectorAll("button").forEach((b) => {
    b.onclick = () => { FILTER = b.dataset.f; render(); };
  });

  // 图鉴墙
  const list = POKEDEX.filter((p) => FILTER === "all" || genOf(p.id) === Number(FILTER));
  $("grid").innerHTML = list.map((p) => {
    const c = owned.get(p.id);
    const art = c && c.shiny && p.artShiny ? p.artShiny : p.art;
    // 预告世代的条目：名字直接亮出来（本来就是公开资料），并挂个「预告」标记
    const prev = !!p.preview;
    const known = c || prev;
    const cls = c ? "" : (prev ? "preview" : "locked");
    return `<div class="cell ${cls} ${c && c.shiny ? "shiny" : ""}" data-id="${esc(p.id)}">
      <div class="no">No.${esc(p.id)}${prev && p.provisionalNo ? "（暂定）" : ""}</div>
      <img src="${IMG}${art}" alt="${esc(p.nameZh)}" loading="lazy" onerror="this.classList.add('broken')">
      <div class="nm">${known ? esc(p.nameZh) : "???"}${prev ? '<span class="prevTag">预告</span>' : ""}</div>
      <div class="ty">${esc((p.types || []).join("/"))}</div>
    </div>`;
  }).join("");
  $("grid").querySelectorAll(".cell").forEach((el) => {
    el.onclick = () => openSheet(el.dataset.id);
  });
}

function openSheet(id) {
  const p = POKEDEX.find((x) => x.id === id);
  if (!p) return;
  const c = ownedMap().get(id);
  const sheet = $("sheet");
  const inner = $("sheetInner");
  if (!c) {
    if (p.preview) {
      inner.innerHTML = `<button class="close" id="sheetClose">✕</button>
        <img src="${IMG}${p.art}" alt="${esc(p.nameZh)}" onerror="this.classList.add('broken')">
        <div class="nm">${esc(p.nameZh)}<span class="prevTag">预告</span></div>
        <div class="sub">No.${esc(p.id)}${p.provisionalNo ? "（编号暂定）" : ""} · ${esc((p.types || []).join(" / "))} · ${esc(p.category || "")}</div>
        <div class="rows">
          <div class="row"><b>特性</b><span>${esc((p.abilities || []).join("、") || "-")}</span></div>
          <div class="row"><b>身高</b><span>${esc(p.height || "-")}</span></div>
          <div class="row"><b>体重</b><span>${esc(p.weight || "-")}</span></div>
          <div class="row"><b>进化链</b><span>${esc((p.evolution || []).join(" → ") || "-")}</span></div>
          <div class="row"><b>日文名</b><span>${esc(p.nameJa || "-")}</span></div>
        </div>
        <div class="flavor">${esc(p.flavor)}</div>
        <div class="lockedMsg">第 10 世代《风/波》，2026-02-27 公布，游戏 2027 年发售。<br>编号与资料均为暂定 —— 但读满 ${Math.round((DEX.today?.threshold || 1800) / 60)} 分钟，照样能在图鉴里遇上它。</div>`;
    } else {
      inner.innerHTML = `<button class="close" id="sheetClose">✕</button>
        <img src="${IMG}${p.art}" alt="" onerror="this.classList.add('broken')">
        <div class="nm">???</div>
        <div class="sub">No.${esc(p.id)} · ${esc((p.types || []).join(" / "))}</div>
        <div class="lockedMsg">还没收服这一只。<br>读满 ${Math.round((DEX.today?.threshold || 1800) / 60)} 分钟就能遇上新的宝可梦。</div>`;
    }
  } else {
    const art = c.shiny && p.artShiny ? p.artShiny : p.art;
    inner.innerHTML = `<button class="close" id="sheetClose">✕</button>
      <img src="${IMG}${art}" alt="${esc(p.nameZh)}">
      <div class="nm">${esc(p.nameZh)}${c.shiny ? '<span class="shinyTag">✦ 闪光</span>' : ""}</div>
      <div class="sub">No.${esc(p.id)} · ${esc((p.types || []).join(" / "))} · ${esc(p.category)}</div>
      <div class="rows">
        <div class="row"><b>捕获日</b><span>${esc(c.date || c.at || "-")}</span></div>
        <div class="row"><b>特性</b><span>${esc((p.abilities || []).join("、") || "-")}</span></div>
        <div class="row"><b>身高</b><span>${esc(p.height || "-")}</span></div>
        <div class="row"><b>体重</b><span>${esc(p.weight || "-")}</span></div>
        <div class="row"><b>进化链</b><span>${esc((p.evolution || []).join(" → ") || "-")}</span></div>
        <div class="row"><b>日文名</b><span>${esc(p.nameJa || "-")}</span></div>
      </div>
      <div class="flavor">${esc(p.flavor)}</div>`;
  }
  sheet.hidden = false;
  $("sheetClose").onclick = () => { sheet.hidden = true; };
}

$("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) $("sheet").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("sheet").hidden = true; });

load().catch((e) => { setText("summary", "载入失败：" + e.message); });
