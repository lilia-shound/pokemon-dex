'use strict';
/* Worker 校验逻辑的 node 单测。跑法：node tests/worker_validate_spec.js */

const assert = require('assert');
const path = require('path');
const V = require(path.join(__dirname, '..', 'worker', 'lib', 'validate.cjs'));
const R = require(path.join(__dirname, '..', 'worker', 'lib', 'rules.cjs'));

const TODAY = '2026-11-25';

function ok(report, today) {
  const r = V.validateReport(report, R.RULES, today || TODAY);
  assert.strictEqual(r.ok, true, '本该通过：' + JSON.stringify(r));
  return r;
}
function bad(report, today) {
  const r = V.validateReport(report, R.RULES, today || TODAY);
  assert.strictEqual(r.ok, false, '本该被拒：' + JSON.stringify(report));
  return r.error;
}

// ── 正常形状 ─────────────────────────────────────────────
const r = ok({ v: 1, source: 'pom', days: { '2026-11-21': 37 } });
assert.deepStrictEqual(r.days, { '2026-11-21': 37 });
assert.strictEqual(r.source, 'pom');

// ── 版本不对 ─────────────────────────────────────────────
assert.ok(bad({ v: 2, source: 'pom', days: { '2026-11-21': 37 } }).indexOf('版本') >= 0);

// ── 源不在白名单 ─────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'weread', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: '', days: { '2026-11-21': 37 } }).indexOf('source') >= 0);

// ── days 的形状 ──────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: {} }).indexOf('没有天数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: [] }).indexOf('days') >= 0);
const many = {};
for (let i = 1; i <= 31; i++) many['2026-10-' + String(i).padStart(2, '0')] = 1;
assert.ok(bad({ v: 1, source: 'pom', days: many }).indexOf('一次最多带 30 天') >= 0);

// ── 日期 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21 10:00': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-13-01': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-26': 5 } }).indexOf('未来') >= 0);

// ── 数值 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': -1 } }).indexOf('负数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1.5 } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': '37' } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1441 } }).indexOf('超过上限') >= 0);
assert.ok(bad({ v: 1, source: 'words', days: { '2026-11-21': 501 } }).indexOf('超过上限') >= 0);

// ── 今天的判定：UTC+8，不是 UTC ──────────────────────────
// 2026-11-25T17:30:00Z ⇒ 北京时间已经是 11-26 了
const t = Date.parse('2026-11-25T17:30:00Z');
assert.strictEqual(V.todayCst(t), '2026-11-26');
// 2026-11-25T15:30:00Z ⇒ 北京时间还是 11-25
assert.strictEqual(V.todayCst(Date.parse('2026-11-25T15:30:00Z')), '2026-11-25');

// ── 限幅表 ───────────────────────────────────────────────
assert.deepStrictEqual(Object.keys(R.RULES).sort(), ['koreader', 'pom', 'words']);

console.log('worker 校验逻辑：全部通过');
