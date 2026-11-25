/* ============================================================
   备考（背单词）记数页
   —— 手机上打开就能记：输入今天背了多少个词 → 折算成额度
      → 点一下**直接交**给图鉴服务（免登录投递通道，不用再点 GitHub 的 Submit）。
      交不上去的先存在本机，下次打开自动补发；
      通道没开时**退回老办法**（打开预填好的 GitHub 新建 issue 页，人点 Submit）。
   —— 这一页**只报数**：抽卡权在服务端，它自己不动图鉴。
   —— 额度按**天**向下取整：floor(150/100) + floor(50/100) = 1 + 0 = 1，
      **不是** floor(200/100) = 2（零头不跨天凑）。
      与服务端 scripts/sources.py 的 credit_for_day 口径一致 ✓。
   —— 零外链：字体/图标全用系统自带；
      除投递那个 POST 外，唯一的网络请求是**同源**的 sources.json。
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

  /* ---------- 免登录投递通道 · 常量 ---------- */
  /**
   * 待发队列的 localStorage 键 —— **必须按源分开**。
   *
   * 三个子页（番茄钟 / 背单词 / KOReader）**同源 ⇒ 共用同一份 localStorage**。
   * 若本页也用 'pendingIngest'（番茄钟页那个键），两个页面的条目会混在一个数组里 ⇒
   * 队列超 50 条时 slice(-50) 会**静默丢掉别页最老的那条报告**。
   * ⇒ 本页用 'pendingIngest.words'；KOReader 页用 'pendingIngest.koreader'。
   */
  const PENDING_KEY = 'pendingIngest.words';
  /** 单次投递的超时（毫秒） */
  const INGEST_TIMEOUT_MS = 15000;
  /**
   * 投递进行中（连点两次「交上去」时用它挡住第二次）。
   *
   * workers.dev 在国内慢，15 秒超时窗口里很容易再点一下 ⇒ 两个 POST 同时在飞；
   * 两个都失败的话，同一份报告会入队两次，白占 50 条上限、还把别的报告挤掉。
   */
  let INGESTING = false;

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
   * **上一次记过的那一天**（今天之前、最近的一个有数的天）。
   *
   * 用途：打开页面时，今天还没记 ⇒ 把上次的数字**预填进输入框**，
   * 她每天的量差不多 ⇒ 多数时候点一下提交就行。
   *
   * 两个限制：
   *   - 必须是**今天之前**的（今天有记录就轮不到它）；
   *   - 必须在「最近 WINDOW_DAYS 天」里 —— 那个数要能在她看得见的那一排里核对，
   *     十天前的数拿来预填没有意义。
   *
   * @param {Object<string, number>} days
   * @param {string} today 今天（YYYY-MM-DD）
   * @param {number} [windowDays] 往回看几天（默认 WINDOW_DAYS）
   * @returns {?{day: string, value: number}} 没有就 null
   */
  function previousRecordedDay(days, today, windowDays) {
    const w = Number(windowDays) >= 1 ? Math.floor(Number(windowDays)) : WINDOW_DAYS;
    const candidates = lastDays(w).filter(k => k !== today && Number((days || {})[k]) > 0);
    if (!candidates.length) return null;
    const day = candidates[candidates.length - 1];   // lastDays 是从早到晚排的
    return { day: day, value: Math.floor(Number(days[day])) };
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
   * 现在是不是「加到主屏后独立打开」（standalone 模式）。
   *
   * 两个都查：
   *   - `matchMedia('(display-mode: standalone)')` —— 安卓 Chrome、桌面 Chrome 走这条；
   *   - `navigator.standalone` —— iOS Safari（老版本）走这条。
   *
   * ⚠️ **检测不到就当「不在 standalone」**：浏览器不支持 matchMedia、或它抛异常，
   * 一律按「不是」处理 —— 宁可多显示一行小字，也不许报错、不许整页崩。
   *
   * @returns {boolean}
   */
  function isStandalone() {
    try {
      if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
        const mq = window.matchMedia('(display-mode: standalone)');
        if (mq && mq.matches) return true;
      }
    } catch (e) {
      // 检测不到 ⇒ 当不在 standalone（见上面的说明）
    }
    try {
      if (typeof navigator !== 'undefined' && navigator.standalone) return true;
    } catch (e) {
      // 同上
    }
    return false;
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
   *  - `draft` —— **预填**：{day, value, from}（今天还没记时，把上次的数字填进输入框）
   *     ⚠️ 预填**只影响输入框的初值**：它不进 `days` ⇒ 额度/周合计仍按实际已记的天算
   *     （不然「还没提交」的日子会提前达标）。
   *  - `ingest` —— 免登录通道的配置（从 sources.json **顶层**读；没有就是 null ⇒ 走老办法）
   *  - `lastSubmit` —— 提交结果提示（{msg}）：有它就别让「当前状态」的说明盖掉它
   */
  const state = {
    days: {}, selected: todayKey(),
    per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, perAward: DEFAULT_PER_AWARD,
    registryLoaded: false, storageOk: true,
    enabled: true, linkNote: '',
    draft: null,
    ingest: null,        // 免登录通道的配置（从 sources.json 顶层读）
    lastSubmit: null,    // 提交结果提示（{msg}）
  };

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
      if (el) el.textContent = String(text == null ? '' : text);
    },
    /** 状态变了要重画（页面里是 render()） */
    render: function () {
      if (typeof document === 'undefined') return;
      render();
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
   * 这一页没有 toast() ⇒ 提示只能写进 #submitHint；
   * 而 #submitHint 也放「当前状态」的说明（没记数 / 源被停用 / 怎么交）⇒
   * 两个内容会互相盖掉。⇒ 结果提示记在 state.lastSubmit 里：
   * renderSubmit() 优先显示它，她改了数就把它清掉（状态说明回到前台）。
   */
  function setResult(text) {
    state.lastSubmit = { msg: String(text == null ? '' : text) };
    ui.hint(state.lastSubmit.msg);
  }

  /** 她改了数 ⇒ 结果提示作废（状态说明回到前台）。 */
  function clearResult() {
    if (state.lastSubmit) state.lastSubmit = null;
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
    // 她点了「交上去」⇒ 预填的数就此变成今天的记录
    if (state.draft && !(Number(state.days[state.draft.day]) > 0)) {
      const n = Math.max(0, Math.floor(Number(state.draft.value) || 0));
      if (n > 0) state.days[state.draft.day] = n;
      state.days = trimDays(state.days);
      state.draft = null;
      saveDays();
      ui.render();
    }
    // ★ 报告要发**夹过单日上限**的那些天（用 reportDays()，别用 state.days）
    const { days } = reportDays();
    if (!Object.keys(days).length) {
      setResult('还没有记下任何词 —— 先在上面填个数。');
      return;
    }
    // ★ 通道没开（还没部署、或被回滚关了）⇒ **退回老办法**：
    //   直接开 GitHub 的新建 issue 页。少了这个分支，部署窗口里她点提交只会报错。
    if (!ingestAvailable()) {
      window.open(buildIssueUrl(days), '_blank', 'noopener');
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

  const API = {
    creditsForDay, creditsForDays, clampDays, latestDay, previousRecordedDay,
    buildReport, buildIssueTitle, buildIssueUrl,
    lastDays, todayKey, shortLabel, weekdayOf, isStandalone,
    DEFAULT_PER, DEFAULT_MAX_PER_DAY, SOURCE_ID, ISSUE_NEW, REPO,
    WINDOW_DAYS, MAX_REPORT_DAYS, REGISTRY_PATH,
    /* 免登录投递通道 —— 一并挂出来，node 探针要能真跑这几段 */
    PENDING_KEY, INGEST_TIMEOUT_MS,
    pendingQueue, savePending, enqueueReport, shiftPending,
    postIngest, ingestAvailable, flushPending, doSubmit,
    isIngesting: function () { return INGESTING; },
    loadRegistry,
    ui, state,
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
        // 免登录通道的配置 —— **同一份 sources.json 的顶层**，不再多发一个请求
        state.ingest = (d && d.ingest && typeof d.ingest === 'object') ? d.ingest : null;
        // 配置到了 ⇒ 把上次没交上去的补发掉
        // ★ 这里必须挂 .catch —— flushPending 是异步的，漏了会在控制台留一条
        //   "Unhandled promise rejection"，页面看着像坏了（浏览器的 console 错误）。
        flushPending().catch(() => { /* 补发失败就下次再说 */ });
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
        // 拉不到注册表 ⇒ 不知道通道开没开 ⇒ **退回老办法**（老办法不需要配置）
        state.ingest = null;
        // 拉不到注册表 ⇒ 不知道源有没有被停用 ⇒ 照旧让她记（兜底口径生效）。
        return { per: DEFAULT_PER, cap: DEFAULT_MAX_PER_DAY, perAward: DEFAULT_PER_AWARD, enabled: true };
      });
  }

  /* ---------- 渲染 ---------- */

  /** 当前选中的那天的**有效**个数（夹过单日上限的）。 */
  function effectiveOf(day) {
    return clampDays({ [day]: state.days[day] || 0 }, state.cap).days[day];
  }

  /**
   * 要提交的那些天：夹过上限、有量的（零不报）。
   *
   * **预填的数也算在里面** —— 她点「去 GitHub 提交」就是要把今天这个数报上去
   * （不然那个「直接提交」根本交不了东西）。
   * 但它**不算进额度显示**：额度只按 `state.days`（实际已记的天）算。
   */
  function reportDays() {
    const clamped = clampDays(state.days, state.cap);
    const days = {};
    Object.keys(clamped.days).sort().reverse().forEach(d => {
      if (clamped.days[d] > 0) days[d] = clamped.days[d];
    });
    if (state.draft && !(days[state.draft.day] > 0)) {
      const capped = clampDays({ [state.draft.day]: state.draft.value }, state.cap);
      if (capped.days[state.draft.day] > 0) days[state.draft.day] = capped.days[state.draft.day];
    }
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
    if (input) {
      const recorded = state.days[state.selected];
      if (recorded) {
        input.value = String(recorded);
      } else if (state.draft && state.draft.day === state.selected) {
        // **预填**：今天还没记 ⇒ 把上次的数字填进来（她可以改）
        input.value = String(state.draft.value);
      } else {
        input.value = '';
      }
    }
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

    // **预填提示** —— 必须让她知道「这是上次的数，不是今天的」
    const pre = $('#prefillNote');
    if (state.draft && !(Number(state.days[state.draft.day]) > 0)) {
      pre.hidden = false;
      pre.textContent = '今天还没记 —— 上次记的是 ' + state.draft.value + ' 个（' +
        shortLabel(state.draft.from) + '），已经填在上面的框里了：改一下或直接提交。' +
        '（这个数还没算进今天的额度，提交之后才算。）';
    } else {
      pre.hidden = true;
      pre.textContent = '';
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
        clearResult();          // 换了一天 ⇒ 上一次「已记录」那条提示作废
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

  /** 提交按钮：点一下直接交（交不上去就排队）；没有量时禁用。 */
  function renderSubmit() {
    const { days } = reportDays();
    const btn = $('#submitBtn');
    const has = Object.keys(days).length > 0;
    // 老办法的链接（**永远留着** —— workers.dev 在国内可能抽风，这是兜底）
    const old = $('#oldWayBtn');
    if (old) old.href = has ? buildIssueUrl(days) : '#';
    if (has && state.enabled) {
      btn.classList.remove('off');
      btn.removeAttribute('aria-disabled');
    } else if (!state.enabled) {
      // 源被停用 ⇒ 服务端会整份拒收 ⇒ 页面不许给她一个「看起来能交」的按钮
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
    } else {
      btn.classList.add('off');
      btn.setAttribute('aria-disabled', 'true');
    }
    // 提示：**结果提示优先**，没有才说「现在是什么状态、怎么交」
    const hint = $('#submitHint');
    if (state.lastSubmit) {
      // 结果提示是纯文本（服务器来的错误消息不能当 HTML 插）
      if (hint.textContent !== state.lastSubmit.msg) hint.textContent = state.lastSubmit.msg;
    } else {
      const hintHtml = !state.enabled
        ? '备考源现在被停用了（注册表里 <code>enabled</code> 不是 true）—— ' +
          '交上去服务端也不会收这一类上报。'
        : (!has
          ? '还没有记下任何词 —— 先在上面填个数。'
          : (ingestAvailable()
            ? '点「交上去」直接交（不用登录 GitHub）。'
            : '点「交上去」会打开 GitHub 的新建 issue 页，在那个页面上点一下 <b>Submit</b> 才算交上去。'));
      // ★ 内容没变就别重写 DOM —— 重画时把元素换掉，鼠标按下到松开那一下会点空。
      if (hint.innerHTML !== hintHtml) hint.innerHTML = hintHtml;
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

  /**
   * 「加到主屏」的提示：**不在 standalone 模式时**显示一行小字。
   *
   * 不弹窗、不打断 —— 她每天就是「打开 → 改个数 → 提交」，
   * 只有还在浏览器里用的时候才需要看到这一句怎么加。
   * 已经加到主屏（一点就开）就不用再说了 ⇒ 藏起来。
   */
  function renderInstallNote() {
    const note = $('#installNote');
    if (!note) return;
    if (isStandalone()) {
      note.hidden = true;
      note.textContent = '';
      return;
    }
    note.hidden = false;
    note.textContent = '可以把它加到主屏：iPhone 用 Safari「分享 → 添加到主屏幕」，' +
      '安卓用 Chrome「菜单 → 添加到主屏幕」。';
  }

  function render() {
    renderDay();
    renderWeek();
    renderSubmit();
    renderInstallNote();
  }

  /* ---------- 交互 ---------- */

  /**
   * 把输入框里的数字写进状态（空 = 0，非法 = 0）。
   *
   * @param {boolean} [byUser] 这是**她自己动**的输入框吗？
   *   - true  ⇒ 她打字了 ⇒ 照记（预填就此变成她自己的记录）。
   *   - false ⇒ 只是失焦之类的被动事件 ⇒ 预填还没被动过就**别记**
   *     （否则「只是点了一下别处」就把预填变成今天的记录 ⇒ 提前达标）。
   */
  function readInput(byUser) {
    if (!byUser && state.draft && state.draft.day === state.selected &&
        !(Number(state.days[state.selected]) > 0)) {
      // 预填原封不动 ⇒ 不动状态
      return;
    }
    const input = $('#countInput');
    const v = Number(input.value);
    const n = Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
    if (n > 0) state.days[state.selected] = n;
    else delete state.days[state.selected];
    state.days = trimDays(state.days);
    state.draft = null;          // 她已经表态了 ⇒ 预填使命完成
    clearResult();                // 她改了数 ⇒ 上一次「已记录」那条提示作废
    saveDays();
  }

  /**
   * 快捷按钮：+10 / +20 / +50 / 清零。
   *
   * 基数取**输入框里现在显示的数**（不是 `state.days`）——
   * 这样「预填了 150，她点 +10」得到的是 160，而不是 10。
   */
  function bump(delta) {
    const input = $('#countInput');
    const shown = Number(input ? input.value : 0);
    const base = Number.isFinite(shown) && shown > 0 ? Math.floor(shown) : (state.days[state.selected] || 0);
    const next = delta === 0 ? 0 : Math.max(0, base + delta);
    if (next > 0) state.days[state.selected] = next;
    else delete state.days[state.selected];
    state.days = trimDays(state.days);
    state.draft = null;          // 她已经表态了 ⇒ 预填使命完成
    clearResult();                // 她改了数 ⇒ 上一次「已记录」那条提示作废
    saveDays();
    render();
  }

  function bind() {
    const input = $('#countInput');
    input.addEventListener('input', () => {
      readInput(true);
      // 不重画输入框（免得正在输入的数字被抹掉），只更新其余部分
      renderMetrics();
      renderWeek();
      renderSubmit();
    });
    input.addEventListener('blur', () => readInput(false));
    $$('#quick button').forEach(b => {
      b.onclick = () => bump(Number(b.dataset.add || 0));
    });
    const btn = $('#submitBtn');
    btn.addEventListener('click', e => {
      if (btn.getAttribute('aria-disabled') === 'true') {
        e.preventDefault();
        return;
      }
      // ★ 必须挂 .catch —— doSubmit 是异步的，漏了会在控制台留一条
      //   "Unhandled promise rejection"，页面看着像坏了。
      //   （doSubmit 内部已经把「交不上去」兜成提示了，这里只是最后一道保险。）
      doSubmit().catch(() => { /* 出不了什么岔子，别让控制台红 */ });
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
    // **预填上次的数字**（减摩擦）：今天还没记、且上次记过 ⇒ 把上次的数填进输入框，
    // 她每天的量差不多 ⇒ 多数时候点一下提交就行。
    //
    // ⚠️ 预填**只影响输入框的初值**：它**不进 `state.days`**
    // ⇒ 额度/周合计仍按实际已记的天算（不然「还没提交」的日子会提前达标）。
    const today = todayKey();
    if (!(Number(state.days[today]) > 0)) {
      const prev = previousRecordedDay(state.days, today, WINDOW_DAYS);
      if (prev) state.draft = { day: today, value: prev.value, from: prev.day };
    }
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
