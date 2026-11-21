#!/usr/bin/env node
/*
 * KOReader 落地页的线格式对拍（JS 侧）。
 *
 *   node tests/koreader_landing_spec.js
 *
 * 把 tests/fixtures/koreader_wire_cases.txt 里的每一条线喂给**落地页真正用的那份代码**
 * （site/koreader/app.js —— 不是另抄一份实现），把结果吐成一行 JSON：
 * Python 侧（tests/test_koreader_landing.py）拿它跟 Python 的 parse_days 逐例对拍。
 *
 * **为什么共用一份用例文件**：Lua / Python / JS 三端各写一份字面量的话，
 * 三边一起漂移都发现不了。用例只有一份 ⇒ 谁改了规则，另外两端立刻红。
 *
 * 退出码：0 = 全过；1 = 自检没过；2 = runner 自己没跑起来。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const WIRE_CASES = path.join(__dirname, 'fixtures', 'koreader_wire_cases.txt');
const SOURCES_JSON = path.join(ROOT, 'data', 'sources.json');

function die(msg) {
  console.error('runner 出错：' + msg);
  process.exit(2);
}

function readText(p, what) {
  try {
    return fs.readFileSync(p, 'utf8');
  } catch (err) {
    die('读不到' + what + '：' + p + '（' + err.message + '）');
  }
}

/* 落地页的那份代码（浏览器与 node 都跑得起来） */
let page;
try {
  page = require(path.join(ROOT, 'site', 'koreader', 'app.js'));
} catch (err) {
  die('加载 site/koreader/app.js 失败：' + err.message);
}
if (!page || typeof page.parseDays !== 'function') {
  die('site/koreader/app.js 没有导出 parseDays');
}

/* 线格式的用例：每行一条（**不 trim 每一行** —— 带空白的行本身就是用例） */
const raw = readText(WIRE_CASES, '线格式用例文件');
const wireCases = raw.endsWith('\n') ? raw.slice(0, -1) : raw;
if (!wireCases) die('线格式用例文件是空的');

/*
 * 用例里的转义：`\n`（反斜杠 + n）表示一个**真的换行符** ——
 * 文件一行只能放一条线，而「段尾带换行」这种用例必须真的带一个换行符。
 * （Python 侧与 Lua 侧用的是同一套约定，见 tests/koreader_lua_bridge.py。）
 */
function decodeWire(line) {
  return line.replace(/\\n/g, '\n');
}

/* 额度折算口径（几分钟 1 个额度）从服务端注册表里读，别在测试里另抄一份 */
let per = 15;
let cap = 1440;
try {
  const registry = JSON.parse(readText(SOURCES_JSON, '源注册表'));
  if (registry && registry.sources && registry.sources.koreader) {
    per = registry.sources.koreader.per;
    cap = registry.sources.koreader.maxPerDay;
  }
} catch (err) {
  die('读不到源注册表：' + SOURCES_JSON + '（' + err.message + '）');
}

/* ---------- 逐例跑一遍 ---------- */
const cases = wireCases.split('\n').map(decodeWire).map(wire => {
  const decoded = page.parseDays(wire);
  return {
    wire: wire,
    decoded: decoded,
    encoded: page.encodeDays(decoded),
    credits: page.creditsForDays(decoded, per, cap),
  };
});

/* ---------- 自检（脚本自己也得站得住） ---------- */
const fails = [];
function check(name, cond, detail) {
  if (!cond) fails.push(name + (detail ? ' —— ' + detail : ''));
}

const clean = cases.find(c => c.wire === '2026-11-21:25,2026-11-20:50');
check('干净的 fixture 在用例里', !!clean);
if (clean) {
  check('干净的 fixture 解出 25 / 50 分钟',
    clean.decoded['2026-11-21'] === 25 && clean.decoded['2026-11-20'] === 50,
    JSON.stringify(clean.decoded));
  check('干净的 fixture 编回去还是自己', clean.encoded === clean.wire, clean.encoded);
  check('额度是 4（按天 floor：1 + 3），不是 5', clean.credits === 4, String(clean.credits));
}

/* 每条线：解出来的东西要能编回去，而且再解一遍还是一样的（幂等） */
for (const c of cases) {
  const again = page.parseDays(c.encoded);
  check('编回去再解还是一样：' + JSON.stringify(c.wire),
    JSON.stringify(again) === JSON.stringify(c.decoded),
    JSON.stringify(again) + ' vs ' + JSON.stringify(c.decoded));
}

/* 单日上限：分钟数再大也只算一天的量 */
const over = page.creditsForDays({ '2026-11-21': 5000 }, per, cap);
check('超过单日上限要夹住', over === Math.floor(cap / per), String(over));

/* clampDays 的夹取口径要跟 creditsForDay 一致：负数夹 0、不是数的当 0、超上限夹上限 */
const clampedDays = page.clampDays({
  '2026-11-21': -5,        // 负数
  '2026-11-20': 5000,      // 超上限
  '2026-11-19': NaN,       // 不是数
}, cap);
check('负数夹到 0', clampedDays.days['2026-11-21'] === 0, String(clampedDays.days['2026-11-21']));
check('超上限夹到上限', clampedDays.days['2026-11-20'] === cap, String(clampedDays.days['2026-11-20']));
check('不是数的当 0', clampedDays.days['2026-11-19'] === 0, String(clampedDays.days['2026-11-19']));
check('只有超上限的那天才提示', clampedDays.clamped.length === 1 &&
  clampedDays.clamped[0].day === '2026-11-20', JSON.stringify(clampedDays.clamped));
/* 夹后的额度也要按夹后的分钟数算（页面显示的、提交的是同一份） */
check('夹后的额度按夹后的值算',
  page.creditsForDays(clampedDays.days, per, cap) === Math.floor(cap / per),
  String(page.creditsForDays(clampedDays.days, per, cap)));

/* ---------- 结果 ---------- */
console.log(JSON.stringify({ per: per, cap: cap, cases: cases }));

if (fails.length) {
  console.error('自检没过：');
  for (const f of fails) console.error('  ✗ ' + f);
  process.exit(1);
}
