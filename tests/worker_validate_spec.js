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

// ── 白名单不能被原型链绕过 ───────────────────────────────
// ★ 直接 table[source] 会命中 Object.prototype.constructor（一个函数）
//   ⇒ 「量比上限大」变成和 NaN 比（恒 false）⇒ 白名单和单日上限同时失效。
assert.ok(bad({ v: 1, source: 'constructor', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: '__proto__', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: 'toString', days: { '2026-11-21': 37 } }).indexOf('不认识这个源') >= 0);
// 绕过时的实际打法：拿一个不存在的源报一个爆表的量
assert.ok(bad({ v: 1, source: 'constructor', days: { '2026-11-21': 999999 } }).indexOf('不认识这个源') >= 0);
assert.ok(bad({ v: 1, source: 'hasOwnProperty', days: { '2026-11-21': 999999 } }).indexOf('不认识这个源') >= 0);

// ── days 的形状 ──────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: {} }).indexOf('没有天数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: [] }).indexOf('days') >= 0);
// 正好 30 天要通过（一次最多带 30 天 ⇒ 30 天收，31 天才拒）
const thirty = {};
{
  const end = new Date(Date.UTC(2026, 9, 27));   // 2026-10-27，往后推 30 天
  for (let i = 0; i < 30; i++) {
    thirty[end.toISOString().slice(0, 10)] = 1;
    end.setUTCDate(end.getUTCDate() + 1);
  }
}
assert.strictEqual(Object.keys(thirty).length, 30);
assert.strictEqual(Object.keys(thirty).sort()[0], '2026-10-27');
assert.strictEqual(Object.keys(thirty).sort()[29], '2026-11-25');
ok({ v: 1, source: 'pom', days: thirty });
const many = {};
for (let i = 1; i <= 31; i++) many['2026-10-' + String(i).padStart(2, '0')] = 1;
assert.ok(bad({ v: 1, source: 'pom', days: many }).indexOf('一次最多带 30 天') >= 0);

// ── 日期 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21 10:00': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-13-01': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-26': 5 } }).indexOf('未来') >= 0);
// ★ 假日期：V8 里 new Date('2026-02-30T00:00:00Z') 解析成功（得到 2026-03-02），
//   isNaN 拦不住 ⇒ 只有 isDate 里「toISOString 逐字比对」那一步能拦。
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-02-30': 5 } }).indexOf('日期格式') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-02-29': 5 } }).indexOf('日期格式') >= 0);  // 2026 不是闰年
// 日期正好是今天要通过（北京时间今天）
ok({ v: 1, source: 'pom', days: { '2026-11-25': 37 } });

// ── 数值 ─────────────────────────────────────────────────
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': -1 } }).indexOf('负数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1.5 } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': '37' } }).indexOf('不是整数') >= 0);
assert.ok(bad({ v: 1, source: 'pom', days: { '2026-11-21': 1441 } }).indexOf('超过上限') >= 0);
assert.ok(bad({ v: 1, source: 'words', days: { '2026-11-21': 501 } }).indexOf('超过上限') >= 0);
// 量正好等于单日上限要通过
ok({ v: 1, source: 'pom', days: { '2026-11-21': 1440 } });
ok({ v: 1, source: 'koreader', days: { '2026-11-21': 1440 } });
ok({ v: 1, source: 'words', days: { '2026-11-21': 500 } });

// ── 今天的判定：UTC+8，不是 UTC ──────────────────────────
// 2026-11-25T17:30:00Z ⇒ 北京时间已经是 11-26 了
const t = Date.parse('2026-11-25T17:30:00Z');
assert.strictEqual(V.todayCst(t), '2026-11-26');
// 2026-11-25T15:30:00Z ⇒ 北京时间还是 11-25
assert.strictEqual(V.todayCst(Date.parse('2026-11-25T15:30:00Z')), '2026-11-25');

// ── 限幅表 ───────────────────────────────────────────────
assert.deepStrictEqual(Object.keys(R.RULES).sort(), ['koreader', 'pom', 'words']);

console.log('worker 校验逻辑：全部通过');
