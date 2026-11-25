/* ============================================================
   宝可梦番茄钟 · 中枢版
   —— 移植自 pokepomo（探头 / 摸鱼监督 / 1/96 监督宝可梦 / 音效 / 计时环），
      接上中枢的三件事：
        1. 图走图鉴页 CDN（拉不到退回剪影，不阻塞计时）
        2. 已收集清单走图鉴页 dex.json（优先派没收集过的）
        3. 专注分钟数通过「同步」交回服务端（GitHub Issue 投递箱）
   数据只存在本机 localStorage；同步时才联网。
   ============================================================ */
'use strict';

/* ---------- 中枢接线 ---------- */
const CDN = 'https://lilia-shound.github.io/pokemon-dex/';
const IMG_BASE = CDN + 'img/';
const REPO = 'lilia-shound/pokemon-dex';
const SOURCE_ID = 'pom';
const ISSUE_NEW = `https://github.com/${REPO}/issues/new`;
const MAX_SYNC_DAYS = 30;
/** 页面上能点选补录的天数（最近 7 天） */
const PICK_DAYS = 7;
/** 拉不到图时用的剪影（data URI，不外链） */
const SILHOUETTE = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
  '<circle cx="50" cy="50" r="40" fill="#8b7bff" opacity=".22"/>' +
  '<circle cx="50" cy="50" r="40" fill="none" stroke="#8b7bff" stroke-opacity=".35" stroke-width="2"/>' +
  '</svg>');

/* ---------- 免登录投递通道 ---------- */
/** 待发队列的 localStorage 键 —— 交不上去的先存这儿，下次打开页面自动补发。 */
const PENDING_KEY = 'pendingIngest';
/** 单次投递的超时（毫秒） */
const INGEST_TIMEOUT_MS = 15000;
/** 免登录通道的配置（从 sources.json 顶层读，见 loadList） */
let INGEST = null;
/**
 * 投递进行中（连点两次「同步」时用它挡住第二次）。
 * workers.dev 在国内慢，15 秒超时窗口里很容易再点一下 ⇒ 两个 POST 同时在飞；
 * 两个都失败的话，同一份报告会入队两次，白占 50 条上限、还把别的报告挤掉。
 */
let INGESTING = false;

/** 全局宝可梦清单（来自 pokedex-lite.json） */
let POKEMON = [];
/** 服务端已收服的 id 集合（来自图鉴页的 dex.json）——用来优先派没收集过的 */
let SERVER_OWNED = new Set();
/** 该源多少原始单位 = 1 个额度（来自 sources.json，拉不到就用默认 25 分钟） */
let UNIT_PER_CREDIT = 25;
/** 清单是否加载成功 */
let LIST_OK = false;

/* ---------- 小工具 ---------- */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const pad = n => String(n).padStart(2, '0');
const todayStr = () => {
  const d = new Date();
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
};
const dayDiff = (a, b) => {
  if (!a) return 99;
  const t = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d).getTime(); };
  return Math.round((t(b) - t(a)) / 86400000);
};
const rnd = n => Math.floor(Math.random() * n);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/** 图片拉不到时换成剪影（不重试、不阻塞计时） */
const imgOnError = "this.onerror=null;this.src=window.SILHOUETTE";
/** 宝可梦立绘地址 */
const artUrl = p => IMG_BASE + (p.art || '');
const artShinyUrl = p => (p.artShiny ? IMG_BASE + p.artShiny : artUrl(p));

/* ---------- 存储层（带版本号 + 世代号） ---------- */
const LS_KEY = 'pomodoro-hub.v1';
const VERSION = 1;
function defaultState() {
  return {
    settings: { focusMin: 25, breakMin: 5, peekCount: 'auto', supervisor: true, sound: true, autoBreak: true },
    dex: {},                                   // { pkmId: {count, firstAt, shiny} }
    days: {},                                  // { 'YYYY-MM-DD': {minutes, poms} }
    streak: { current: 0, lastDay: '' },
    totalPoms: 0, totalMinutes: 0, slacks: 0, roundSlacks: 0,
    lastSyncAt: 0, syncedDays: [],
  };
}
const Store = {
  epoch: 0,
  st: null,
  load() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); } catch (e) { raw = null; }
    if (!raw || !raw.data) {
      this.epoch = Date.now();
      this.st = { v: VERSION, epoch: this.epoch, data: defaultState() };
      this.write();
      return;
    }
    let s = raw;
    // 老版本（pokepomo 的 pokepomo.v1）不迁移 —— 键名不同，本来就是两套数据
    this.epoch = s.epoch || Date.now();
    this.st = s;
    const d = this.st.data;
    d.settings = Object.assign(defaultState().settings, d.settings || {});
    d.dex = d.dex || {};
    d.days = d.days || {};
    d.streak = d.streak || { current: 0, lastDay: '' };
    d.syncedDays = d.syncedDays || [];
  },
  write() {
    try {
      const cur = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
      if (cur && cur.epoch && cur.epoch !== this.epoch) {
        toast('另一个窗口改了数据，已放弃本次写入');
        return false;
      }
      localStorage.setItem(LS_KEY, JSON.stringify(this.st));
      return true;
    } catch (e) {
      toast('保存失败：' + (e && e.name === 'QuotaExceededError' ? '存储空间满了' : '浏览器拒绝写入'));
      return false;
    }
  },
  reset() {
    const fresh = { v: VERSION, epoch: Date.now(), data: defaultState() };
    this.epoch = fresh.epoch;
    this.st = fresh;
    try { localStorage.setItem(LS_KEY, JSON.stringify(fresh)); } catch (e) { /* 忽略 */ }
  }
};
Store.load();
const S = () => Store.st.data;

