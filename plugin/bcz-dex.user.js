// ==UserScript==
// @name         百词斩 · 训练家图鉴
// @namespace    https://lilia-shound.github.io/pokemon-dex/
// @version      1.0.0
// @description  在百词斩网页版读出「今天背了多少词」，一键带进训练家图鉴的记数页（只带数，不代提交）
// @author       Mavis
// @match        https://baicizhan.com/*
// @match        https://www.baicizhan.com/*
// @match        https://*.baicizhan.com/*
// @updateURL    https://lilia-shound.github.io/pokemon-dex/bcz-dex.user.js
// @downloadURL  https://lilia-shound.github.io/pokemon-dex/bcz-dex.user.js
// @grant        none
// @run-at       document-start
// ==/UserScript==

/* ============================================================
   百词斩 · 训练家图鉴（油猴脚本）
   —— 在百词斩网页版上认出「今天背了多少词」，给一个小浮层：
      默认填上认出来的数 → 点「打开记数页」把它带进
      https://lilia-shound.github.io/pokemon-dex/words/?words=N
   —— **只带数，绝不自动提交**：最后那一下由她点（抽卡权只在服务端）。
   —— **不读也不发任何 Cookie / 凭证**：只读页面上的文字与页面自己请求回来的 JSON。
   —— 口径：今天背的词数 = **新学 + 复习**（百词斩首页/学习日历展示的就是这个口径）。
   —— ⚠️ 百词斩 App 左上角的「需新学 15 / 需复习 1」是**剩余量**，
      不是今天的成果 ⇒ 脚本把它们当**排除项**，绝不会把「还要背多少」
      当成「今天背了多少」。
   —— 零外部依赖：不引 jQuery、不引 CDN。
   ============================================================ */
