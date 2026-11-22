let POKEDEX = null;
let DEX = { collected: [], streak: 0, today: {} };
let SOURCES = null;   // 源注册表（sources.json）—— 额度的权威来源
let FILTER = "all";
const IMG = "img/";
let POKEDEX_BY_ID = new Map();   // id → 图鉴记录（查形态用，别线性找）

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

/* ── 额度（必须与服务端 scripts/sources.py 逐字同构） ────────────────────
   额度是中枢的中间货币：不同行为的单位不可通约（秒 / 分钟 / 个词），
   但「每天掉一只宝可梦」的名额是统一的。
   服务端的算式（scripts/sources.py 的 credit_for_day）：
       min(raw, maxPerDay) → max(0, …) → // per     # 每个源各自 floor
   当天总额度 = 各启用源的额度之和。
   **差一个额度就是「她明明凑够了，页面却说没够」** ⇒ 这段口径不许改。
*/

/** 一个源今天折算出多少额度（与服务端 credit_for_day 同一算式）。 */
function creditForSource(raw, src) {
  const per = Number(src && src.per);
  if (!(per >= 1)) return 0;          // per 不对就当 0（宁可不发也不错发）
  let amount = Number(raw);
  if (!Number.isFinite(amount)) amount = 0;
  amount = Math.trunc(amount);        // 服务端 int()：向零取整
  const cap = src.maxPerDay;
  if (cap !== undefined && cap !== null) {
    const c = Number(cap);
    if (Number.isFinite(c)) amount = Math.min(amount, c);
  }
  amount = Math.max(0, amount);
  return Math.floor(amount / per);
}

/** 注册表里启用的源（与服务端 enabled_sources 同一口径：enabled 为假就不算）。 */
function registrySources() {
  const reg = SOURCES && SOURCES.sources;
  if (!reg || typeof reg !== "object") return [];
  const out = [];
  for (const sid of Object.keys(reg)) {
    const s = reg[sid];
    if (!s || typeof s !== "object") continue;
    if (!s.enabled) continue;         // 停用的源不列、不结算
    out.push({
      id: sid,
      name: s.name || sid,
      icon: s.icon || "",
      unit: s.unit || "",
      cfg: s,
    });
  }
  return out;
}

/** 今天各源的原始量（today.sources；**没有这个键就是 0**）。 */
function todayRawAmounts() {
  const map = (DEX.today || {}).sources;
  return (map && typeof map === "object") ? map : {};
}

/** 今日各源明细：原始量 + 折算出来的额度。 */
function sourceCredits() {
  const raw = todayRawAmounts();
  return registrySources().map((s) => {
    const n = Number(raw[s.id]);
    const amount = Number.isFinite(n) ? n : 0;
    return {
      id: s.id,
      name: s.name,
      icon: s.icon,
      unit: s.unit,
      raw: amount,
      credit: creditForSource(amount, s.cfg),
    };
  });
}

/** 攒够几个额度掉 1 只（注册表优先；拿不到就用状态文件里那个）。 */
function perAwardOf() {
  const fromReg = Number(SOURCES && SOURCES.perAward);
  if (Number.isFinite(fromReg) && fromReg >= 1) return Math.floor(fromReg);
  const fromState = Number((DEX.today || {}).perAward);
  if (Number.isFinite(fromState) && fromState >= 1) return Math.floor(fromState);
  return 0;
}

/** 原始量按注册表里的单位写出来（1753 秒 / 25 分钟 / 0 个）。 */
function formatRaw(value, unit) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return unit ? `${n} ${unit}` : `${n}`;
}

/** 秒太多就折成分钟（1800 秒 ⇒ 30 分钟）—— 用在「几个额度 ≈ 多少」那种文案里。 */
function formatNice(value, unit) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (unit === "秒" && n % 60 === 0) return `${n / 60} 分钟`;
  return formatRaw(n, unit);
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
  POKEDEX_BY_ID = buildPokedexMap(POKEDEX);
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

/** id → 图鉴记录（1028 只，别每次线性找）。 */
function buildPokedexMap(list) {
  const m = new Map();
  for (const p of list || []) m.set(String(p.id), p);
  return m;
}

/**
 * 收服时的那只形态 —— collected[].form（进化了也显示进化形态，别再显示基础形态）。
 *
 * 三级回退：**form → id → 没有（剪影）**。
 * 老数据没有 form ⇒ 回退到 id；万一 form 在图鉴里查不到 ⇒ 也回退到 id。
 */
function formEntry(c) {
  if (!c) return null;
  const formId = (c.form !== undefined && c.form !== null && c.form !== "")
    ? String(c.form) : String(c.id);
  return POKEDEX_BY_ID.get(formId) || POKEDEX_BY_ID.get(String(c.id)) || null;
}