/* ---------- 音效（全程序合成，无音频文件） ---------- */
let AC = null;
function ac() {
  if (!AC) { try { AC = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; } }
  if (AC && AC.state === 'suspended') AC.resume();
  return AC;
}
function play(notes) {
  if (!S().settings.sound) return;
  const c = ac(); if (!c) return;
  const t0 = c.currentTime + 0.01;
  notes.forEach((n, i) => {
    const [f, d, type, gain] = n;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type || 'sine';
    o.frequency.setValueAtTime(f, t0 + i);
    g.gain.setValueAtTime(0.0001, t0 + i);
    g.gain.linearRampToValueAtTime(gain || 0.14, t0 + i + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + i + d);
    o.connect(g); g.connect(c.destination);
    o.start(t0 + i); o.stop(t0 + i + d + 0.03);
  });
}
const SFX = {
  start: [[392, .16], [523, .2]],
  peek: [[784, .1], [622, .13]],
  collect: [[784, .09], [988, .09], [1319, .2]],
  shiny: [[659, .08], [784, .08], [988, .08], [1319, .08], [1568, .28]],
  super: [[180, .28, 'square', .1], [140, .34, 'square', .09]],
  done: [[523, .13], [659, .13], [784, .13], [1047, .32]],
  slack: [[300, .1, 'triangle', .12], [220, .16, 'triangle', .12]],
  break: [[660, .2], [880, .28]]
};
const sfx = k => play(SFX[k] || []);

/* ---------- 提示气泡 ---------- */
let toastTimer = null;
function toast(msg, ms) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms || 2400);
}

/* ---------- 计时器状态 ---------- */
const T = {
  phase: 'idle',           // idle | focus | break | paused | settling
  endAt: 0,
  total: 0,
  elapsed: 0,              // 当前阶段有效进行时长（暂停不计入）
  pausedPhase: '',
  pausedLeft: 0,
  pausedTotal: 0,
  pausedElapsed: 0,
  plan: [],                // 本轮探头计划
  caught: [],              // 本轮捕获
  supervisor: null,        // 当前监督宝可梦的 DOM
  superOn: false
};
const RING_C = 2 * Math.PI * 108;

function fmt(ms) {
  const s = Math.ceil(ms / 1000);
  return pad(Math.floor(s / 60)) + ':' + pad(s % 60);
}
function phaseLabel() {
  if (T.phase === 'focus') return '专注中';
  if (T.phase === 'break') return '休息';
  if (T.phase === 'paused') return '已暂停';
  if (T.phase === 'settling') return '完成';
  return '准备开始';
}

/* ---------- 计划本轮探头：随机时刻 + 随机宝可梦 ---------- */
function planPeeks(durMs) {
  const mode = S().settings.peekCount;
  let n;
  if (mode === 'auto') {
    const m = durMs / 60000;
    n = m <= 15 ? 1 + rnd(2) : m <= 30 ? 2 + rnd(2) : m <= 45 ? 3 + rnd(2) : 3 + rnd(4);
  } else n = Number(mode) || 1;

  const lo = 0.07, hi = 0.93;
  const times = [];
  for (let i = 0; i < n; i++) times.push((lo + Math.random() * (hi - lo)) * durMs);
  times.sort((a, b) => a - b);

  const gap = durMs / (n + 2);
  for (let i = 1; i < times.length; i++) if (times[i] - times[i - 1] < gap) times[i] = times[i - 1] + gap;
  let cap = durMs * 0.96;
  for (let i = times.length - 1; i >= 0; i--) {
    if (times[i] > cap) { times[i] = cap; cap = times[i] - gap; }
  }

  // 优先派没收集过的（本机捡到的 + 服务端图鉴里有的都算收集过）
  const owned = new Set([...Object.keys(S().dex), ...SERVER_OWNED]);
  const fresh = POKEMON.filter(p => !owned.has(p.id));
  const pool = fresh.length >= n ? fresh.slice() : POKEMON.slice();
  const picks = [];
  for (let i = 0; i < n; i++) {
    if (pool.length) picks.push(pool.splice(rnd(pool.length), 1)[0]);
    else if (POKEMON.length) picks.push(POKEMON[rnd(POKEMON.length)]);
    else break;
  }
  return times.map((at, i) => ({ at, pkm: picks[i], done: false }));
}

