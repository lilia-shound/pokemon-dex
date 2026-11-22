/* ============================================================
   备考（背单词）记数页
   —— 手机上打开就能记：输入今天背了多少个词 → 折算成额度
      → 一键打开**预填好的** GitHub 新建 issue 页（人点 Submit）。
   —— **这一页不发任何写请求** —— 抽卡权只在服务端，这里只报数。
   —— 额度按**天**向下取整：floor(150/100) + floor(50/100) = 1 + 0 = 1，
      **不是** floor(200/100) = 2（零头不跨天凑）。
      与服务端 scripts/sources.py 的 credit_for_day 口径一致 ✓。
   —— 零外链：字体/图标全用系统自带；唯一的网络请求是**同源**的 sources.json。
   ============================================================ */
'use strict';

(function () {

  /* ---------- 中枢接线 ---------- */
  const SOURCE_ID = 'words';
  const REPO = 'lilia-shound/pokemon-dex';
  const ISSUE_NEW = 'https://github.com/' + REPO + '/issues/new';
  /** 拉不到 sources.json 时的兜底口径（与服务端注册表一致：100 个词 = 1 个额度） */
  const DEFAULT_PER = 100;
  /** 单日上限（个）—— 服务端 credit_for_day 会先夹到它再折算 */
  const DEFAULT_MAX_PER_DAY = 500;
  /** 攒够几个额度掉 1 只（兜底值；注册表里有 perAward） */
  const DEFAULT_PER_AWARD = 2;
  /** 一次投递最多带多少天（与服务端 MAX_REPORT_DAYS 对齐） */
  const MAX_REPORT_DAYS = 30;
  /** 页面上显示最近几天（点某天可以补录） */
  const WINDOW_DAYS = 7;
  /** 本机存储的键 */
  const LS_KEY = 'pokemon-dex-words-v1';
  /**
   * 注册表（sources.json）的路径 —— **相对路径**、同源，不许外链。
   *
   * 为什么是 `../sources.json`（站根那份），不是同目录的 `sources.json`：
   * 站根那份（`site/sources.json`）是 `scripts/deploy.sh` 从 `data/sources.json`
   * 自动同步出来的**部署产物** ⇒ 改 `data/sources.json` 的 `per`，重新部署就生效，
   * **永远不会漂移**。同目录再放一份手工副本的话，部署脚本根本不管它 ⇒
   * 页面显示的额度会和服务端算账用的口径对不上。
   */
  const REGISTRY_PATH = '../sources.json';

  /* ---------- 纯逻辑（浏览器与 node 都跑得起来） ---------- */

  /**
   * 一天折算多少额度：**按天**向下取整（零头不跨天凑）。
   * 与服务端 scripts/sources.py 的 credit_for_day 口径一致：
   * 先夹到单日上限、再夹到 0，最后 // per。
   * @param {number} amount 当天的原始量（个）
   * @param {number} per 多少个词 = 1 个额度
   * @param {number} cap 单日上限（个）
   * @returns {number}
   */
  function creditsForDay(amount, per, cap) {
    let n = Number(amount);
    if (!Number.isFinite(n)) n = 0;
    if (n > cap) n = cap;
    if (n < 0) n = 0;
    return Math.floor(n / per);
  }

  /**
   * 这些天一共折算多少个额度：**按天 floor 再相加**。
   * 例：150 个 + 50 个、per=100 ⇒ 1 + 0 = 1（不是 floor(200/100) = 2）。
   * @param {Object<string, number>} days
   * @param {number} per
   * @param {number} cap
   * @returns {number}
   */
  function creditsForDays(days, per, cap) {
    const p = Number(per) >= 1 ? Number(per) : DEFAULT_PER;
    const c = Number(cap) >= 0 ? Number(cap) : DEFAULT_MAX_PER_DAY;
    let total = 0;
    for (const d of Object.keys(days || {})) total += creditsForDay(days[d], p, c);
    return total;
  }

  /**
   * 把每天的个数**夹到单日上限**。
   *
   * 为什么要在页面夹：服务端 scripts/collect_reports.py 的规则是
   * 「任一天超过该源的 maxPerDay ⇒ **整份报告丢弃**」⇒ 页面只字不提的话，
   * 她会以为交上去了。所以编 body 时先夹住，夹过的那些天要在页面上说清楚。
   *
   * @param {Object<string, number>} days
   * @param {number} cap 单日上限（个）
   * @returns {{days: Object<string, number>, clamped: Array<{day: string, raw: number, capped: number}>}}
   */
  function clampDays(days, cap) {
    const limit = Number(cap) >= 0 ? Number(cap) : DEFAULT_MAX_PER_DAY;
    const out = {};
    const clamped = [];
    Object.keys(days || {}).forEach(d => {
      const raw = Number(days[d]);
      let amount = Number.isFinite(raw) ? raw : 0;
      if (amount > limit) amount = limit;
      if (amount < 0) amount = 0;
      out[d] = amount;
      // 只有「超上限被夹」才提示（那是她该知道、也该改的事）；
      // 负数 / 非数是脏数据 ⇒ 静默归零（不然服务端会把整份报告丢掉）。
      if (raw > limit) clamped.push({ day: d, raw: raw, capped: amount });
    });
    return { days: out, clamped: clamped };
  }

  /**
   * 最新的一天（日期倒序里的第一个）。ISO 日期字符串比大小就是比日期先后。
   * @param {Object<string, number>} days
   * @returns {string} 没有就空串
   */
  function latestDay(days) {
    const keys = Object.keys(days || {}).sort();
    return keys.length ? keys[keys.length - 1] : '';
  }

  /**
   * 报告体：{v, source, days} —— 服务端 scripts/collect_reports.py 的 parse_report 认这个格式。
   * days 只带**有量**的天（零不报）；键按**日期倒序**排。
   * @param {Object<string, number>} days
   * @returns {Object}
   */
  function buildReport(days) {
    const ordered = {};
    Object.keys(days || {}).sort().reverse().forEach(d => {
      const v = Number(days[d]);
      if (Number.isFinite(v) && v > 0) ordered[d] = Math.floor(v);
    });
    return { v: 1, source: SOURCE_ID, days: ordered };
  }

  /**
   * issue 的标题：`[report] words <最新的一天>`（与番茄钟那套同一个格式）。
   * @param {Object<string, number>} days
   * @returns {string}
   */
  function buildIssueTitle(days) {
    return '[report] ' + SOURCE_ID + ' ' + latestDay(days);
  }

  /**
   * 预填好的 GitHub 新建 issue 链接。
   * @param {Object<string, number>} days
   * @returns {string}
   */
  function buildIssueUrl(days) {
    return ISSUE_NEW + '?title=' + encodeURIComponent(buildIssueTitle(days)) +
      '&body=' + encodeURIComponent(JSON.stringify(buildReport(days)));
  }

  /* ---------- 日期小工具 ---------- */
  const pad = n => String(n).padStart(2, '0');
  const dateKey = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const todayKey = () => dateKey(new Date());
  const isKey = k => typeof k === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(k);
  const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];
  function weekdayOf(key) {
    const p = key.split('-').map(Number);
    return WEEKDAYS[new Date(p[0], p[1] - 1, p[2]).getDay()];
  }
  /** 短标签：11-22 */
  function shortLabel(key) {
    return isKey(key) ? key.slice(5) : key;
  }
  /** 最近 n 天的日期键（**从早到晚**） */
  function lastDays(n) {
    const out = [];
    const d = new Date();
    for (let i = n - 1; i >= 0; i--) {
      const t = new Date(d.getFullYear(), d.getMonth(), d.getDate() - i);
      out.push(dateKey(t));
    }
    return out;
  }

  /* ---------- 页面状态 ---------- */

  /**
   *  - `days`   —— {日期: 个数}（她输入的**原值**）
   *  - `per` / `cap` / `perAward` —— 额度口径（来自注册表，拉不到就用兜底值）
   *  - `selected` —— 正在编辑的那一天（默认今天）
   *  - `registryLoaded` —— 注册表**已经生效**（读到真注册表了，或者兜底口径生效了）
   *  - `storageOk` —— 本机存储可用（隐私模式下不可用 ⇒ 页面照常用，只是不记住）
   *  - `enabled` —— 注册表里这个源是不是启用的（`enabled` 不为 true ⇒ 服务端拒收上报）
   *  - `linkNote` —— 链接参数（`?day=` / `?words=`）带来的提示
   */
  const state = {
    days: {}, selected: todayKey(),
    per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, perAward: DEFAULT_PER_AWARD,
    registryLoaded: false, storageOk: true,
    enabled: true, linkNote: '',
  };

  const API = {
    creditsForDay, creditsForDays, clampDays, latestDay,
    buildReport, buildIssueTitle, buildIssueUrl,
    lastDays, todayKey, shortLabel, weekdayOf,
    DEFAULT_PER, DEFAULT_MAX_PER_DAY, SOURCE_ID, ISSUE_NEW, REPO,
    WINDOW_DAYS, MAX_REPORT_DAYS, REGISTRY_PATH,
    state,
  };

  /* node 侧（如果有人要对拍）可以 require 这个文件；
     浏览器侧则把同一份 API 挂到 window 上，方便浏览器测试直接问。 */
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  if (typeof globalThis !== 'undefined') globalThis.WordsLanding = API;

  /* ---------- 页面部分（只在浏览器里跑） ---------- */
  if (typeof document === 'undefined') return;

  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];

  /* ---------- 存储层（隐私模式下会抛异常 ⇒ 全兜住） ---------- */

  function loadDays() {
    try {
      const raw = window.localStorage.getItem(LS_KEY);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      const days = obj && obj.days;
      if (!days || typeof days !== 'object') return {};
      const out = {};
      Object.keys(days).forEach(k => {
        if (!isKey(k)) return;
        const v = Number(days[k]);
        if (!Number.isFinite(v) || v <= 0) return;
        out[k] = Math.floor(v);
      });
      return out;
    } catch (e) {
      // 隐私模式 / 沙箱 iframe 下 localStorage 会抛异常 ⇒ 记不住就记不住，页面照常用。
      state.storageOk = false;
      return {};
    }
  }

  function saveDays() {
    try {
      window.localStorage.setItem(LS_KEY, JSON.stringify({ v: 1, days: state.days }));
      state.storageOk = true;
      return true;
    } catch (e) {
      state.storageOk = false;
      return false;
    }
  }

  /** 只留最近 MAX_REPORT_DAYS 天（与服务端一次最多收的天数对齐）—— 更早的量页面也看不见了。 */
  function trimDays(days) {
    const keep = new Set(lastDays(MAX_REPORT_DAYS));
    const out = {};
    Object.keys(days || {}).forEach(k => { if (keep.has(k)) out[k] = days[k]; });
    return out;
  }

  /* ---------- 口径（注册表） ---------- */

  /** 额度口径：per / 单日上限 / perAward 都从 sources.json 读；拉不到就用兜底值。 */
  function loadRegistry() {
    return fetch(REGISTRY_PATH, { cache: 'no-cache' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => {
        const src = (d && d.sources && d.sources[SOURCE_ID]) || null;
        if (!src) throw new Error('注册表里没有 ' + SOURCE_ID + ' 源');
        const per = Number(src.per);
        if (!(per >= 1)) throw new Error('per 不对：' + src.per);
        const cap = Number(src.maxPerDay);
        const perAward = Number(d.perAward);
        return {
          per: per,
          cap: (cap >= 0) ? cap : DEFAULT_MAX_PER_DAY,
          perAward: (perAward >= 1) ? perAward : DEFAULT_PER_AWARD,
          // `enabled` 不是 true ⇒ 服务端 `parse_report` 会**整份拒收**这个源的 push 上报
          // （「源 words 当前是停用的」）⇒ 页面不许再假装交上去就能算数。
          enabled: src.enabled === true,
        };
      })
      .catch(err => {
        // 失败原因**不要整个吞掉**：线上注册表挂了会静默退回 100，事后无从查。
        console.warn('[words] 读注册表失败，退回默认口径（' + DEFAULT_PER +
          ' 个词 = 1 个额度）：', err);
        // 拉不到注册表 ⇒ 不知道源有没有被停用 ⇒ 照旧让她记（兜底口径生效）。
        return { per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, perAward: DEFAULT_PER_AWARD, enabled: true };
      });
  }

  /* ---------- 渲染 ---------- */

  /** 当前选中的那天的**有效**个数（夹过单日上限的）。 */
  function effectiveOf(day) {
    return clampDays({ [day]: state.days[day] || 0 }, state.cap).days[day];
  }

  /** 要提交的那些天：夹过上限、有量的（零不报）。 */
  function reportDays() {
    const clamped = clampDays(state.days, state.cap);
    const days = {};
    Object.keys(clamped.days).sort().reverse().forEach(d => {
      if (clamped.days[d] > 0) days[d] = clamped.days[d];
    });
    return { days: days, clamped: clamped.clamped };
  }

  /** 正在编辑的那天的标题：今天是「今天」，别的天是「11-21（周四）」。 */
  function selectedLabel() {
    const k = state.selected;
    if (k === todayKey()) return '今天';
    return shortLabel(k) + '（周' + weekdayOf(k) + '）';
  }

  function renderDay() {
    const input = $('#countInput');
    if (input) input.value = state.days[state.selected] ? String(state.days[state.selected]) : '';
    $('#dayTitle').textContent = selectedLabel() + '背了多少个词';
    renderMetrics();
  }

  /** 额度、说明、夹取提示（**不动输入框** —— 免得跟正在输入的数字打架）。 */
  function renderMetrics() {
    const raw = Number(state.days[state.selected]) || 0;
    const eff = effectiveOf(state.selected);
    const credit = creditsForDay(eff, state.per, state.cap);
    const line = $('#creditLine');
    if (eff > 0) {
      let text = selectedLabel() + ' ' + eff + ' 个 → 折算 ' + credit + ' 个额度';
      // 被夹过上限时把**原值**也写出来 —— 输入框里是原值，
      // 光看额度行（夹后的数）会以为系统记错了。
      if (raw > eff) text += '（原始 ' + raw + '）';
      line.textContent = text;
      line.classList.remove('empty');
    } else {
      // 零的时候给一句人话，不是一个孤零零的 0
      line.textContent = state.selected === todayKey() ? '今天还没记' : '这天还没记';
      line.classList.add('empty');
    }
    $('#perNote').textContent = state.per + ' 个 = 1 个额度（按天算，零头不计）';
    $('#awardNote').textContent = '攒满 ' + state.perAward + ' 个额度掉 1 只宝可梦（当天最多 1 只）';

    const { clamped } = reportDays();
    const note = $('#clampNote');
    if (!clamped.length) {
      note.hidden = true;
      note.textContent = '';
    } else {
      note.hidden = false;
      note.textContent = '⚠️ ' + clamped.map(c =>
        shortLabel(c.day) + ' 那天记了 ' + c.raw + ' 个，超过单日上限 ' + state.cap +
        ' 个 —— 提交时按 ' + c.capped + ' 个报（服务端会把超上限的报告整份丢掉）。'
      ).join(' ');
    }

    // 源被停用 ⇒ 说清楚（服务端不收这一类上报，页面不能让她白交）
    const dis = $('#disabledNote');
    if (state.enabled) {
      dis.hidden = true;
      dis.textContent = '';
    } else {
      dis.hidden = false;
      dis.textContent = '⚠️ 注册表里备考源不是启用状态（enabled 不为 true）—— ' +
        '服务端不收这一类上报，现在交上去也不会算数。';
    }

    // 链接参数带来的提示（`?words=` 预填 / `?day=` 太老）
    const link = $('#linkNote');
    if (state.linkNote) {
      link.hidden = false;
      link.textContent = state.linkNote;
    } else {
      link.hidden = true;
      link.textContent = '';
    }

    const store = $('#storeNote');
    if (state.storageOk) {
      store.hidden = true;
      store.textContent = '';
    } else {
      store.hidden = false;
      store.textContent = '⚠️ 这个浏览器不让存东西（隐私模式？）—— 数字能用，但刷新就没了。';
    }
  }

  /** 最近 7 天：一天一个小卡片（点一下补录）。 */
  function renderWeek() {
    const list = $('#dayList');
    list.innerHTML = '';
    const today = todayKey();
    lastDays(WINDOW_DAYS).forEach(k => {
      const eff = effectiveOf(k);
      const credit = creditsForDay(eff, state.per, state.cap);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dcell' + (k === today ? ' today' : '') + (k === state.selected ? ' on' : '');
      btn.dataset.day = k;
      btn.title = k + '：' + (eff > 0 ? eff + ' 个（' + credit + ' 个额度）' : '没记');
      btn.innerHTML = '<span class="w"></span><span class="d"></span><span class="n"></span>' +
        '<span class="c"></span>';
      btn.querySelector('.w').textContent = weekdayOf(k);
      btn.querySelector('.d').textContent = shortLabel(k);
      btn.querySelector('.n').textContent = eff > 0 ? String(eff) : '–';
      btn.querySelector('.c').textContent = eff > 0 ? credit + ' 额度' : '没记';
      btn.onclick = () => {
        state.selected = k;
        renderDay();
        renderWeek();
      };
      list.appendChild(btn);
    });

    // 窗口内的合计：**按天 floor 再相加**（不跨天凑零头）
    const win = {};
    lastDays(WINDOW_DAYS).forEach(k => { win[k] = effectiveOf(k); });
    const total = Object.keys(win).reduce((s, k) => s + win[k], 0);
    $('#weekTotal').textContent = total + ' 个';
    $('#weekCredit').textContent = creditsForDays(win, state.per, state.cap) + ' 个额度';
  }

  /** 提交按钮：预填好的 GitHub 新建 issue 链接（没有量时禁用）。 */
  function renderSubmit() {
    const { days } = reportDays();
    const btn = $('#submitBtn');
    const has = Object.keys(days).length > 0;
    if (has && state.enabled) {
      btn.href = buildIssueUrl(days);
      btn.classList.remove('off');
      btn.removeAttribute('aria-disabled');
      $('#submitHint').innerHTML = '点开是 GitHub 的新建 issue 页，标题和内容都填好了 —— ' +
        '在那个页面上点一下 <b>Submit</b> 才算交上去。';
    } else if (!state.enabled) {
      // 源被停用 ⇒ 服务端会整份拒收 ⇒ 页面不许给她一个「看起来能交」的按钮
      btn.href = '#';
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
      $('#submitHint').innerHTML = '备考源现在被停用了（注册表里 <code>enabled</code> 不是 true）—— ' +
        '交上去服务端也不会收这一类上报。';
    } else {
      btn.href = '#';
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
      $('#submitHint').innerHTML = '还没有记下任何词 —— 先在上面填个数。';
    }
    $('#issueTitle').textContent = has ? buildIssueTitle(days) : '[report] words …';

    // 报告里可能有**「最近 7 天」之外**的日子（存了 30 天，显示 7 天）——
    // 那些天在页面上看不见，她却会一起交上去 ⇒ 必须说清楚。
    const win = new Set(lastDays(WINDOW_DAYS));
    const outside = Object.keys(days).filter(d => !win.has(d)).sort();
    const extra = $('#extraDaysNote');
    if (!outside.length) {
      extra.hidden = true;
      extra.textContent = '';
    } else {
      extra.hidden = false;
      extra.textContent = '⚠️ 这份报告里有 ' + outside.length + ' 天不在「最近 7 天」里（' +
        outside.map(shortLabel).join('、') + '）—— 它们也会一起交上去。';
    }
  }

  function render() {
    renderDay();
    renderWeek();
    renderSubmit();
  }

  /* ---------- 交互 ---------- */

  /** 把输入框里的数字写进状态（空 = 0，非法 = 0）。 */
  function readInput() {
    const input = $('#countInput');
    const v = Number(input.value);
    const n = Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
    if (n > 0) state.days[state.selected] = n;
    else delete state.days[state.selected];
    state.days = trimDays(state.days);
    saveDays();
  }

  /** 快捷按钮：+10 / +20 / +50 / 清零。 */
  function bump(delta) {
    const cur = state.days[state.selected] || 0;
    const next = delta === 0 ? 0 : Math.max(0, cur + delta);
    if (next > 0) state.days[state.selected] = next;
    else delete state.days[state.selected];
    state.days = trimDays(state.days);
    saveDays();
    render();
  }

  function bind() {
    const input = $('#countInput');
    input.addEventListener('input', () => {
      readInput();
      // 不重画输入框（免得正在输入的数字被抹掉），只更新其余部分
      renderMetrics();
      renderWeek();
      renderSubmit();
    });
    input.addEventListener('blur', readInput);
    $$('#quick button').forEach(b => {
      b.onclick = () => bump(Number(b.dataset.add || 0));
    });
    const btn = $('#submitBtn');
    btn.addEventListener('click', e => {
      if (btn.getAttribute('aria-disabled') === 'true') {
        e.preventDefault();
        return;
      }
    });
  }

  /* ---------- 启动 ---------- */

  /** 读链接上的参数（`?day=2026-11-21` / `?words=150`）—— 没有参数也照常跑。 */
  function readParams() {
    let q = '';
    try {
      q = new URLSearchParams(window.location.search).toString();
    } catch (e) {
      q = '';
    }
    const out = {};
    q.split('&').forEach(pair => {
      if (!pair) return;
      const i = pair.indexOf('=');
      const k = decodeURIComponent(i < 0 ? pair : pair.slice(0, i));
      const v = decodeURIComponent(i < 0 ? '' : pair.slice(i + 1).replace(/\+/g, ' '));
      out[k] = v;
    });
    return out;
  }

  /** 往链接提示上追加一句（多句时用空格隔开）。 */
  function addLinkNote(text) {
    state.linkNote = state.linkNote ? (state.linkNote + ' ' + text) : text;
  }

  function boot() {
    state.days = loadDays();
    const params = readParams();
    if (isKey(params.day)) {
      // 只能补记**存储窗口**（最近 MAX_REPORT_DAYS 天）里的日子 ——
      // 更早的天记下来也会被 trimDays 丢掉（她以为记上了，其实没有）。
      const keep = new Set(lastDays(MAX_REPORT_DAYS));
      if (keep.has(params.day)) {
        state.selected = params.day;
      } else {
        addLinkNote('那一天太久了（只能补记最近 ' + MAX_REPORT_DAYS + ' 天）—— 落在今天了。');
      }
    }
    // `?words=150` —— 给以后「自动导入」那一层留的口子（手动记数时用不到）。
    // **不许悄悄覆盖**她已经记好的数：当天已经有记录就只提示，不动手。
    if (params.words !== undefined && Number.isFinite(Number(params.words))) {
      const n = Math.max(0, Math.floor(Number(params.words)));
      if (n > 0) {
        const already = Number(state.days[state.selected]) || 0;
        if (already > 0) {
          addLinkNote('这一天已经记了 ' + already + ' 个 —— 链接里的 ' + n + ' 没有覆盖它。');
        } else {
          state.days[state.selected] = n;
          addLinkNote('已按链接预填 ' + n + ' 个（可以改）。');
        }
      }
    }
    state.days = trimDays(state.days);
    saveDays();
    bind();
    render();

    // 注册表是唯一的额度口径来源；**拉不到就用兜底值**（绝不让页面卡在「载入中…」）
    loadRegistry().then(reg => {
      state.per = reg.per;
      state.cap = reg.cap;
      state.perAward = reg.perAward;
      state.enabled = reg.enabled;
      render();
      // 注册表（真口径或兜底口径）**已经生效** ⇒ 这一行是「可以断言了」的信号。
      state.registryLoaded = true;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
