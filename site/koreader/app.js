/* ============================================================
   KOReader 阅读时长落地页
   —— 手机扫二维码落到的那一页：
        https://lilia-shound.github.io/pokemon-dex/koreader/?days=2026-11-21:25,2026-11-20:50
      它只做三件事：
        1. 把短链里的 days= 展开成「哪天读了多久」
        2. 折算成几个额度（per 从 sources.json 读；拉不到退回 15）
        3. **点「交上去」直接交**（免登录投递通道，不用登录 GitHub）；
           交不上去的先存在本机（pendingIngest.koreader），下次打开自动补发；
           通道没开时**退回老办法**（打开预填好的 GitHub 新建 issue 页，人点 Submit）。
   —— 这一页**只报数**：抽卡权只在服务端，它自己不动图鉴。

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

  /* ---------- 免登录投递通道 · 常量 ---------- */
  /**
   * 待发队列的 localStorage 键 —— **必须按源分开**。
   *
   * 三个子页（番茄钟 / 背单词 / KOReader）**同源 ⇒ 共用同一份 localStorage**。
   * 若本页也用 'pendingIngest'（番茄钟页那个键），两个页面的条目会混在一个数组里 ⇒
   * 队列超 50 条时 slice(-50) 会**静默丢掉别页最老的那条报告**。
   * ⇒ 本页用 'pendingIngest.koreader'；番茄钟页是 'pendingIngest'，背单词页是 'pendingIngest.words'。
   */
  const PENDING_KEY = 'pendingIngest.koreader';
  /** 单次投递的超时（毫秒） */
  const INGEST_TIMEOUT_MS = 15000;
  /**
   * 投递进行中（连点两次「交上去」时用它挡住第二次）。
   *
   * workers.dev 在国内慢，15 秒超时窗口里很容易再点一下 ⇒ 两个 POST 同时在飞；
   * 两个都失败的话，同一份报告会入队两次，白占 50 条上限、还把别的报告挤掉。
   */
  let INGESTING = false;

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
   * 与 Lua 一致（Python 侧现在也用 fullmatch，三端一样紧 —— 见 tests/test_koreader_lua.py）。
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
   * 把每天的分钟数**夹到单日上限**。
   *
   * 为什么要在落地页夹：服务端 `scripts/collect_reports.py` 的规则是
   * 「任一天超过该源的 maxPerDay ⇒ **整份报告丢弃**」——
   * 而插件的 collect_days 不夹单日分钟数 ⇒ 理论上能产出服务端拒收的报告，
   * 而页面只字不提 ⇒ 用户会以为交上去了。所以在编 body 时先夹住，
   * 夹过的那些天要在页面上说清楚（见 renderClampNote）。
   *
   * **夹取的口径要与 creditsForDay 一致**（两道夹）：
   *   1. 不是数的（NaN / undefined）⇒ 当 0；
   *   2. 超上限 ⇒ 夹到上限；负数 ⇒ 夹到 0。
   * 两边口径不一致 ⇒ 页面显示的分钟数、额度、提交的值会对不上。
   *
   * @param {Object<string, number>} days
   * @param {number} cap 单日上限（分钟）
   * @returns {{days: Object<string, number>, clamped: Array<{day: string, raw: number, capped: number}>}}
   */
  function clampDays(days, cap) {
    const limit = Number(cap) >= 0 ? Number(cap) : DEFAULT_MAX_PER_DAY;
    const out = {};
    const clamped = [];
    Object.keys(days || {}).forEach(d => {
      const raw = Number(days[d]);
      let amount = Number.isFinite(raw) ? raw : 0;   // 不是数的当 0（与 creditsForDay 一样）
      if (amount > limit) amount = limit;            // 超上限 ⇒ 夹到上限
      if (amount < 0) amount = 0;                    // 负数 ⇒ 夹到 0
      out[d] = amount;
      // 只有「超上限被夹」才提示（那是用户该知道、也该改的事）；
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

  /* ---------- 免登录投递通道（纯逻辑：浏览器与 node 都跑得起来） ---------- */

  /**
   * 页面侧的两个口子 —— node 探针里可以替换。
   *
   * 这一页是 IIFE，浏览器里才有 DOM；而投递这套逻辑**必须能真跑一遍**才算测过
   * （只做子串匹配等于没测）⇒ 把「写提示」和「重画页面」收成两个可替换的函数：
   * 页面里是默认实现，node 里换成收集器 / 空函数。
   */
  const ui = {
    /** 写一行**纯文本**提示（结果提示用这个 —— 服务器来的错误消息不能当 HTML 插） */
    hint: function (text) {
      if (typeof document === 'undefined') return;
      const el = document.querySelector('#submitHint');
      if (!el) return;
      const msg = String(text == null ? '' : text);
      // ★ 内容没变就别重写 DOM —— 重写会把内容换掉，
      //   鼠标按下到松开那一下就点空了。
      if (el.textContent !== msg) el.textContent = msg;
    },
    /** 状态变了要重画（页面里是 renderDays） */
    render: function () {
      if (typeof document === 'undefined') return;
      renderDays();
    },
  };

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
      resp = await fetch(state.ingest.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dex-Token': state.ingest.token || '' },
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
    return !!(state.ingest && state.ingest.enabled === true && state.ingest.url);
  }

  /**
   * 写一条**结果提示**（已记录 / 交不上去 / 补发了 N 条）。
   *
   * 结果提示记在 state.lastSubmit 里：页面重画时优先显示它，
   * 她换了数（手动补一天）就把它清掉 ⇒ 状态说明回到前台。
   */
  function setResult(text) {
    state.lastSubmit = { msg: String(text == null ? '' : text) };
    ui.hint(state.lastSubmit.msg);
  }

  /**
   * 结果提示作废（状态说明回到前台），**并把整页重画一遍**。
   *
   * ★ 必须重画，不能只清 state：只清 state 的话，页面上那句「已记录 ✓」
   *   会一直挂到下一次重画为止 —— 她补录了别的天数之后，看到的还是
   *   「已记录 ✓」，而那条提示其实已经过时了。
   *
   * ★ 调用方**不用**再 renderDays() —— 这里已经重画过了。
   */
  function clearResult() {
    state.lastSubmit = null;
    ui.render();
  }

  /**
   * 「现在是什么状态、怎么交」—— **通道开着与关着说法不同**。
   *
   * 这是两条完全不同的路（直投 vs 老办法）⇒ 她看到的话必须不一样，
   * 否则她不知道到底交没交上去。
   */
  function statusHint() {
    if (!Object.keys(state.reportDays || {}).length) {
      return '还没读到时长 —— 重新扫一次带天数的链接，或在下面手动补一天。';
    }
    return ingestAvailable()
      ? '点「交上去」直接交（不用登录 GitHub）。'
      : '点「交上去」会打开 GitHub 的新建 issue 页，在那个页面上点一下 Submit 才算交上去。';
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
    if (sent) setResult('补发了 ' + sent + ' 条');
  }

  /**
   * 提交：点一下就交上去。
   *
   * 三条路：
   *   1. 通道开着 ⇒ 直接 POST 上去；交不上就**存进待发队列**，下次打开补发。
   *   2. 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
   *      打开 GitHub 的新建 issue 页（少了这个分支，部署窗口里她点提交只会报错）。
   *   3. 连点两次 ⇒ 只发一个 POST（见 INGESTING）。
   */
  async function doSubmit() {
    // ★ 连点两次「交上去」⇒ 只发一个 POST。
    //   workers.dev 在国内慢，15 秒超时窗口里很容易再点一下 ⇒ 两个 POST 同时在飞；
    //   两个都失败的话，同一份报告会入队两次，白占 50 条上限、还把别的报告挤掉。
    if (INGESTING) { setResult('正在交上去，稍等一下'); return; }
    // ★ 报告要发**夹过单日上限**的那些天（用 state.reportDays，别用 state.days）
    const days = state.reportDays || {};
    if (!Object.keys(days).length) {
      setResult('还没有读到时长 —— 先扫一次带天数的链接。');
      return;
    }
    // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
    //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点提交只会报错。
    if (!ingestAvailable()) {
      window.open(state.issueUrl || '#', '_blank', 'noopener');
      setResult('已打开提交页 —— 在 GitHub 那个页面上点一下 Submit');
      return;
    }
    const report = buildReport(days);
    INGESTING = true;
    try {
      await postIngest(report);
      setResult('已记录 ✓');
    } catch (err) {
      enqueueReport(report);
      setResult('暂时交不上去（' + (err && err.message ? err.message : '未知原因') +
        '）—— 已存着，下次打开自动补发。');
    } finally {
      // ★ 成功、失败、异常都要清 —— 不清的话她再也点不动「交上去」了。
      INGESTING = false;
    }
  }

  /* ---------- 页面状态 ---------- */

  /** 页面当前状态：解出来的天数、额度口径、算出来的额度、issue 链接。
   *
   *  - `days`       —— 短链里解出来的**原始**分钟数（一天一个键）
   *  - `reportDays` —— **夹过单日上限**的分钟数（真正提交给服务端的就是它）
   *  - `clamped`    —— 被夹过的那些天（{day, raw, capped}）—— 页面上要提示
   *  - `registryLoaded` —— **注册表已经生效**（读到真注册表了，或者兜底口径生效了）
   *  - `ingest`     —— 免登录通道的配置（从 sources.json **顶层**读；没有就是 null ⇒ 走老办法）
   *  - `lastSubmit` —— 提交结果提示（{msg}）：有它就别让「当前状态」的说明盖掉它
   *
   *  `registryLoaded` 为什么要有：页面**第一次**渲染用的是默认口径（per=15），
   *  注册表是之后异步换上去的 ⇒ 「画完了」**不等于**「注册表生效了」。
   *  测试要证明「per 来自注册表」，等的必须是这个标志，不是「页面画完了」。
   */
  const state = {
    days: {}, reportDays: {}, clamped: [],
    per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY,
    credits: 0, issueTitle: '', issueUrl: '',
    registryLoaded: false,
    ingest: null,      // 免登录通道的配置（从 sources.json 顶层读）
    lastSubmit: null,  // 提交结果提示（{msg}）
  };

  const API = {
    parseDays, encodeDays, creditsForDay, creditsForDays,
    latestDay, buildReport, buildIssueTitle, buildIssueUrl, clampDays,
    DEFAULT_PER, DEFAULT_MAX_PER_DAY, SOURCE_ID, ISSUE_NEW, REPO, CDN,
    /* 免登录投递通道 —— 一并挂出来，node 探针要能真跑这几段 */
    PENDING_KEY, INGEST_TIMEOUT_MS,
    pendingQueue, savePending, enqueueReport, shiftPending,
    postIngest, ingestAvailable, flushPending, doSubmit, statusHint,
    isIngesting: function () { return INGESTING; },
    loadRegistry,
    ui, state,
  };

  /* node 侧（tests/koreader_landing_spec.js）要 require 这个文件做对拍；
     浏览器侧则把同一份 API 挂到 window 上，方便浏览器测试直接问。 */
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  if (typeof globalThis !== 'undefined') globalThis.KoreaderLanding = API;

  /* ---------- 页面部分（只在浏览器里跑） ---------- */
  if (typeof document === 'undefined') return;

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
        // 免登录通道的配置 —— **同一份 sources.json 的顶层**，不再多发一个请求
        state.ingest = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
        return { per: per, cap: (cap >= 0) ? cap : DEFAULT_MAX_PER_DAY };
      })
      .catch(err => {
        // 失败原因**不要整个吞掉**：线上注册表挂了会静默退回 15，事后无从查。
        // 打个 warn 就能在浏览器控制台里看到「为什么退回了默认口径」。
        console.warn('[koreader] 读注册表失败，退回默认口径（' + DEFAULT_PER +
          ' 分钟 = 1 个额度）：', err);
        // 拉不到注册表 ⇒ 不知道通道开没开 ⇒ **退回老办法**（老办法不需要配置）
        state.ingest = null;
        return { per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY };
      });
  }

  /** 渲染有效天数的那一屏 */
  function renderDays() {
    // 显示的是**要提交给服务端的那份**（夹过单日上限的）——
    // 页面写 1440、报 1440、额度也按 1440 算，三者永远一致。
    const keys = Object.keys(state.reportDays).sort().reverse();   // 日期倒序：最新的一天在前
    const list = $('#dayList');
    // ★ 内容没变就别重写 DOM —— 重画时把行换掉，鼠标按下到松开那一下会点空。
    //   （口径变了 ⇒ 额度变了 ⇒ 内容其实变了 ⇒ 该重写。）
    const rows = keys.map(d => [d, state.reportDays[d],
      creditsForDay(state.reportDays[d], state.per, state.cap)]);
    const rowKey = state.per + '/' + state.cap + '|' + JSON.stringify(rows);
    if (list.dataset.rows !== rowKey) {
      list.innerHTML = '';
      rows.forEach(r => {
        const li = document.createElement('li');
        li.innerHTML = '<span class="d"></span><span class="m"></span><span class="c"></span>';
        li.querySelector('.d').textContent = r[0];
        li.querySelector('.m').textContent = r[1] + ' 分钟';
        li.querySelector('.c').textContent = r[2] + ' 个额度';
        list.appendChild(li);
      });
      list.dataset.rows = rowKey;
    }
    let total = 0;
    keys.forEach(d => { total += state.reportDays[d]; });
    $('#totalMin').textContent = total + ' 分钟';
    $('#creditNum').textContent = state.credits;
    $('#creditCount').textContent = state.credits;
    $('#perNote').textContent = state.per + ' 分钟 = 1 个额度（按天算，零头不计）';

    // 提交按钮：没有可交的天数时禁用（<button> 的原生禁用 + aria-disabled）
    const has = keys.length > 0;
    const submit = $('#submitBtn');
    if (submit) {
      if (has) {
        submit.removeAttribute('aria-disabled');
        submit.disabled = false;
      } else {
        submit.setAttribute('aria-disabled', 'true');
        submit.disabled = true;
      }
    }
    // 老办法的链接（**永远留着** —— workers.dev 在国内可能抽风，这是兜底）
    const old = $('#oldWayBtn');
    if (old) old.href = state.issueUrl || '#';
    // 提示行：**结果提示优先**，没有才说「现在是什么状态、怎么交」
    const hint = $('#submitHint');
    if (hint) {
      const msg = state.lastSubmit ? state.lastSubmit.msg : statusHint();
      // ★ 内容没变就别重写 DOM —— 重写会把那句话的内容换掉。
      if (hint.textContent !== msg) hint.textContent = msg;
    }
    const titleEl = $('#issueTitle');
    if (titleEl) titleEl.textContent = state.issueTitle;
    renderClampNote();
  }

  /** 单日超上限时的提示（有夹取才显示，没有就藏起来） */
  function renderClampNote() {
    const note = $('#clampNote');
    if (!note) return;
    if (!state.clamped.length) {
      note.hidden = true;
      note.textContent = '';
      return;
    }
    note.hidden = false;
    note.textContent = '⚠️ ' + state.clamped.map(c =>
      c.day + ' 那天读了 ' + c.raw + ' 分钟，超过单日上限 ' + state.cap +
      ' 分钟 —— 提交时按 ' + c.capped + ' 分钟报（服务端会把超上限的报告整份丢掉）。'
    ).join(' ');
  }

  /* ---------- 补录：手动加一天 ---------- */

  /**
   * 手动加一天（短链里没带的那天，或者补前天忘了报的那天）。
   *
   * ★ 手动加的天**也要过单日上限** —— `recompute()` 里的
   *   `clampDays(state.days, cap)` 会把它夹住（不然能被刷额度）。
   * ★ 之前是「没读出有效时长」那一屏（mainBox 藏着）的话，加完要把主区放出来。
   */
  function addManualDay() {
    var dateEl = document.getElementById('manualDate');
    var minEl = document.getElementById('manualMinutes');
    var hint = document.getElementById('manualHint');
    var day = ((dateEl && dateEl.value) || '').trim();
    var minutes = parseInt((minEl && minEl.value) || '', 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
      if (hint) hint.textContent = '先选一个日期。';
      return false;
    }
    if (!(minutes >= 0) || isNaN(minutes)) {
      if (hint) hint.textContent = '填一下那天的分钟数（没有就读 0）。';
      return false;
    }
    state.days[day] = minutes;
    if (hint) hint.textContent = '记下了 ' + day + ' —— ' + minutes + ' 分钟。';
    if (minEl) minEl.value = '';
    // 之前是「没读出有效时长」那一屏 ⇒ 现在有数据了，把主区放出来
    if (!Object.keys(state.reportDays || {}).length) {
      state.per = DEFAULT_PER;
      state.cap = DEFAULT_MAX_PER_DAY;
      var bad = document.getElementById('badBox');
      var main = document.getElementById('mainBox');
      if (bad) bad.hidden = true;
      if (main) main.hidden = false;
    }
    recompute();
    // ★ 补了一天 ⇒ 之前那句「已记录 ✓」作废（数已经变了）⇒ 整页重画。
    //   clearResult() 内部会重画（它把结果提示清掉，再把状态说明放回前台）。
    clearResult();
    // 注册表还没到的时候，等真口径到了再算一遍
    if (!state.registryLoaded) {
      loadRegistry().then(function (reg) {
        state.per = reg.per;
        state.cap = reg.cap;
        recompute();
        renderDays();
        state.registryLoaded = true;
      });
    }
    return true;
  }

  /** 绑定手动补记的表单。 */
  function bindManualDay() {
    var btn = document.getElementById('manualAdd');
    if (btn) btn.addEventListener('click', addManualDay);
  }

  /**
   * 绑定「交上去」按钮。
   *
   * ★ 调用处必须挂 .catch —— doSubmit 是异步的，漏了会在控制台留一条
   *   "Unhandled promise rejection"，页面看着像坏了（浏览器的 console 错误）。
   *   （doSubmit 内部已经把「交不上去」兜成提示了，这里只是最后一道保险。）
   */
  function bindSubmit() {
    var btn = document.getElementById('submitBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (btn.getAttribute('aria-disabled') === 'true') return;
      doSubmit().catch(function () { /* 出不了什么岔子，别让控制台红 */ });
    });
  }

  /** 渲染「没读出有效时长」那一屏 */
  function renderBad() {
    $('#badBox').hidden = false;
    $('#mainBox').hidden = true;
  }

  /** 按当前口径（per / cap）重算一遍：夹上限后的天数、额度、issue 链接 */
  function recompute() {
    const clamped = clampDays(state.days, state.cap);
    state.reportDays = clamped.days;      // 真正提交给服务端的是它
    state.clamped = clamped.clamped;       // 被夹过的天（页面上要提示）
    // 额度按**夹后**的分钟数算（与提交给服务端的那份完全一致）——
    // 不要用 state.days（原始值）：那样「显示的分钟数 / 额度 / 提交的值」会各说各话。
    state.credits = creditsForDays(state.reportDays, state.per, state.cap);
    // 标题只用**最新的一天**（days 与 reportDays 的键是同一批，取谁都一样）⇒ 跟着 reportDays 走。
    state.issueTitle = buildIssueTitle(state.reportDays);
    state.issueUrl = buildIssueUrl(state.reportDays);
  }

  /** 主流程：解线 → 算额度 → 渲染；然后去拉注册表（拉到了就按真口径重算） */
  function boot() {
    bindManualDay();
    bindSubmit();
    state.days = parseDays(readWire());
    const keys = Object.keys(state.days);
    if (!keys.length) {
      // 一条都没解出来 ⇒ 友好提示，不是空白页、也不是一个空的 0
      renderBad();
      return;
    }
    state.per = DEFAULT_PER;
    state.cap = DEFAULT_MAX_PER_DAY;
    recompute();
    $('#badBox').hidden = true;
    $('#mainBox').hidden = false;
    renderDays();

    // 注册表是唯一的额度口径来源；**拉不到就用默认值 15**（绝不让页面卡在「载入中…」）
    loadRegistry().then(reg => {
      state.per = reg.per;
      state.cap = reg.cap;
      recompute();
      renderDays();
      // 注册表（真口径或兜底口径）**已经生效** ⇒ 这一行是「可以断言了」的信号。
      // 放在 renderDays 之后：等到这个标志为 true 时，页面上的数字一定已经是新口径的了。
      state.registryLoaded = true;
      // ★ 上次没交上去的补发掉（注册表到了 ⇒ 知道通道开没开）。
      //   这里必须挂 .catch —— flushPending 是异步的，漏了会在控制台留一条
      //   "Unhandled promise rejection"，页面看着像坏了。
      flushPending().catch(function () { /* 补发失败就下次再说 */ });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