/* ---------- 探头 ---------- */
function spawnPeek(pkm) {
  if (!pkm) return;
  const isSuper = S().settings.supervisor && !T.superOn && Math.random() < 1 / 96;
  const el = document.createElement('div');
  el.className = 'peek p' + rnd(4) + (isSuper ? ' sup' : '');
  el.innerHTML =
    '<div class="bubble"></div>' +
    (isSuper ? '<div class="eye">👁</div>' : '') +
    '<img src="' + esc(artUrl(pkm)) + '" alt="' + esc(pkm.nameZh) + '" onerror="' + imgOnError + '">' +
    '<div class="tag">' + esc(pkm.nameZh) + '<em>' + esc((pkm.types || []).join('/')) + '</em></div>';
  $('#stage').appendChild(el);
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('in')));

  const bubble = el.querySelector('.bubble');
  const say = (txt, ms) => {
    bubble.textContent = txt;
    bubble.classList.add('on');
    setTimeout(() => bubble.classList.remove('on'), ms || 1600);
  };
  say(isSuper ? '我在盯着你哦' : '嗨～', 1800);
  sfx(isSuper ? 'super' : 'peek');

  const life = setTimeout(() => {
    if (el.isConnected) el.classList.add('out');
    if (isSuper) { T.superOn = false; T.supervisor = null; }
  }, isSuper ? 60000 : 12000);
  el.addEventListener('click', () => {
    clearTimeout(life);
    collect(el, pkm, isSuper);
  });
  if (isSuper) { T.superOn = true; T.supervisor = el; }
}

function collect(el, pkm, isSuper) {
  const shiny = Math.random() < 1 / 128;
  const rec = S().dex[pkm.id] || (S().dex[pkm.id] = { count: 0, firstAt: Date.now(), shiny: false });
  rec.count++;
  if (shiny) rec.shiny = true;
  T.caught.push({ pkm, shiny });

  if (isSuper) {
    T.superOn = false; T.supervisor = null;
    toast('监督宝可梦被安抚了，它打着哈欠睡着了', 2600);
  } else {
    toast('收下了 ' + pkm.nameZh + (shiny ? ' ✨ 闪光！' : ''), 2600);
  }
  const b = el.querySelector('.bubble');
  b.textContent = shiny ? '✨ 闪光！' : '收下啦！';
  b.classList.add('on');
  if (shiny) el.classList.add('shiny');
  el.classList.add('out');
  sfx(shiny ? 'shiny' : 'collect');
  Store.write();
  renderDex(); renderTop();
  setTimeout(() => el.remove(), 700);
}

/* ---------- 摸鱼检测 ---------- */
let hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (T.phase === 'focus') hiddenAt = Date.now();
  } else {
    if (T.phase === 'focus' && hiddenAt && T.superOn) {
      const away = Date.now() - hiddenAt;
      if (away > 3000) slack(away);
    }
    hiddenAt = 0;
  }
});
function slack(awayMs) {
  S().slacks = (S().slacks || 0) + 1;
  S().roundSlacks = (S().roundSlacks || 0) + 1;
  const r = S().roundSlacks;
  sfx('slack');
  if (r >= 3) {
    toast('被监督宝可梦抓到摸鱼 3 次 —— 这一轮不计入统计了', 3800);
    abortRound(true);
  } else {
    toast('监督宝可梦：它看见你离开了 ' + Math.round(awayMs / 1000) + ' 秒（' + r + '/3）', 3200);
  }
  Store.write();
}

/* ---------- 流程控制 ---------- */
function startFocus() {
  const min = S().settings.focusMin;
  T.phase = 'focus';
  T.total = min * 60000;
  T.elapsed = 0;
  T.endAt = Date.now() + T.total;
  T.plan = planPeeks(T.total);
  T.caught = [];
  T.superOn = false;
  T.supervisor = null;
  S().roundSlacks = 0;
  $('#stage').innerHTML = '';
  sfx('start');
  Store.write(); render();
}
function startBreak() {
  const min = S().settings.breakMin;
  T.phase = 'break';
  T.total = min * 60000;
  T.elapsed = 0;
  T.endAt = Date.now() + T.total;
  sfx('break');
  Store.write(); render();
}
function pause() {
  if (T.phase !== 'focus' && T.phase !== 'break') return;
  T.pausedPhase = T.phase;
  T.pausedLeft = Math.max(0, T.endAt - Date.now());
  T.pausedTotal = T.total;
  T.pausedElapsed = T.elapsed;
  T.phase = 'paused';
  render();
}
function resume() {
  if (T.phase !== 'paused') return;
  T.phase = T.pausedPhase;
  T.total = T.pausedTotal;
  T.elapsed = T.pausedElapsed;
  T.endAt = Date.now() + T.pausedLeft;
  render();
}
function abortRound(bySlack) {
  T.phase = 'idle';
  T.total = 0;
  T.elapsed = 0;
  T.plan = [];
  T.caught = [];
  T.superOn = false;
  T.supervisor = null;
  S().roundSlacks = 0;
  $('#stage').innerHTML = '';
  render();
}
function completePhase() {
  if (T.phase === 'focus') {
    T.phase = 'settling';                 // 先脱离 focus，防止 tick 重复结算
    const today = todayStr();
    const d = S().days[today] || (S().days[today] = { minutes: 0, poms: 0 });
    d.minutes += S().settings.focusMin;
    d.poms += 1;
    S().totalPoms = (S().totalPoms || 0) + 1;
    S().totalMinutes = (S().totalMinutes || 0) + S().settings.focusMin;
    const st = S().streak;
    if (st.lastDay !== today) {
      st.current = (st.lastDay && dayDiff(st.lastDay, today) === 1) ? st.current + 1 : 1;
      st.lastDay = today;
    }
    sfx('done');
    showSummary();
    $('#stage').innerHTML = '';
    T.superOn = false;
    if (S().settings.autoBreak && S().settings.breakMin > 0) {
      setTimeout(() => { if (T.phase === 'settling') startBreak(); }, 900);
    } else {
      T.phase = 'idle'; T.total = 0;
    }
  } else if (T.phase === 'break') {
    T.phase = 'idle'; T.total = 0;
    sfx('done');
  }
  Store.write(); render();
}