/** 「妙蛙种子 → 妙蛙花」：形态和基础形态不是同一只时写这一行。 */
function formLine(base, form) {
  if (!base || !form || String(base.id) === String(form.id)) return "";
  return `${base.nameZh} → ${form.nameZh}`;
}

// 页面**不再**拿 today.threshold 当任何数字：
// 那是发放器按「微信读书专用口径」写的快照（perAward × sources.weread.per），
// 拿它算多源额度就会算错（它看不见另外三个源）。
// ⇒ 原来的 registryThreshold() / thresholdSeconds() 一并删掉了；
//   多源额度一律走 creditForSource（与服务端 credit_for_day 同构）。

/** 今日各源的额度明细 + 合计（顶栏与文案都用它）。 */
function todayInfo() {
  const rows = sourceCredits();
  const perAward = perAwardOf();
  const total = rows.reduce((sum, r) => sum + r.credit, 0);
  return { rows, perAward, total, date: stateToday() };
}

/** 状态文件里那个「今天」（服务端口径；没有就是空串）。 */
function stateToday() {
  const d = (DEX.today || {}).date;
  return (typeof d === "string" && d) ? d : "";
}

/** 算天数差用的基准日：优先状态文件里的今天（服务端同一个口径），拿不到就用本地日期。 */
function referenceDate() {
  const d = stateToday();
  if (d) return d;
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** 两个 ISO 日期之间隔了几天（按 UTC 算，不受浏览器时区影响）。 */
function daysBetween(fromISO, toISO) {
  const a = Date.parse(`${fromISO}T00:00:00Z`);
  const b = Date.parse(`${toISO}T00:00:00Z`);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.round((b - a) / 86400000);
}

/** 「N 个额度 ≈ 多少」的等价值文案。 */
function equivalentsText(perAward) {
  const parts = [];
  for (const s of registrySources()) {
    const per = Number(s.cfg.per);
    if (!(per >= 1)) continue;
    parts.push(`${s.name} ${formatNice(per * perAward, s.unit)}`);
  }
  // 注册表没加载上 ⇒ **不说具体数字**（宁可少说一句，也不说错的）：
  // today.threshold 是微信读书专用的旧口径，不能拿它当多源的等价值。
  return parts.join(" / ");
}

// 「凑满多少额度」的文案 —— 额度口径拿不到时不说具体数字（宁可少说一句，也不说错的）
function thresholdSentence(preview) {
  const perAward = perAwardOf();
  if (!perAward) {
    return preview
      ? "凑满今天的额度，照样能在图鉴里遇上它。"
      : "今天凑够额度就能遇上新的宝可梦。";
  }
  const eq = equivalentsText(perAward);
  const head = eq ? `凑满 ${perAward} 个额度（${eq}）` : `凑满 ${perAward} 个额度`;
  return preview
    ? `${head}，照样能在图鉴里遇上它。`
    : `${head}就能遇上新的宝可梦。`;
}

/**
 * 画顶栏的「今日额度」面板。
 *
 * 每个启用的源一行：图标 + 名字 + 今日原始量 → 折算出的额度；
 * 下面一条总进度 = 合计额度 / perAward。
 *
 * **额度口径与服务端 scripts/sources.py 的 credit_for_day 逐字同构** ——
 * 每个源各自 floor，零头不跨天凑（见 creditForSource）。
 */
function renderCreditPanel() {
  const panel = $("creditPanel");
  if (!panel) return;
  const rowsBox = $("cpRows");
  const totalBox = $("cpTotal");
  const fillBox = $("cpFill");
  const noteBox = $("cpNote");
  const t = todayInfo();

  if (!t.rows.length) {
    // 注册表没加载上（或一个启用的源都没有）⇒ 各源明细说不清，
    // 但**不能一个字都不说**：总额度还有状态文件里那个（服务端自己算的）。
    panel.hidden = false;
    rowsBox.innerHTML = "";
    const credit = Number((DEX.today || {}).credit);
    totalBox.textContent = (Number.isFinite(credit) && credit >= 0 && t.perAward)
      ? `${credit} / ${t.perAward} 个额度`
      : "今日额度";
    if (fillBox) fillBox.style.width = "0%";
    noteBox.textContent = "各源明细暂时读不到（源注册表没加载上）";
    return;
  }

  panel.hidden = false;
  rowsBox.innerHTML = t.rows.map((r) => `
    <div class="cpRow" data-src="${esc(r.id)}">
      <span class="cpIcon">${esc(r.icon)}</span>
      <span class="cpName">${esc(r.name)}</span>
      <span class="cpRaw">${esc(formatRaw(r.raw, r.unit))}</span>
      <span class="cpArrow">→</span>
      <span class="cpCredit">${r.credit} 个额度</span>
    </div>`).join("");
  totalBox.textContent = t.perAward
    ? `${t.total} / ${t.perAward} 个额度`
    : `${t.total} 个额度`;
  if (fillBox) {
    const pct = t.perAward > 0 ? Math.min(100, (t.total / t.perAward) * 100) : 0;
    fillBox.style.width = `${pct}%`;
  }
  noteBox.textContent = !t.perAward
    ? "还差几个额度才能掉一只，暂时算不出来（源注册表没加载上）"
    : (t.total >= t.perAward
      ? "今天已经凑够啦 —— 新的宝可梦在等你。"
      : `还差 ${t.perAward - t.total} 个额度就能遇上新的宝可梦。`);
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

  // 今日额度（多源）：每个源一行 —— 原始量 → 折算出的额度，下面一条总进度
  renderCreditPanel();

  const t = todayInfo();

  // 今日开箱卡
  const collected = DEX.collected || [];
  const todays = collected.filter((c) => c.date === t.date);
  const last = todays.length ? todays[todays.length - 1]
    : (collected.length ? collected[collected.length - 1] : null);
  const lp = last ? formEntry(last) : null;
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
        ? "图鉴还是空的 —— 今天凑够额度就会掉一只进来"
        : "这一代没有宝可梦"}</div>`;
  } else {
  $("grid").innerHTML = list.map((p) => {
    const c = owned.get(p.id);
    // 显示的是**收服时的那只形态**（collected[].form），不是基础形态
    const fe = c ? formEntry(c) : null;
    const shown = fe || p;
    const art = c && c.shiny && shown.artShiny ? shown.artShiny : shown.art;
    // 预告世代的条目：名字直接亮出来（本来就是公开资料），并挂个「预告」标记
    const prev = !!p.preview;
    const known = c || prev;
    const cls = c ? "" : (prev ? "preview" : "locked");
    const fl = c && fe ? formLine(p, fe) : "";
    return `<div class="cell ${cls} ${c && c.shiny ? "shiny" : ""}" data-id="${esc(p.id)}">
      <div class="no">No.${esc(p.id)}${prev && p.provisionalNo ? "（暂定）" : ""}</div>
      <img src="${IMG}${art}" alt="${esc(shown.nameZh)}" ${prev ? 'loading="eager"' : 'loading="lazy"'} onerror="this.classList.add('broken')">
      <div class="nm">${known ? esc(shown.nameZh) : "???"}${prev ? '<span class="prevTag">预告</span>' : ""}</div>
      ${fl ? `<div class="form">${esc(fl)}</div>` : ""}
      <div class="ty">${esc((shown.types || []).join("/"))}</div>
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
    // 已收服：显示的是**收服时的那只形态**（collected[].form）
    const fe = formEntry(c) || p;
    const art = c.shiny && fe.artShiny ? fe.artShiny : fe.art;
    const fl = formLine(p, fe);
    inner.innerHTML = `<button class="close" id="sheetClose">✕</button>
      <img src="${IMG}${art}" alt="${esc(fe.nameZh)}">
      <div class="nm">${esc(fe.nameZh)}${c.shiny ? '<span class="shinyTag">✦ 闪光</span>' : ""}</div>
      ${fl ? `<div class="form">${esc(fl)}</div>` : ""}
      <div class="sub">No.${esc(fe.id)} · ${esc((fe.types || []).join(" / "))} · ${esc(fe.category)}</div>
      <div class="rows">
        <div class="row"><b>捕获日</b><span>${esc(c.date || c.at || "-")}</span></div>
        <div class="row"><b>特性</b><span>${esc((fe.abilities || []).join("、") || "-")}</span></div>
        <div class="row"><b>身高</b><span>${esc(fe.height || "-")}</span></div>
        <div class="row"><b>体重</b><span>${esc(fe.weight || "-")}</span></div>
        <div class="row"><b>进化链</b><span>${esc((fe.evolution || []).join(" → ") || "-")}</span></div>
        <div class="row"><b>日文名</b><span>${esc(fe.nameJa || "-")}</span></div>
      </div>
      <div class="flavor">${esc(fe.flavor)}</div>`;
  }
  sheet.hidden = false;
  $("sheetClose").onclick = () => { sheet.hidden = true; };
}

$("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) $("sheet").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("sheet").hidden = true; });

// 给测试看的小窗口（页面自己不用它）—— 与 site/words/app.js 的 WordsLanding 同一套路：
// 测试要能**在浏览器里**问出「页面算出来的额度是多少」，才好跟服务端对拍。
window.DexPage = {
  creditForSource,
  sourceCredits,
  perAwardOf,
  thresholdSentence,
  stateToday,
  referenceDate,
  daysBetween,
  formEntry,
  formLine,
  get state() { return { dex: DEX, sources: SOURCES }; },
};

load().catch((e) => { setText("summary", "载入失败：" + e.message); });
