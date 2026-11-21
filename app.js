let POKEDEX = null;
let DEX = { collected: [], streak: 0, today: {} };
let SOURCES = null;   // 源注册表（sources.json）—— 阈值的权威来源
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

/** 拉一个 JSON，带超时与重试。
 *
 * **必须有超时**：GitHub Pages 的 CDN 偶尔会把大文件传成爬行速度
 * （实测 130KB 的 gzip 版传了 110 秒还没完），fetch 一直挂着，
 * 整页就永远停在「载入中…」—— 看起来像网站坏了，其实只是没超时。
 */
async function fetchJSON(name, timeout = 30000, retries = 2) {
  let lastErr = null;
  for (let i = 0; i <= retries; i++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(name, { signal: ctl.signal, cache: "no-cache" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) {
      lastErr = e;
      if (i < retries) await new Promise((r) => setTimeout(r, 1200 * (i + 1)));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr;
}

async function load() {
  // 三个文件各自兜底：状态没到不影响图鉴墙，阈值没到只影响兜底文案
  const [pd, dx, reg] = await Promise.all([
    fetchJSON("pokedex.json").catch(() => null),
    fetchJSON("dex.json").catch(() => null),
    fetchJSON("sources.json").catch(() => null),
  ]);
  if (!pd || !pd.pokemon) {
    setText("summary", "图鉴数据没加载上（网络问题）");
    const btn = document.createElement("button");
    btn.className = "retryBtn";
    btn.textContent = "重试";
    btn.onclick = () => { btn.remove(); setText("summary", "载入中…"); load(); };
    const box = $("summary").parentElement;
    if (box && !box.querySelector(".retryBtn")) box.appendChild(btn);
    return;
  }
  POKEDEX = pd.pokemon;
  DEX = dx || { collected: [], streak: 0, today: {} };
  SOURCES = reg;
  // 深链：?gen=10 直接打开第 10 世代（那一页只有 3 格，图片不用等懒加载）
  //      ?owned=1 直接打开「已获得」视图（已收服的那几只，不用在 1028 格里翻）
  try {
    const q = new URL(location.href).searchParams;
    if (q.get("owned") === "1") {
      FILTER = "owned";
    } else {
      const g = q.get("gen");
      if (g && (g === "all" || (Number(g) >= 1 && Number(g) <= GENS.length))) FILTER = g;
    }
  } catch (e) { /* 忽略 */ }
  render();
  if (!dx) {
    // 状态没加载上要说清楚 —— 不然「图鉴 0/1028」会让人以为全丢了
    setText("summary", "图鉴数据加载了，但状态没加载上（网络问题），刷新再试");
  }
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

// 「读满多少分钟」的文案 —— 阈值拿不到时不说具体数字（宁可少说一句，也不说错的）
function thresholdSentence(preview) {
  const thr = thresholdSeconds();
  if (thr > 0) {
    const m = Math.round(thr / 60);
    return preview
      ? `读满 ${m} 分钟，照样能在图鉴里遇上它。`
      : `读满 ${m} 分钟就能遇上新的宝可梦。`;
  }
  return preview
    ? "读满今天的阅读额度，照样能在图鉴里遇上它。"
    : "今天读满就能遇上新的宝可梦。";
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
  if (t.thr > 0) {
    $("todayMini").hidden = false;
    // 向下取整：不虚报进度。1776 秒四舍五入会显示成「30/30 分」，
    // 读起来像「今天已达标」，但其实额度 1 < 2，今天不会掉。
    // 分子还要夹到阈值：perAward=1 时阈值是 900 秒（15 分），而她今天已读 1776 秒（29 分）
    // ⇒ 不夹会显示成「29/15 分」（超过 100%）。满额时显示「15/15 分」。
    const thrMin = Math.floor(t.thr / 60);
    const readMin = Math.min(Math.floor(t.sec / 60), thrMin);
    setText("todayMiniVal", `${readMin}/${thrMin} 分`);
  } else {
    // 阈值拿不到 —— 不显示猜的进度（宁可空着）
    $("todayMini").hidden = true;
  }

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
  // 「已获得」放在最后并用强调色区分 —— 她目前只有 5 只，在 1028 格里翻很松散，
  // 一键只显示收服过的那几只才对。按钮上带实时计数。
  $("filters").innerHTML =
    [["all", "全部"], ["owned", `已获得 ${owned.size}`], ...GENS.map((g, i) => [
      i + 1, `第 ${i + 1} 世代${PREVIEW_GENS.has(i + 1) ? " · 预告" : ""}`])]
      .map(([v, label]) =>
        `<button data-f="${v}" class="${String(v) === String(FILTER) ? "on" : ""}${v === "owned" ? " fOwned" : ""}">${label}</button>`)
      .join("");
  $("filters").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      FILTER = b.dataset.f;
      // 写回地址栏，这样「?gen=10」「?owned=1」可以直接分享/收藏
      try {
        const u = new URL(location.href);
        u.searchParams.delete("gen");
        u.searchParams.delete("owned");
        if (FILTER === "owned") u.searchParams.set("owned", "1");
        else if (FILTER !== "all") u.searchParams.set("gen", FILTER);
        history.replaceState(null, "", u);
      } catch (e) { /* file:// 下没有 history，忽略 */ }
      render();
    };
  });

  // 图鉴墙
  // 「已获得」视图：只显示收服过的那几只，按获得日期倒序（最新收服的排最前），
  // 并在卡片上标出哪天收的 —— 这样一眼能看到「我最近收服了哪几只」。
  const list = FILTER === "owned"
    ? POKEDEX.filter((p) => owned.has(p.id)).sort((a, b) =>
        String(owned.get(b.id).date || "").localeCompare(String(owned.get(a.id).date || "")))
    : POKEDEX.filter((p) => FILTER === "all" || genOf(p.id) === Number(FILTER));
  if (!list.length) {
    $("grid").innerHTML = `<div class="gridEmpty">${
      FILTER === "owned"
        ? "图鉴还是空的 —— 读满今天的额度就会掉一只进来"
        : "这一代没有宝可梦"}</div>`;
  } else {
  $("grid").innerHTML = list.map((p) => {
    const c = owned.get(p.id);
    const art = c && c.shiny && p.artShiny ? p.artShiny : p.art;
    // 预告世代的条目：名字直接亮出来（本来就是公开资料），并挂个「预告」标记
    const prev = !!p.preview;
    const known = c || prev;
    const cls = c ? "" : (prev ? "preview" : "locked");
    return `<div class="cell ${cls} ${c && c.shiny ? "shiny" : ""}" data-id="${esc(p.id)}">
      <div class="no">No.${esc(p.id)}${prev && p.provisionalNo ? "（暂定）" : ""}</div>
      <img src="${IMG}${art}" alt="${esc(p.nameZh)}" ${prev ? 'loading="eager"' : 'loading="lazy"'} onerror="this.classList.add('broken')">
      <div class="nm">${known ? esc(p.nameZh) : "???"}${prev ? '<span class="prevTag">预告</span>' : ""}</div>
      <div class="ty">${esc((p.types || []).join("/"))}</div>
      ${FILTER === "owned" && c && c.date ? `<div class="dt">${esc(String(c.date).slice(5))} 获得</div>` : ""}
    </div>`;
  }).join("");
  }
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
        <div class="lockedMsg">第 10 世代《风/波》，2026-02-27 公布，游戏 2027 年发售。<br>编号与资料均为暂定 —— 但${thresholdSentence(true)}</div>`;
    } else {
      inner.innerHTML = `<button class="close" id="sheetClose">✕</button>
        <img src="${IMG}${p.art}" alt="" onerror="this.classList.add('broken')">
        <div class="nm">???</div>
        <div class="sub">No.${esc(p.id)} · ${esc((p.types || []).join(" / "))}</div>
        <div class="lockedMsg">还没收服这一只。<br>${thresholdSentence(false)}</div>`;
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