/* ---------- 结算 ---------- */
function showSummary() {
  $('#sumTitle').textContent = '这一轮完成了！';
  $('#sumMin').textContent = S().settings.focusMin + ' 分钟';
  const row = $('#sumRow');
  row.innerHTML = '';
  if (T.caught.length) {
    T.caught.forEach(c => {
      const d = document.createElement('div');
      d.innerHTML = '<img src="' + esc(c.shiny && c.pkm.artShiny ? artShinyUrl(c.pkm) : artUrl(c.pkm)) +
        '" alt="' + esc(c.pkm.nameZh) + '" onerror="' + imgOnError + '"><span>' +
        esc(c.pkm.nameZh) + (c.shiny ? ' ✨' : '') + '</span>';
      row.appendChild(d);
    });
  }
  $('#sumText').innerHTML = T.caught.length
    ? '路上捡到 ' + T.caught.length + ' 只宝可梦。'
    : '这轮一只都没遇到 —— 下次再来，路上总有惊喜。';
  $('#mSum').classList.add('on');
}

/* ---------- 主循环 ---------- */
function tick() {
  if (T.phase === 'focus' || T.phase === 'break') {
    const now = Date.now();
    const left = Math.max(0, T.endAt - now);
    T.elapsed = T.total - left;
    if (T.phase === 'focus') {
      T.plan.forEach(p => {
        if (!p.done && T.elapsed >= p.at) { p.done = true; spawnPeek(p.pkm); }
      });
    }
    if (left <= 0) { completePhase(); return; }
  }
  render();
}
setInterval(tick, 200);

