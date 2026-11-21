/* ============================================================
   KOReader 阅读时长落地页
   —— 手机扫二维码落到的那一页：
        https://lilia-shound.github.io/pokemon-dex/koreader/?days=2026-11-21:25,2026-11-20:50
      它只做三件事：
        1. 把短链里的 days= 展开成「哪天读了多久」
        2. 折算成几个额度（per 从 sources.json 读；拉不到退回 15）
        3. 给一个按钮，跳到**预填好的** GitHub 新建 issue 页
   **这一页不发任何写请求** —— 抽卡权只在服务端，这里只报数。

   线格式（`YYYY-MM-DD:分钟`，逗号分隔，日期倒序）的解析规则是**定死的**，
   与 Lua 的 `Report.decode_days`、Python 的 `parse_days` 一一对应：
     - 一段必须**完全**匹配才收；不完全匹配就**整段丢掉**（不 trim、不做别的容错）
     - 数字只认 ASCII 0-9
     - 解析不出来任何一天 ⇒ 页面显示友好提示，不显示一个空的 0
   ============================================================ */
'use strict';

(function () {

  /* ---------- 中枢接线 ---------- */
  const CDN = 'https://lilia-shound.github.io/pokemon-dex/';
  const REPO = 'lilia-shound/pokemon-dex';
  const SOURCE_ID = 'koreader';
  const ISSUE_NEW = 'https://github.com/' + REPO + '/issues/new';
  /** 拉不到 sources.json 时的兜底口径（与服务端注册表一致：15 分钟 = 1 个额度） */
  const DEFAULT_PER = 15;
  /** 单日上限（分钟）—— 服务端 credit_for_day 会先夹到它再折算 */
  const DEFAULT_MAX_PER_DAY = 1440;

  /* ---------- 线格式 ---------- */
  /**
   * 一天里每一段的样子：YYYY-MM-DD:分钟
   *
   * **数字只认 ASCII 0-9** —— 所以用 [0-9] 而不是 \d
   * （JS 的 \d 本来也就是 ASCII-only，这里写出来是让规则一眼可见；
   *  Lua 的 %d 只认 ASCII，Python 的 \d 会认 Unicode 数字 —— 那边才必须用 [0-9]）。
   *
   * JS 的 `$`（没有 /m flag）只匹配字符串**末尾** ——
   * 所以 "2026-11-21:25\n" 这种带尾换行的段在这里**不匹配**（整段丢掉），
   * 与 Lua 一致（Python 的 `$` 会放过尾换行，那是三端里唯一偏松的一处）。
   */
  const WIRE_CHUNK = /^([0-9]{4}-[0-9]{2}-[0-9]{2}):([0-9]+)$/;

  /* ---------- 纯逻辑（浏览器与 node 都跑得起来） ---------- */

  /**
   * 把天数串解成 {日期: 分钟}。
   * @param {string} wire 形如 "2026-11-21:25,2026-11-20:50"
   * @returns {Object<string, number>}
   */
  function parseDays(wire) {
    const out = {};
    const text = (wire === null || wire === undefined) ? '' : String(wire);
    if (!text) return out;
    for (const chunk of text.split(',')) {
      const m = WIRE_CHUNK.exec(chunk);
      if (m) out[m[1]] = parseInt(m[2], 10);   // "007" ⇒ 7（与 Lua/Python 一致）
    }
    return out;
  }

  /**
   * 把 {日期: 分钟} 编回天数串（日期倒序，逗号分隔）—— Lua 的 Report.encode_days。
   * @param {Object<string, number>} days
   * @returns {string}
   */
  function encodeDays(days) {
    return Object.keys(days || {}).sort().reverse()
      .map(d => d + ':' + days[d]).join(',');
  }

  /**
   * 一天折算多少额度：**按天**向下取整（不跨天凑零头）。
   * 与服务端 scripts/sources.py 的 credit_for_day 口径一致：
   * 先夹到单日上限、再夹到 0，最后 // per。
   * @param {number} minutes
   * @param {number} per 多少分钟 = 1 个额度
   * @param {number} cap 单日上限（分钟）
   * @returns {number}
   */
  function creditsForDay(minutes, per, cap) {
    let amount = Number(minutes);
    if (!Number.isFinite(amount)) amount = 0;
    if (amount > cap) amount = cap;
    if (amount < 0) amount = 0;
    return Math.floor(amount / per);
  }

  /**
   * 这些分钟一共折算多少个额度：**按天 floor 再相加**。
   * 例：25 分钟 + 50 分钟、per=15 ⇒ 1 + 3 = 4（不是 floor(75/15) = 5）。
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
   * days 的键按**日期倒序**排（跟天数串一个顺序，读起来顺）。
   * @param {Object<string, number>} days
   * @returns {Object}
   */
  function buildReport(days) {
    const ordered = {};
    Object.keys(days || {}).sort().reverse().forEach(d => { ordered[d] = days[d]; });
    return { v: 1, source: SOURCE_ID, days: ordered };
  }

  /**
   * issue 的标题：`[report] koreader <最新的一天>`。
   * @param {Object<string, number>} days
   * @returns {string}
   */
  function buildIssueTitle(days) {
    return '[report] ' + SOURCE_ID + ' ' + latestDay(days);
  }

  /**
   * 预填好的 GitHub 新建 issue 链接（与番茄钟那套同一个格式）。
   * @param {Object<string, number>} days
   * @returns {string}
   */
  function buildIssueUrl(days) {
    return ISSUE_NEW + '?title=' + encodeURIComponent(buildIssueTitle(days)) +
      '&body=' + encodeURIComponent(JSON.stringify(buildReport(days)));
  }

  const API = {
    parseDays, encodeDays, creditsForDay, creditsForDays,
    latestDay, buildReport, buildIssueTitle, buildIssueUrl,
    DEFAULT_PER, DEFAULT_MAX_PER_DAY, SOURCE_ID, ISSUE_NEW, REPO, CDN,
  };

  /* node 侧（tests/koreader_landing_spec.js）要 require 这个文件做对拍；
     浏览器侧则把同一份 API 挂到 window 上，方便浏览器测试直接问。 */
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  if (typeof globalThis !== 'undefined') globalThis.KoreaderLanding = API;

  /* ---------- 页面部分（只在浏览器里跑） ---------- */
  if (typeof document === 'undefined') return;

  /** 页面当前状态：解出来的天数、额度口径、算出来的额度、issue 链接 */
  const state = { days: {}, per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, credits: 0, issueTitle: '', issueUrl: '' };
  API.state = state;

  const $ = s => document.querySelector(s);

  /** 从链接里取 days 参数。
   *
   * 用 URLSearchParams（表单解码）：%2B ⇒ 「+」、%20 ⇒ 空格 —— 都是**原样还原**。
   * 插件生成的那条短链里只有 ASCII 的「日期:分钟」，不会有歧义。 */
  function readWire() {
    try {
      return new URLSearchParams(window.location.search).get('days') || '';
    } catch (e) {
      return '';
    }
  }

  /** 额度口径：per 与单日上限都从 sources.json 读；拉不到就用默认的 15 分钟 */
  function loadRegistry() {
    return fetch(CDN + 'sources.json', { cache: 'no-cache' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => {
        const src = (d && d.sources && d.sources[SOURCE_ID]) || null;
        if (!src) throw new Error('注册表里没有 ' + SOURCE_ID + ' 源');
        const per = Number(src.per);
        if (!(per >= 1)) throw new Error('per 不对：' + src.per);
        const cap = Number(src.maxPerDay);
        return { per: per, cap: (cap >= 0) ? cap : DEFAULT_MAX_PER_DAY };
      })
      .catch(() => ({ per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY }));
  }

  /** 渲染有效天数的那一屏 */
  function renderDays() {
    const keys = Object.keys(state.days).sort().reverse();   // 日期倒序：最新的一天在前
    const list = $('#dayList');
    list.innerHTML = '';
    let total = 0;
    keys.forEach(d => {
      const minutes = state.days[d];
      total += minutes;
      const li = document.createElement('li');
      const c = creditsForDay(minutes, state.per, state.cap);
      li.innerHTML = '<span class="d"></span><span class="m"></span><span class="c"></span>';
      li.querySelector('.d').textContent = d;
      li.querySelector('.m').textContent = minutes + ' 分钟';
      li.querySelector('.c').textContent = c + ' 个额度';
      list.appendChild(li);
    });
    $('#totalMin').textContent = total + ' 分钟';
    $('#creditNum').textContent = state.credits;
    $('#creditCount').textContent = state.credits;
    $('#perNote').textContent = state.per + ' 分钟 = 1 个额度（按天算，零头不计）';
    $('#submitBtn').href = state.issueUrl;
    const titleEl = $('#issueTitle');
    if (titleEl) titleEl.textContent = state.issueTitle;
  }

  /** 渲染「没读出有效时长」那一屏 */
  function renderBad() {
    $('#badBox').hidden = false;
    $('#mainBox').hidden = true;
  }

  /** 主流程：解线 → 算额度 → 渲染；然后去拉注册表（拉到了就按真口径重算） */
  function boot() {
    state.days = parseDays(readWire());
    const keys = Object.keys(state.days);
    if (!keys.length) {
      // 一条都没解出来 ⇒ 友好提示，不是空白页、也不是一个空的 0
      renderBad();
      return;
    }
    state.per = DEFAULT_PER;
    state.cap = DEFAULT_MAX_PER_DAY;
    state.credits = creditsForDays(state.days, state.per, state.cap);
    state.issueTitle = buildIssueTitle(state.days);
    state.issueUrl = buildIssueUrl(state.days);
    $('#badBox').hidden = true;
    $('#mainBox').hidden = false;
    renderDays();

    // 注册表是唯一的额度口径来源；**拉不到就用默认值 15**（绝不让页面卡在「载入中…」）
    loadRegistry().then(reg => {
      state.per = reg.per;
      state.cap = reg.cap;
      state.credits = creditsForDays(state.days, state.per, state.cap);
      renderDays();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
