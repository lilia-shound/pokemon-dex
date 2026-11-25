'use strict';
/**
 * 免登录投递通道的**纯校验逻辑**。
 *
 * 这个文件**不碰网络、不碰 KV、不碰 Date.now 之外的任何东西** ——
 * 所以 node 可以直接 require 它跑单测（见 tests/worker_validate_spec.js），
 * 而 Worker 入口（worker/index.js，ESM）也 import 同一份 ⇒ **只有一份实现**。
 *
 * 校验清单（与服务端 scripts/collect_reports.py 的 parse_report **同构**）：
 *   1. body 是 JSON 对象
 *   2. `v` == 1
 *   3. `source` 在限幅表里（限幅表 = 白名单）
 *   4. `days` 是非空对象、条数 ≤ 30
 *   5. 每个 key 是合法 YYYY-MM-DD，且不晚于今天（**今天按 UTC+8 算**）
 *   6. 每个 value 是非负整数，且 ≤ 该源的单日上限
 *
 * **任何一条不过就整份拒收**（不部分采纳）—— 一条坏数据不该只污染一半。
 *
 * ★ 白名单查的是**自有属性**（hasOwnProperty）——
 *   直接 `table[body.source]` 会命中 `Object.prototype`
 *   （`constructor` / `__proto__` / `toString` …）⇒ 限幅层空转。
 */

const REPORT_VERSION = 1;
const MAX_REPORT_DAYS = 30;

/** 是不是整数（**布尔不算** —— JS 里没有 Python 那个 bool 是 int 子类的坑）。 */
function isInt(v) {
  return typeof v === 'number' && Number.isInteger(v);
}

/** 是不是合法的 YYYY-MM-DD（且是真实存在的日子）。 */
function isDate(s) {
  if (typeof s !== 'string' || s.length !== 10) return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(s + 'T00:00:00Z');
  if (isNaN(d.getTime())) return false;
  return d.toISOString().slice(0, 10) === s;   // 2026-02-30 这种会被这一步踢掉
}

/**
 * 北京时间（UTC+8）的今天。
 *
 * ★ 写死 +8 小时，不做时区推导：账本、图鉴页、scripts/sources.py
 *   全部按 Asia/Shanghai 分天 ⇒ Worker 若用 UTC 就会在每天 0:00~8:00
 *   （北京时间）判错一天。
 */
function todayCst(nowMs) {
  const ms = (typeof nowMs === 'number' && Number.isFinite(nowMs)) ? nowMs : Date.now();
  return new Date(ms + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

/**
 * 校验一份报告。
 * @param {*} body 客户端发来的 JSON（已解析）
 * @param {Object<string, number>} rules 源 id → 单日上限（同时是白名单）
 * @param {string} today 今天（YYYY-MM-DD，北京时间）
 * @returns {{ok: boolean, error?: string, days?: Object<string, number>, source?: string}}
 */
function validateReport(body, rules, today) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'body 不是 JSON 对象' };
  }
  if (body.v !== REPORT_VERSION) {
    return { ok: false, error: '版本不对：期望 v=1，收到 ' + JSON.stringify(body.v) };
  }
  if (typeof body.source !== 'string' || !body.source) {
    return { ok: false, error: 'source 不是源 id：' + JSON.stringify(body.source) };
  }
  // ★ 白名单必须查**自有属性**：直接 table[body.source] 会命中 Object.prototype
  //   （constructor / __proto__ / toString …）⇒ 拿到的 cap 是函数，
  //   「量比上限大」那个比较变成和 NaN 比（恒 false）⇒ 单日上限同时失效。
  const table = rules || {};
  if (!Object.prototype.hasOwnProperty.call(table, body.source)) {
    return { ok: false, error: '不认识这个源：' + body.source };
  }
  const cap = table[body.source];
  const days = body.days;
  if (!days || typeof days !== 'object' || Array.isArray(days)) {
    return { ok: false, error: 'days 不是对象：' + JSON.stringify(days) };
  }
  const keys = Object.keys(days);
  if (!keys.length) {
    return { ok: false, error: '报告里没有天数' };
  }
  if (keys.length > MAX_REPORT_DAYS) {
    return { ok: false, error: '一次最多带 ' + MAX_REPORT_DAYS + ' 天，收到 ' + keys.length + ' 天' };
  }
  const out = {};
  for (const day of keys) {
    if (!isDate(day)) {
      return { ok: false, error: '日期格式不对：' + day + '（要 YYYY-MM-DD）' };
    }
    if (day > today) {
      return { ok: false, error: '日期在未来：' + day };
    }
    const amount = days[day];
    if (!isInt(amount)) {
      return { ok: false, error: day + ' 的量不是整数：' + JSON.stringify(amount) };
    }
    if (amount < 0) {
      return { ok: false, error: day + ' 的量是负数：' + amount };
    }
    if (amount > cap) {
      return { ok: false, error: day + ' 的量 ' + amount + ' 超过上限 ' + cap };
    }
    out[day] = amount;
  }
  return { ok: true, days: out, source: body.source };
}

module.exports = { validateReport, todayCst, isInt, isDate, REPORT_VERSION, MAX_REPORT_DAYS };