/* ---------- 渲染 ---------- */
function currentLeft() {
  if (T.phase === 'focus' || T.phase === 'break') return Math.max(0, T.endAt - Date.now());
  if (T.phase === 'paused') return T.pausedLeft;
  if (T.phase === 'settling') return 0;
  return T.total || S().settings.focusMin * 60000;
}
function render() {
  const left = currentLeft();
  $('#clock').textContent = fmt(left);
  $('#phaseTxt').textContent = phaseLabel();
  $('#ring').classList.toggle('idle', T.phase !== 'focus');
  const pct = T.total ? Math.max(0, Math.min(1, left / T.total)) : 1;
  $('#ringFg').style.strokeDasharray = RING_C;
  $('#ringFg').style.strokeDashoffset = RING_C * (1 - pct);
  const b = $('#btnMain');
  b.textContent = (T.phase === 'focus' || T.phase === 'break') ? '暂停'
                : T.phase === 'paused' ? '继续' : '开始';
  const running = (T.phase === 'focus' || T.phase === 'break');
  $('#btnBreak').style.display = running ? '' : 'none';
  $('#btnAbort').style.display = running ? '' : 'none';
  const ti = $('#taskInput');
  $('#taskTxt').textContent = ti ? ti.value.trim() : '';
  renderSync();
  renderDayStrip();
}
function renderTop() {
  const t = S().days[todayStr()] || { minutes: 0, poms: 0 };
  $('#sToday').textContent = t.minutes;
  $('#sPoms').textContent = t.poms;
  $('#sStreak').textContent = S().streak.current;
  $('#sDex').textContent = Object.keys(S().dex).length + '/' + POKEMON.length;
}
function renderDex() {
  const grid = $('#dexGrid');
  const empty = $('#dexEmpty');
  const recs = Object.entries(S().dex)
    .map(([id, rec]) => ({ p: POKEMON.find(x => x.id === id), rec }))
    .filter(x => x.p)
    .sort((a, b) => (b.rec.firstAt || 0) - (a.rec.firstAt || 0));
  grid.innerHTML = recs.map(({ p, rec }) =>
    '<div class="dexc' + (rec.shiny ? ' shiny' : '') + '">' +
    (rec.shiny ? '<div class="sh">✨</div>' : '') +
    '<img src="' + esc(rec.shiny && p.artShiny ? artShinyUrl(p) : artUrl(p)) +
    '" alt="' + esc(p.nameZh) + '" onerror="' + imgOnError + '">' +
    '<div class="nm">' + esc(p.nameZh) + '</div>' +
    '<div class="ct">' + esc((p.types || []).join('/')) + ' ×' + rec.count + '</div>' +
    '</div>').join('');
  if (empty) empty.style.display = recs.length ? 'none' : '';
  const shiny = Object.values(S().dex).filter(d => d.shiny).length;
  $('#dexSub').textContent = recs.length + ' 只' + (shiny ? ' · 闪光 ' + shiny : '');
}
function renderStats() {
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const k = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    days.push({ k, label: ['日', '一', '二', '三', '四', '五', '六'][d.getDay()], v: (S().days[k] || {}).minutes || 0, today: i === 0 });
  }
  const max = Math.max(30, ...days.map(d => d.v));
  $('#bars').innerHTML = days.map(d =>
    '<div class="bar' + (d.today ? ' today' : '') + '"><i style="height:' +
    Math.max(3, d.v / max * 88) + '%"></i><span>' + d.label + '</span></div>'
  ).join('');
  const s = S();
  const days7 = days.reduce((a, d) => a + d.v, 0);
  $('#statBox').innerHTML =
    '<div style="font-size:13px;line-height:2.1">' +
    '累计专注：<b style="color:#6ee7c8">' + (s.totalMinutes || 0) + '</b> 分钟<br>' +
    '累计番茄：<b style="color:#ff8fb1">' + (s.totalPoms || 0) + '</b> 个<br>' +
    '近 7 天：<b style="color:#ffd93d">' + days7 + '</b> 分钟<br>' +
    '摸鱼被抓：<b style="color:#ffb26b">' + (s.slacks || 0) + '</b> 次<br>' +
    '捡到宝可梦：<b style="color:#8b7bff">' + Object.keys(s.dex).length + '</b> 只' +
    '</div>';
}

/* ---------- 补录：改过去某一天的分钟数 ---------- */

/** 最近 N 天的日期串（今天在前）。 */
function pickDays(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    out.push(d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()));
  }
  return out;
}

/** 画那排日期格子 —— 已记过的显示分钟数。 */
function renderDayStrip() {
  const el = $('#dayStrip');
  if (!el) return;
  el.innerHTML = pickDays(PICK_DAYS).map(function (k) {
    const v = (S().days[k] || {}).minutes || 0;
    const w = ['日', '一', '二', '三', '四', '五', '六'][new Date(k).getDay()];
    return '<button class="day' + (v > 0 ? ' has' : '') + '" data-day="' + k + '">' +
      '<span class="d">' + k.slice(5) + '</span>' +
      '<span class="w">' + w + '</span>' +
      '<span class="v">' + (v > 0 ? v + ' 分' : '—') + '</span>' +
      '</button>';
  }).join('');
  $$('#dayStrip .day').forEach(function (btn) {
    btn.onclick = function () { openDayEditor(btn.getAttribute('data-day')); };
  });
}

/** 展开某一天的编辑器。 */
function openDayEditor(day) {
  const ed = $('#dayEditor');
  if (!ed) return;
  ed.hidden = false;
  ed.setAttribute('data-day', day);
  $('#dayEditorTitle').textContent = day + ' 专注了多久？';
  $('#dayMinutes').value = (S().days[day] || {}).minutes || '';
  $('#dayHint').textContent = day === todayStr()
    ? '就是今天 —— 这里填的会覆盖计时器记的数。'
    : '补记过去的日子 —— 额度按天算，零头不跨天凑。';
  $('#dayMinutes').focus();
}

/** 把这一天的分钟数写回 Store（唯一真值：S().days[day].minutes）。 */
function saveDay() {
  const ed = $('#dayEditor');
  const day = ed && ed.getAttribute('data-day');
  if (!day) return;
  const input = $('#dayMinutes');
  const n = Math.max(0, Math.min(1440, parseInt(input.value, 10) || 0));
  const row = S().days[day] || (S().days[day] = { minutes: 0, poms: 0 });
  row.minutes = n;
  if (n === 0) row.poms = 0;
  Store.write();
  renderDayStrip();
  renderSync();
  ed.hidden = true;
  toast(n > 0 ? ('记下了 ' + day + '：' + n + ' 分钟') : ('清掉了 ' + day + ' 的记录'));
}

/** 绑定日期格子的按钮。 */
function bindDayEditor() {
  const save = $('#daySave');
  const close = $('#dayClose');
  if (save) save.onclick = saveDay;
  if (close) close.onclick = function () {
    const ed = $('#dayEditor');
    if (ed) ed.hidden = true;
  };
}