(() => {
  "use strict";

  /* ---------- 纯逻辑（浏览器与 node 都跑得起来） ---------- */

  /** 记数页地址（带上今天背了多少个词） */
  const LANDING_BASE = "https://lilia-shound.github.io/pokemon-dex/words/";

  /**
   * 单日词数的「防呆天花板」。
   *
   * 注册表里 words 源的 maxPerDay 是 500 ⇒ 一天真背了 800 词都算离谱。
   * 这个天花板用来挡掉两类误读：
   *   - 累计总量（「累计学会 12000 个词」这种）；
   *   - 词书总量（「本词书共 3200 词」这种）。
   * 超了就当**没认出来**（宁可让她手填，也不填一个错的数）。
   */
  const SANITY_MAX = 2000;

  /**
   * 关键词清单 —— **总数**类（数字本身就是「今天背了多少」）。
   * 长的排前面（匹配时先试长的）。
   */
  const TOTAL_LABELS = [
    "今日学习量", "今日已学习", "今日已学", "今日学习", "今日单词", "今日完成",
    "今日已背", "今日背", "今日完成量", "今日记住了", "今日学习词数",
    "今天学习量", "今天已学习", "今天已学", "今天学习", "今天单词", "今天完成", "今天已背",
    "本日学习量", "本日已学", "本日学习",
    "学习量", "完成量", "已学习", "已学", "已完成", "已背", "完成", "学完", "已掌握",
    "背了", "记住了", "已斩",
  ];

  /**
   * 关键词清单 —— **分量**类（新学 / 复习）。
   * 两个都有 ⇒ 相加（口径就是「新学 + 复习」）；只有一个 ⇒ 当成半个口径，会在浮层上说清楚。
   */
  const NEW_LABELS = [
    "今日新学", "今天新学", "本日新学", "新学单词", "新学", "新词",
  ];
  const REVIEW_LABELS = [
    "今日复习", "今天复习", "本日复习", "已复习", "复习了", "复习",
  ];

  /**
   * **排除清单** —— 这些标签后面的数字是「**还要**背多少」，不是今天的成果。
   *
   * 匹配时先扫这一份：只要某个数字落在排除项的数字范围里，
   * 那个数字就**不算数**（哪怕它同时被「新学 / 复习」这些正面标签命中）。
   *
   * ⚠️ 这份清单是本脚本最容易搞反的地方：
   * 「需新学 15 / 需复习 1」= 剩下要背的 ⇒ 绝不能当成今天的成果。
   */
  const REMAINING_LABELS = [
    "需新学", "需复习", "需背", "需学习", "需单词", "需完成", "需记忆",
    "还剩", "剩余", "剩余量", "还需", "还有",
    "待复习", "待新学", "待学习", "待背", "待完成",
    "未学习", "未学", "未背", "未完成", "未掌握",
  ];

  /** 「新学 30 复习 107」这种挨着的算一组（字符距离上限） */
  const PAIR_WINDOW = 80;

  /** 转义正则里的特殊字符 */
  function escapeRe(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /** 关键词 → 正则（长的排前面，匹配时先试长的） */
  function labelRegex(labels) {
    const sorted = labels.slice().sort((a, b) => b.length - a.length);
    return new RegExp("(" + sorted.map(escapeRe).join("|") + ")(\\s*[:：]?\\s*)(\\d{1,6})", "g");
  }

  /**
   * 在一段文字里找出所有「标签 + 数字」。
   * @param {string} text
   * @param {string[]} labels 关键词清单
   * @returns {Array<{label: string, value: number, start: number, numStart: number, numEnd: number}>}
   */
  function findLabelMatches(text, labels) {
    const re = labelRegex(labels);
    const out = [];
    let m;
    while ((m = re.exec(text)) !== null) {
      const label = m[1];
      const gap = m[2] || "";
      const digits = m[3];
      const numStart = m.index + label.length + gap.length;
      out.push({
        label: label,
        value: Number(digits),
        start: m.index,
        numStart: numStart,
        numEnd: numStart + digits.length,
      });
    }
    return out;
  }

  /**
   * 这个数字是不是落在某个「剩余量」的范围里？
   * （「需新学 15」里的 15 ⇒ 是；「今日学习 137」里的 137 ⇒ 不是）
   */
  function isRemaining(cand, remainingList) {
    return remainingList.some((r) => r.numStart <= cand.numStart && cand.numEnd <= r.numEnd);
  }

  /** 标签前面不远处有没有「今日 / 今天 / 本日」？ */
  function hasTodayNear(text, pos) {
    const before = text.slice(Math.max(0, pos - 12), pos);
    return /今日|今天|本日/.test(before);
  }

  /** 标签本身的可信度：带「今日」的最可信，其次是「已学/完成」，最后是「…量」 */
  function labelScore(label) {
    if (/^(今日|今天|本日)/.test(label)) return 50;
    if (/^(已|完成|学完|背了|记住)/.test(label)) return 30;
    if (/量$/.test(label)) return 20;
    return 10;
  }

  /** 挑一个最可信的候选（分数高的赢；同分取文档里靠前的那个） */
  function pickBest(cands, text) {
    let best = null;
    let bestScore = -1;
    for (const c of cands) {
      const s = labelScore(c.label) + (hasTodayNear(text, c.start) ? 20 : 0);
      if (s > bestScore) {
        bestScore = s;
        best = c;
      }
    }
    return best;
  }

  /**
   * 找「新学 + 复习」这一组（挨在一起的两个数）。
   * 多个候选时：带「今日」的那组赢；都没有就取最靠前的一组。
   */
  function findPair(news, reviews, text) {
    let best = null;
    let bestScore = -1;
    for (const n of news) {
      for (const r of reviews) {
        const gap = Math.max(n.numStart, r.numStart) - Math.min(n.numEnd, r.numEnd);
        if (gap > PAIR_WINDOW) continue;
        const score =
          (hasTodayNear(text, n.start) ? 20 : 0) +
          (hasTodayNear(text, r.start) ? 20 : 0) -
          gap * 0.01;
        if (score > bestScore) {
          bestScore = score;
          best = { neu: n, rev: r, gap: gap };
        }
      }
    }
    return best;
  }

  /**
   * 取候选附近的一小段原文（浮层上给她看：这个数是从哪句话读来的）。
   *
   * 两头都**避开半个数字**：切出来的片段里出现「…需新学 1」（其实是 15 的一半）
   * 会让她以为脚本读错了 ⇒ 切完之后把首尾残缺的数字串去掉。
   */
  function snippetAround(text, cand) {
    const before = text.slice(Math.max(0, cand.start - 12), cand.start).replace(/^\s*\d+\s*/, "");
    const after = text.slice(cand.numEnd, Math.min(text.length, cand.numEnd + 10)).replace(/\s*\d+\s*$/, "");
    return (before + (before && !/\s$/.test(before) ? " " : "") + cand.label + " " + cand.value + after)
      .replace(/\s+/g, " ")
      .trim();
  }

  /** 一组候选（两个挨着的数）跨着的那一小段原文 */
  function snippetAcross(text, first, last) {
    const from = Math.max(0, first.start - 10);
    const to = Math.min(text.length, last.numEnd + 8);
    return text.slice(from, to).replace(/\s+/g, " ").trim();
  }

  /**
   * 从**页面文字**里认「今天背了多少词」。
   *
   * 优先级：总数标签 > 新学 + 复习 > 单个分量。
   * 「需新学 / 需复习 / 还剩 / 剩余」这类**剩余量**一律排除。
   *
   * @param {string} text 页面可见文字（document.body.innerText）
   * @returns {?{value: number, kind: string, label: string, snippet: string}}
   *   kind: total（总数）| sum（新学+复习）| partial（只读到一半）
   *   认不出来 ⇒ null（**绝不瞎猜一个数填进去**）
   */
  function parseCountsFromText(text) {
    if (typeof text !== "string" || !text.trim()) return null;

    const remaining = findLabelMatches(text, REMAINING_LABELS);
    const totals = findLabelMatches(text, TOTAL_LABELS).filter((c) => !isRemaining(c, remaining));
    const news = findLabelMatches(text, NEW_LABELS).filter((c) => !isRemaining(c, remaining));
    const revs = findLabelMatches(text, REVIEW_LABELS).filter((c) => !isRemaining(c, remaining));

    const sane = (list) => list.filter((c) => c.value > 0 && c.value <= SANITY_MAX);

    if (sane(totals).length) {
      const best = pickBest(sane(totals), text);
      return {
        value: best.value,
        kind: "total",
        label: best.label,
        snippet: snippetAround(text, best),
      };
    }

    const pair = findPair(sane(news), sane(revs), text);
    if (pair) {
      const value = pair.neu.value + pair.rev.value;
      if (value > 0 && value <= SANITY_MAX) {
        return {
          value: value,
          kind: "sum",
          label: pair.neu.label + " + " + pair.rev.label,
          snippet: snippetAcross(text, pair.neu, pair.rev),
        };
      }
    }

    if (sane(news).length) {
      const best = pickBest(sane(news), text);
      return {
        value: best.value,
        kind: "partial",
        label: best.label,
        snippet: snippetAround(text, best),
      };
    }
    if (sane(revs).length) {
      const best = pickBest(sane(revs), text);
      return {
        value: best.value,
        kind: "partial",
        label: best.label,
        snippet: snippetAround(text, best),
      };
    }
    return null;
  }

  /* ---------- 接口兜底（页面自己请求回来的 JSON） ---------- */

  /**
   * 明确表示「**已完成**」的键（名字里带 today / learned / studied / finished / completed）。
   * 只有这类键才敢直接用。
   */
  const KEY_TOTAL_DONE = [
    "todaywordcount", "todaylearncount", "todaystudiedcount", "todaylearnedcount",
    "todaydonecount", "learnedwordcount", "studiedwordcount", "finishedwordcount",
    "completedwordcount", "learnedcount", "studiedcount", "finishedcount", "donewordcount",
  ];
  const KEY_NEW_DONE = [
    "todaynewwordcount", "todaynewcount", "todaynewwords", "learnednewcount", "finishednewcount",
  ];
  const KEY_REVIEW_DONE = [
    "todayreviewwordcount", "todayreviewcount", "todayreviewwords",
    "learnedreviewcount", "finishedreviewcount",
  ];

  /**
   * 语义**不明确**的键 —— 百词斩接口里这几个名字很可能就是
   * 「还需新学多少 / 还需复习多少」（App 左上角那两个数）⇒
   * **只有在明确是「今天」的节点里才敢用**；拿不准就不用。
   */
  const KEY_TOTAL_MAYBE = ["wordcount", "learncount", "totalwordcount"];
  const KEY_NEW_MAYBE = ["newwordcount", "newcount", "newwords"];
  const KEY_REVIEW_MAYBE = ["reviewcount", "reviewwords", "reviewwordcount"];

  /** 递归把 JSON 拆成一堆「对象节点」，记下每个节点的路径 */
  function walkPayload(node, path, out) {
    if (node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) walkPayload(node[i], path.concat("[" + i + "]"), out);
      return;
    }
    out.push({ path: path, obj: node });
    for (const k of Object.keys(node)) walkPayload(node[k], path.concat(k), out);
  }

  /** 在一个对象里找第一个能用的键（返回 {key, value}，键名用原样大小写） */
  function pickKey(obj, keys) {
    for (const want of keys) {
      for (const k of Object.keys(obj)) {
        if (k.toLowerCase() !== want) continue;
        const v = Number(obj[k]);
        if (!Number.isFinite(v) || v <= 0 || v > SANITY_MAX) continue;
        return { key: k, value: Math.floor(v) };
      }
    }
    return null;
  }

  /** 这个节点是不是「明确属于今天」的？ */
  function nodeIsToday(node, todayKey) {
    for (const seg of node.path) {
      const s = String(seg).toLowerCase();
      if (s.indexOf("today") >= 0 || s.indexOf("daily") >= 0) return true;
    }
    if (!todayKey) return false;
    const compact = String(todayKey).replace(/-/g, "");
    for (const k of Object.keys(node.obj)) {
      const lk = k.toLowerCase();
      if (lk.indexOf("date") < 0 && lk !== "day") continue;
      const v = String(node.obj[k]);
      if (v === String(todayKey) || v === compact) return true;
    }
    return false;
  }

  /**
   * 从接口 JSON 里认「今天背了多少词」。
   *
   * 规矩：
   *   - 只认「已完成」语义的键；语义不明确的键（newWordCount / reviewCount /
   *     wordCount / learnCount）**只在明确是「今天」的节点里**才用；
   *   - 认不出来 ⇒ null（不猜）。
   *
   * @param {*} payload 接口回来的 JSON
   * @param {string} todayKey 今天（YYYY-MM-DD），用来判断节点是不是今天的
   * @returns {?{value: number, kind: string, label: string, snippet: string}}
   */
  function parseCountsFromPayload(payload, todayKey) {
    if (payload === null || typeof payload !== "object") return null;
    const nodes = [];
    walkPayload(payload, [], nodes);

    let best = null;
    let bestScore = -1;
    for (const node of nodes) {
      const scoped = nodeIsToday(node, todayKey);
      const total = pickKey(node.obj, KEY_TOTAL_DONE) || (scoped ? pickKey(node.obj, KEY_TOTAL_MAYBE) : null);
      const neu = pickKey(node.obj, KEY_NEW_DONE) || (scoped ? pickKey(node.obj, KEY_NEW_MAYBE) : null);
      const rev = pickKey(node.obj, KEY_REVIEW_DONE) || (scoped ? pickKey(node.obj, KEY_REVIEW_MAYBE) : null);
      if (!total && !neu && !rev) continue;

      let cand = null;
      if (total) {
        cand = { value: total.value, kind: "total", label: total.key, snippet: total.key + " = " + total.value };
      } else if (neu && rev) {
        const value = neu.value + rev.value;
        if (value > 0 && value <= SANITY_MAX) {
          cand = {
            value: value,
            kind: "sum",
            label: neu.key + " + " + rev.key,
            snippet: neu.key + " = " + neu.value + "，" + rev.key + " = " + rev.value,
          };
        }
      } else if (neu) {
        cand = { value: neu.value, kind: "partial", label: neu.key, snippet: neu.key + " = " + neu.value };
      } else if (rev) {
        cand = { value: rev.value, kind: "partial", label: rev.key, snippet: rev.key + " = " + rev.value };
      }
      if (!cand) continue;

      const quality = cand.kind === "total" ? 3 : cand.kind === "sum" ? 2 : 1;
      const score = quality * 10 + (scoped ? 5 : 0);
      if (score > bestScore) {
        bestScore = score;
        best = cand;
      }
    }
    return best;
  }

  /**
   * 记数页链接（带上今天背了多少个词）。
   * @param {number} n
   * @returns {string}
   */
  function landingUrl(n) {
    const v = Math.max(0, Math.floor(Number(n) || 0));
    return LANDING_BASE + "?words=" + v;
  }

  /* ---------- 导出（node 侧要单测这些纯函数） ---------- */
  const API = {
    parseCountsFromText: parseCountsFromText,
    parseCountsFromPayload: parseCountsFromPayload,
    landingUrl: landingUrl,
    TOTAL_LABELS: TOTAL_LABELS,
    NEW_LABELS: NEW_LABELS,
    REVIEW_LABELS: REVIEW_LABELS,
    REMAINING_LABELS: REMAINING_LABELS,
    SANITY_MAX: SANITY_MAX,
    LANDING_BASE: LANDING_BASE,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  if (typeof globalThis !== "undefined") globalThis.BczDex = API;

  /* ---------- 页面部分（只在浏览器里跑） ---------- */
  if (typeof document === "undefined") return;

  /**
   * 接口钩子**立刻**装上（不等 DOMContentLoaded）。
   *
   * 为什么：页面自己的第一个接口请求往往在 DOM 就绪之前就发出去了
   * ⇒ 等到 document-idle 再装钩子，那份响应已经错过（接口兜底就白干了）。
   * 钩子只是旁路读一份响应，**不改变页面自己的请求**。
   */
  installFetchHook();
  installXhrHook();

  const CSS = `
  /* [hidden] 必须真的藏起来：类选择器里的 display 会盖掉浏览器默认的
     [hidden]{display:none} ⇒ 这里写死。 */
  .bcz-fab[hidden],.bcz-panel[hidden]{display:none !important}
  .bcz-fab{position:fixed;right:14px;bottom:14px;z-index:2147483000;
    display:flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;
    background:rgba(255,253,248,.95);border:1px solid #e3d8c6;color:#2f2823;cursor:pointer;
    box-shadow:0 6px 20px rgba(90,64,40,.18);user-select:none;
    font:13px/1.2 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
  .bcz-fab:hover{transform:translateY(-1px)}
  .bcz-fab .bcz-n{font-weight:600;font-variant-numeric:tabular-nums}
  .bcz-fab .bcz-q{font-size:11px;color:#9a8d80}

  .bcz-panel{position:fixed;right:14px;bottom:14px;z-index:2147483001;width:264px;
    padding:11px 12px 10px;border-radius:14px;background:#fffdf8;border:1px solid #e3d8c6;
    box-shadow:0 12px 32px rgba(90,64,40,.22);color:#2f2823;
    font:13px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
  .bcz-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
  .bcz-head b{font-size:13px}
  .bcz-head .bcz-btns{display:flex;gap:6px}
  .bcz-head button{border:1px solid #e3d8c6;background:#f6f1e7;color:#9a8d80;border-radius:8px;
    padding:1px 7px;font:12px/1.6 inherit;cursor:pointer}
  .bcz-head button:hover{color:#2f2823}
  .bcz-row{display:flex;align-items:center;gap:6px}
  .bcz-row input{flex:1 1 auto;min-width:0;width:100%;padding:6px 9px;border-radius:9px;
    border:1px solid #e3d8c6;background:#fff;color:#2f2823;font:15px/1.4 inherit;
    font-variant-numeric:tabular-nums}
  .bcz-row input:focus{outline:none;border-color:#c8623a}
  .bcz-row span{color:#9a8d80;font-size:12px}
  .bcz-src{margin:7px 0 0;font-size:12px;color:#9a8d80;word-break:break-all;line-height:1.5}
  .bcz-src b{color:#c8623a}
  .bcz-open{display:block;margin-top:9px;text-align:center;padding:7px 10px;border-radius:10px;
    background:#2f2823;color:#f6f1e7;text-decoration:none;font-size:13px;font-weight:600}
  .bcz-open:hover{background:#c8623a;color:#fff}
  .bcz-open.off{background:#e9e0d0;color:#9a8d80;pointer-events:none}
  .bcz-tip{margin:7px 0 0;font-size:11px;color:#b3a693;line-height:1.5}
  `;

  /** 认数结果的「可信度」：总数 > 新学+复习 > 半个口径 */
  function qualityOf(kind) {
    return kind === "total" ? 3 : kind === "sum" ? 2 : 1;
  }

  const state = {
    value: null,       // 认出来的词数（null = 没认出来）
    kind: "none",      // total / sum / partial / none
    source: "none",    // text（页面文字）/ api（接口）/ manual（手填）
    detail: "",        // 来源细节（标签、键名、原文片段）
    manual: false,     // 她自己改过数
    quality: 0,
    hidden: false,     // 整个藏起来了
  };

  let PANEL = null;
  let FAB = null;

  function pad(n) { return String(n).padStart(2, "0"); }
  function todayKey() {
    const d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  /** 装样式 */
  function injectStyle() {
    const style = document.createElement("style");
    style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  /** 建浮层（只建一次，之后只更新内容） */
  function buildUI() {
    if (PANEL) return;
    FAB = document.createElement("div");
    FAB.className = "bcz-fab";
    FAB.id = "bcz-fab";
    FAB.hidden = true;
    FAB.title = "训练家图鉴 · 背单词";
    FAB.innerHTML = '<span>🔤</span><span class="bcz-n"></span><span class="bcz-q"></span>';
    FAB.addEventListener("click", () => {
      if (state.hidden) return;
      PANEL.hidden = false;
      FAB.hidden = true;
    });

    PANEL = document.createElement("div");
    PANEL.className = "bcz-panel";
    PANEL.id = "bcz-panel";
    PANEL.innerHTML =
      '<div class="bcz-head"><b>背单词 · 训练家图鉴</b>' +
      '<span class="bcz-btns"><button type="button" data-act="collapse" title="收起来">收起</button>' +
      '<button type="button" data-act="hide" title="藏起来">×</button></span></div>' +
      '<div class="bcz-row"><input id="bcz-count" type="number" inputmode="numeric" min="0" step="1" ' +
      'placeholder="0" autocomplete="off" aria-label="今天背了多少个词"><span>个</span></div>' +
      '<p class="bcz-src" id="bcz-src"></p>' +
      '<a class="bcz-open" id="bcz-open" target="_blank" rel="noopener noreferrer">打开记数页</a>' +
      '<p class="bcz-tip">只把数带过去 —— 最后那一下由你点。</p>';

    PANEL.querySelector('[data-act="collapse"]').addEventListener("click", () => {
      PANEL.hidden = true;
      FAB.hidden = false;
    });
    PANEL.querySelector('[data-act="hide"]').addEventListener("click", () => {
      state.hidden = true;
      PANEL.hidden = true;
      FAB.hidden = true;
    });

    const input = PANEL.querySelector("#bcz-count");
    input.addEventListener("input", () => {
      // 她自己改数 ⇒ 来源标成「手填」（她一眼知道这个数不是机器读的了）
      state.manual = true;
      state.source = "manual";
      state.detail = "手填的数";
      paint();
    });

    document.body.appendChild(FAB);
    document.body.appendChild(PANEL);
  }

  /** 来源说明那一行 */
  function sourceText() {
    if (state.source === "text") {
      const partial = state.kind === "partial"
        ? "（只读到「" + state.detail + "」，口径是「新学 + 复习」）"
        : "";
      return "从页面文字读到：<b>" + state.detail + "</b>" + partial;
    }
    if (state.source === "api") {
      return "从接口读到：<b>" + state.detail + "</b>";
    }
    if (state.source === "manual") {
      return "手填的数 —— <b>没认出来就自己填</b>";
    }
    return "没认出来，手动填一下（我不会瞎猜一个数）";
  }

  /** 把状态画到浮层上 */
  function paint() {
    if (!PANEL) return;
    const input = PANEL.querySelector("#bcz-count");
    const link = PANEL.querySelector("#bcz-open");
    const src = PANEL.querySelector("#bcz-src");

    if (!state.manual && state.value !== null && state.value > 0) {
      input.value = String(state.value);
    }
    src.innerHTML = sourceText();

    const n = Math.max(0, Math.floor(Number(input.value) || 0));
    if (n > 0) {
      link.href = landingUrl(n);
      link.classList.remove("off");
    } else {
      link.href = "#";
      link.classList.add("off");
    }

    if (FAB) {
      FAB.hidden = state.hidden || !PANEL.hidden;
      const num = FAB.querySelector(".bcz-n");
      const q = FAB.querySelector(".bcz-q");
      num.textContent = n > 0 ? String(n) : "?";
      q.textContent = state.source === "none" ? "" : (state.source === "text" ? "读到的" : "");
    }
  }

  /** 用一个新认出来的结果更新状态（只在新结果更可信时才覆盖） */
  function applyResult(result, source) {
    if (!result) return false;
    // 她已经自己改过数了 ⇒ **不要盖掉她填的**（她填的才是她要的）
    if (state.manual) return false;
    const q = qualityOf(result.kind);
    if (q < state.quality) return false;
    if (q === state.quality && state.source !== "none" && source !== state.source) {
      // 同样可信 ⇒ 先来的赢（页面文字比接口更稳）
      if (source !== "text") return false;
    }
    state.value = result.value;
    state.kind = result.kind;
    state.quality = q;
    state.source = source;
    state.detail = result.kind === "partial"
      ? result.label + "（" + result.snippet + "）"
      : (result.snippet || result.label);
    state.manual = false;
    paint();
    return true;
  }

  /** 扫一遍页面可见文字 */
  function scanText() {
    if (!document.body) return;
    const text = document.body.innerText || "";
    const result = parseCountsFromText(text);
    if (result) applyResult(result, "text");
  }

  /** 页面自己请求回来的 JSON —— 只读，不发任何请求 */
  function handlePayloadText(text) {
    if (typeof text !== "string" || !text || text.length > 200000) return;
    const trimmed = text.trim();
    if (trimmed.charAt(0) !== "{" && trimmed.charAt(0) !== "[") return;
    let obj = null;
    try {
      obj = JSON.parse(trimmed);
    } catch (e) {
      return;
    }
    const result = parseCountsFromPayload(obj, todayKey());
    if (result) applyResult(result, "api");
  }

  /** hook fetch（只旁路读一份响应，**不改**页面自己的请求） */
  function installFetchHook() {
    if (typeof window.fetch !== "function") return;
    const orig = window.fetch;
    if (orig.__bczHooked) return;
    const wrapped = function () {
      const p = orig.apply(this, arguments);
      return p.then(function (res) {
        try {
          if (res && typeof res.clone === "function") {
            res.clone().text().then(handlePayloadText, function () { /* 读不到就算了 */ });
          }
        } catch (e) { /* 读不到就算了 */ }
        return res;
      });
    };
    wrapped.__bczHooked = true;
    window.fetch = wrapped;
  }

  /** hook XMLHttpRequest（同上：只旁路读一份响应） */
  function installXhrHook() {
    if (typeof XMLHttpRequest === "undefined") return;
    const proto = XMLHttpRequest.prototype;
    if (proto.__bczHooked) return;
    const origOpen = proto.open;
    const origSend = proto.send;
    proto.open = function () {
      this.__bczUrl = String(arguments[1] || "");
      return origOpen.apply(this, arguments);
    };
    proto.send = function () {
      try {
        this.addEventListener("load", function () {
          try {
            const type = this.responseType;
            if (type && type !== "text" && type !== "json") return;
            const body = type === "json" ? JSON.stringify(this.response) : this.responseText;
            handlePayloadText(body);
          } catch (e) { /* 读不到就算了 */ }
        });
      } catch (e) { /* 读不到就算了 */ }
      return origSend.apply(this, arguments);
    };
    proto.__bczHooked = true;
  }

  /** 页面变了就再扫一遍（她背完词，页面上的数字会变） */
  function watchPage() {
    if (typeof MutationObserver === "undefined" || !document.body) return;
    let timer = null;
    const mo = new MutationObserver(function (records) {
      let onlyOurs = true;
      for (const r of records) {
        const t = r.target;
        if (t && PANEL && (t === PANEL || PANEL.contains(t))) continue;
        if (t && FAB && (t === FAB || FAB.contains(t))) continue;
        onlyOurs = false;
        break;
      }
      if (onlyOurs) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(scanText, 400);
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function boot() {
    injectStyle();
    if (!document.body) return;
    buildUI();
    scanText();
    paint();
    watchPage();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