/* ---------- 同步（Issue 投递箱） ---------- */
/** 待上报的天：{日期: 当天累计分钟数}，只带最近 30 天且有分钟的 */
function syncDays() {
  const days = {};
  Object.keys(S().days).sort().slice(-MAX_SYNC_DAYS).forEach(k => {
    const v = S().days[k];
    if (v && v.minutes > 0) days[k] = v.minutes;
  });
  return days;
}
/** 报告体：语义是「当天累计总量」（不是增量），服务端取 max 所以重复投递幂等 */
function buildReport() {
  return { v: 1, source: SOURCE_ID, days: syncDays() };
}
/** 生成 GitHub 预填的新建 issue 链接 */
function buildIssueUrl() {
  const title = `[report] ${SOURCE_ID} ${todayStr()}`;
  return ISSUE_NEW + '?title=' + encodeURIComponent(title) +
    '&body=' + encodeURIComponent(JSON.stringify(buildReport()));
}

/* ---------- 投递（免登录通道） ---------- */

/** 读待发队列（坏数据当空队列）。 */
function pendingQueue() {
  try {
    const raw = window.localStorage.getItem(PENDING_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}
/** 写待发队列（只留最近 50 条）。 */
function savePending(list) {
  try {
    // ★ 超限时**静默丢掉最老的那条** —— 这是规格里定的取舍，不改行为。
    //   丢的是最老的报告（最可能已经过期），保的是最近的 50 条。
    window.localStorage.setItem(PENDING_KEY, JSON.stringify((list || []).slice(-50)));
  } catch (e) { /* 存不住就算了 —— 顶多这次没交上 */ }
}
/**
 * 把一份报告排进待发队列。
 * @param {Object} report 要排队的报告
 * @param {boolean} toFront true = 塞回队首（补发失败时用，保持「最老在前」）
 */
function enqueueReport(report, toFront) {
  const list = pendingQueue();
  if (toFront) list.unshift(report); else list.push(report);
  savePending(list);
}
/** 取出队首（没有就 null）。 */
function shiftPending() {
  const list = pendingQueue();
  if (!list.length) return null;
  const head = list.shift();
  savePending(list);
  return head;
}

/**
 * 把一份报告 POST 到免登录通道。
 * 成功返回服务端的回包；失败**抛异常**（调用方负责提示与排队）。
 */
async function postIngest(report) {
  if (!ingestAvailable()) {
    throw new Error('免登录通道没开');
  }
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, INGEST_TIMEOUT_MS);
  let resp = null;
  try {
    resp = await fetch(INGEST.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dex-Token': INGEST.token || '' },
      body: JSON.stringify(report),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = null; }
  if (!resp.ok || !data || data.ok !== true) {
    throw new Error((data && data.error) ? data.error : ('HTTP ' + resp.status));
  }
  return data;
}

/** 免登录通道现在能不能用？（配置在、开着、有地址） */
function ingestAvailable() {
  return !!(INGEST && INGEST.enabled === true && INGEST.url);
}

/** 把上次没交上去的补发掉（页面打开时调一次）。 */
async function flushPending() {
  if (!ingestAvailable()) return;      // 通道没开 ⇒ 没有可补的（也别把它越攒越多）
  let sent = 0;
  let report = shiftPending();
  while (report) {
    try {
      await postIngest(report);
      sent++;
    } catch (e) {
      // 还是交不上去 ⇒ **放回队首**（不是队尾！）—— 队列是「最老在前」的，
      // 放回队尾会把它排到最后，越攒越晚才交上去。
      enqueueReport(report, true);
      break;
    }
    report = shiftPending();
  }
  if (sent) { toast('补发了 ' + sent + ' 条', 3000); renderSync(); }
}
/** 上次写进 #syncHint 的内容 —— 变了才重写 DOM（render() 每 200ms 调一次 renderSync）。 */
let lastSyncHintHTML = '';
function renderSync() {
  const box = $('#syncBox');
  if (!box) return;
  const t = S().days[todayStr()] || { minutes: 0, poms: 0 };
  const credits = Math.floor(t.minutes / UNIT_PER_CREDIT);
  const days = syncDays();
  const dayCount = Object.keys(days).length;
  let html = '今天专注了 <b>' + t.minutes + '</b> 分钟（' + t.poms + ' 个番茄）<br>' +
    '<span class="lit">' + UNIT_PER_CREDIT + ' 分钟 = 1 个额度 —— 今天这些值 <b style="font-size:14px">' +
    credits + '</b> 个额度</span>';
  if (dayCount) {
    html += '<br><span class="lit">累计 ' + dayCount + ' 天的分钟数会一起交上去</span>';
  }
  if (S().lastSyncAt) {
    const at = new Date(S().lastSyncAt);
    html += '<br><span class="lit">上次同步：' +
      (at.getMonth() + 1) + '-' + pad(at.getDate()) + ' ' + pad(at.getHours()) + ':' + pad(at.getMinutes()) + '</span>';
  }
  box.innerHTML = html;
  const btn = $('#btnSync');
  if (btn) btn.disabled = dayCount === 0;
  const sub = $('#syncSub');
  if (sub) sub.textContent = dayCount ? dayCount + ' 天' : '';
  const hint = $('#syncHint');
  if (hint) {
    // ★ 名字跟上面那个（box 的 html）不一样 —— 同名会互相遮蔽，读代码时容易看错。
    let hintHtml;
    if (!dayCount) {
      hintHtml = '还没有攒下分钟数 —— 完成一轮专注再来。';
    } else {
      // ★ 老办法的链接**照样留着**（workers.dev 在国内可能抽风，这是兜底）
      const oldWay = '<a id="syncOldWay" href="' + esc(buildIssueUrl()) +
        '" target="_blank" rel="noopener noreferrer">用 GitHub 提交（老办法）</a>';
      hintHtml = ingestAvailable()
        ? '点「同步到图鉴」直接交上去（不用登录 GitHub）。连不上？' + oldWay
        : '点同步会打开 GitHub 的新建 issue 页面，<b style="opacity:.9">在那个页面上点一下 Submit</b> 才算交上去。也可以直接' + oldWay;
    }
    // ★ 内容没变就别重写 —— render() 每 200ms 调一次 renderSync()，
    //   每次重建这个 <a> 会把「按下鼠标」到「松开鼠标」之间那个元素换掉 ⇒ 点击会丢。
    if (hintHtml !== lastSyncHintHTML) {
      hint.innerHTML = hintHtml;
      lastSyncHintHTML = hintHtml;
    }
  }
}
function doSync() {
  const days = syncDays();
  if (!Object.keys(days).length) { toast('还没有能交上去的分钟数'); return; }
  // ★ 连点两次「同步」⇒ 只发一个 POST。
  //   workers.dev 在国内慢，15 秒超时窗口里很容易再点一下 ⇒ 两个 POST 同时在飞；
  //   两个都失败的话，同一份报告会入队两次，白占 50 条上限、还把别的报告挤掉。
  if (INGESTING) { toast('正在交上去，稍等一下'); return; }
  // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
  //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点同步只会报错。
  if (!ingestAvailable()) {
    window.open(buildIssueUrl(), '_blank', 'noopener');
    S().lastSyncAt = Date.now();
    S().syncedDays = Object.keys(days);
    Store.write();
    renderSync();
    toast('已打开提交页 —— 在 GitHub 那个页面上点一下 Submit', 4200);
    return;
  }
  const report = buildReport();
  INGESTING = true;
  postIngest(report).then(function () {
    S().lastSyncAt = Date.now();
    S().syncedDays = Object.keys(days);
    Store.write();
    renderSync();
    toast('已记录 ' + Object.keys(days).length + ' 天', 3000);
  }).catch(function (err) {
    enqueueReport(report);
    renderSync();
    toast('暂时交不上去（' + (err && err.message ? err.message : '未知原因') +
      '）—— 已存着，下次打开自动补发', 4200);
  }).finally(function () {
    // ★ 成功、失败、异常都要清 —— 不清的话她再也点不动同步了。
    INGESTING = false;
  });
}

/* ---------- 加载清单与服务端已收集 ---------- */
function loadList() {
  // 宝可梦清单（番茄钟用的精简版）
  fetch('pokedex-lite.json', { cache: 'no-cache' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(d => {
      POKEMON = d.pokemon || [];
      LIST_OK = true;
      renderTop(); renderDex();
      $('#netStat').innerHTML = '宝可梦清单：' + POKEMON.length + ' 只 ✓';
    })
    .catch(e => {
      LIST_OK = false;
      $('#netStat').innerHTML = '宝可梦清单没加载上（' + esc(String(e.message || e)) +
        '）—— 计时不受影响，但这一轮不会有宝可梦探头。';
    });
  // 服务端已收服的清单（用来优先派没收集过的）
  fetch(CDN + 'dex.json', { cache: 'no-cache' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(d => {
      SERVER_OWNED = new Set((d.collected || []).map(c => c.id));
      $('#netStat').innerHTML = ($('#netStat').innerHTML || '') +
        '<br>图鉴页已收服 ' + SERVER_OWNED.size + ' 只（优先派没收集过的）';
    })
    .catch(() => { /* 拉不到就只用本机清单 */ });
  // 额度口径（多少分钟 = 1 个额度）
  fetch(CDN + 'sources.json', { cache: 'no-cache' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(d => {
      const per = d && d.sources && d.sources[SOURCE_ID] && d.sources[SOURCE_ID].per;
      if (per > 0) { UNIT_PER_CREDIT = per; renderSync(); }
      // 免登录通道的配置 —— **同一份 sources.json**，不再多发一个请求
      INGEST = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
      // 配置到了 ⇒ 把上次没交上去的补发掉
      // ★ 这里必须挂 .catch —— flushPending 是异步的，漏了会在控制台留一条
      //   "Unhandled promise rejection"，页面看着像坏了（浏览器的 console 错误）。
      flushPending().catch(() => { /* 补发失败就下次再说 */ });
    })
    .catch(() => { /* 用默认 25 */ });
}

/* ---------- 设置与界面 ---------- */
function buildChips() {
  const opts = [15, 20, 25, 30, 45, 60];
  $('#lenChips').innerHTML = opts.map(m =>
    '<button class="chip' + (S().settings.focusMin === m ? ' on' : '') + '" data-min="' + m + '">' +
    m + ' 分</button>').join('');
  $$('#lenChips .chip').forEach(b => {
    b.onclick = () => {
      if (T.phase === 'focus' || T.phase === 'break' || T.phase === 'paused') return;
      S().settings.focusMin = Number(b.dataset.min);
      Store.write(); buildChips(); render();
    };
  });
}
function buildSettings() {
  const f = $('#setFocus');
  [15, 20, 25, 30, 45, 60].forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = m + ' 分钟';
    f.appendChild(o);
  });
  f.value = S().settings.focusMin;
  f.onchange = e => { S().settings.focusMin = Number(e.target.value); Store.write(); buildChips(); render(); };

  const br = $('#setBreak');
  [0, 5, 10, 15].forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = m ? m + ' 分钟' : '不休息';
    br.appendChild(o);
  });
  br.value = S().settings.breakMin;
  br.onchange = e => { S().settings.breakMin = Number(e.target.value); Store.write(); render(); };

  $('#setPeek').value = S().settings.peekCount;
  $('#setPeek').onchange = e => { S().settings.peekCount = e.target.value; Store.write(); };

  const sw = (id, key) => {
    const el = $(id);
    el.classList.toggle('on', !!S().settings[key]);
    el.onclick = () => {
      S().settings[key] = !S().settings[key];
      el.classList.toggle('on', !!S().settings[key]);
      Store.write();
    };
  };
  sw('#swSuper', 'supervisor');
  sw('#swSound', 'sound');
  sw('#swAuto', 'autoBreak');
}
function bindTabs() {
  $$('#tabs button').forEach(b => {
    b.onclick = () => {
      $$('#tabs button').forEach(x => x.classList.toggle('on', x === b));
      $$('main section').forEach(s => s.classList.toggle('on', s.id === 'sec-' + b.dataset.tab));
      if (b.dataset.tab === 'dex') renderDex();
      if (b.dataset.tab === 'stats') renderStats();
    };
  });
}
function bindMain() {
  $('#btnMain').onclick = () => {
    if (T.phase === 'focus' || T.phase === 'break') pause();
    else if (T.phase === 'paused') resume();
    else startFocus();
  };
  $('#btnBreak').onclick = () => { if (T.phase === 'focus' || T.phase === 'break') completePhase(); };
  $('#btnAbort').onclick = () => { $('#mAbort').classList.add('on'); $('#abortInput').value = ''; };
  $('#abortCancel').onclick = () => $('#mAbort').classList.remove('on');
  $('#abortGo').onclick = () => {
    if ($('#abortInput').value.trim() === '我真的要放弃') {
      $('#mAbort').classList.remove('on');
      abortRound(false);
    } else {
      toast('想放弃的话，把「我真的要放弃」完整打出来');
    }
  };
  $('#sumOk').onclick = () => $('#mSum').classList.remove('on');
  $('#btnSync').onclick = doSync;
  $('#taskInput').oninput = () => { $('#taskTxt').textContent = $('#taskInput').value.trim(); };
  $('#btnExport').onclick = () => {
    const blob = new Blob([JSON.stringify(Store.st, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `pomodoro-${todayStr()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  $('#btnImport').onclick = () => $('#fileImport').click();
  $('#fileImport').onchange = e => {
    const f = e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      try {
        const d = JSON.parse(r.result);
        if (!d || !d.data) throw new Error('格式不对');
        Store.st = d;
        Store.epoch = d.epoch || Date.now();
        Store.write();
        buildChips(); buildSettings(); renderTop(); renderDex(); renderStats();
        toast('导入成功');
      } catch (err) {
        toast('导入失败：' + (err.message || err));
      }
    };
    r.readAsText(f);
    e.target.value = '';
  };
  $('#btnReset').onclick = () => {
    if (!confirm('清空本机的专注记录和捡到的宝可梦？')) return;
    Store.reset();
    buildChips(); buildSettings(); renderTop(); renderDex(); renderStats(); render();
    toast('已清空');
  };
}

/* ---------- PWA ---------- */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol === 'file:') return;   // file:// 下没有 SW
  navigator.serviceWorker.register('sw.js').catch(() => { /* 装不上也不影响用 */ });
}

/* ---------- 启动 ---------- */
buildChips();
buildSettings();
bindTabs();
bindMain();
bindDayEditor();
renderTop();
renderDex();
renderStats();
render();
loadList();
registerSW();
window.SILHOUETTE = SILHOUETTE;
