"""客户端的日期过滤必须与 Worker 的校验**逐条对齐**（跨语言对拍）。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_client_filter_parity

★ 为什么要有这条：
  客户端的 `filterReportDays` 是**重新实现**了一遍服务端的规则
  （worker/lib/validate.cjs 的 isDate + todayCst + 30 天上限）。
  两边口径一旦不一致，就会出两种坏结果：

    - 客户端**多剔**了服务端其实会收的日子 ⇒ **她的阅读时长被白扔**
      （比原来的 bug 更隐蔽 —— 页面看着一切正常，就是额度一直不来）；
    - 客户端**少剔**了服务端会拒的日子 ⇒ 毒丸还是进队列
      （虽然 flushPending 会兜住，但会白丢一次投递）。

⇒ 这条测试拿**同一批日子**同时问两边：
  客户端（app.js 里的原函数）与 Worker（worker/lib/validate.cjs），
  **结论必须逐条一致**。

★ 跟 tests/test_worker_rules.py 是同一套模式（那边对拍 Worker ↔ Python，
  这边对拍 Worker ↔ 客户端 JS）。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALIDATE_CJS = ROOT / "worker" / "lib" / "validate.cjs"

# 三个子页：各自的 app.js 与源 id（Worker 的限幅表按源 id 查）
# ★ 变异测试开关：环境变量可以指向 app.js 的另一份拷贝
#   （跟 tests/test_worker_client.py 里的 POM_APP_JS / WORDS_APP_JS / KO_APP_JS 一个路子）。
PAGES = [
    ("pomodoro",
     pathlib.Path(os.environ.get("POM_APP_JS") or (ROOT / "site" / "pomodoro" / "app.js")),
     "pom"),
    ("words",
     pathlib.Path(os.environ.get("WORDS_APP_JS") or (ROOT / "site" / "words" / "app.js")),
     "words"),
    ("koreader",
     pathlib.Path(os.environ.get("KO_APP_JS") or (ROOT / "site" / "koreader" / "app.js")),
     "koreader"),
]

# Worker 的限幅表（源 id → 单日上限）—— 只用来问「这一天服务端收不收」
RULES = {"pom": 1440, "words": 500, "koreader": 1440}


# --------------------------------------------------------------------------
# node 探针：抠出 app.js 里的原函数，与 Worker 的校验同时跑一遍
# --------------------------------------------------------------------------

NODE_PROBE = r"""
'use strict';
const fs = require('fs');
const V = require(process.env.VALIDATE_CJS);

/* ---------- 抠函数（与 tests/test_worker_client.py 的 NODE_PROBE 同一套） ---------- */
function skipString(s, i) {
  const q = s[i]; i += 1;
  while (i < s.length) {
    if (s[i] === '\\') { i += 2; continue; }
    if (s[i] === q) return i + 1;
    i += 1;
  }
  return i;
}
function skipTemplate(s, i) {
  i += 1;
  while (i < s.length) {
    const c = s[i];
    if (c === '\\') { i += 2; continue; }
    if (c === '`') return i + 1;
    if (c === '$' && s[i + 1] === '{') {
      i += 2;
      let depth = 1;
      while (i < s.length && depth > 0) {
        const ch = s[i];
        if (ch === '"' || ch === "'") { i = skipString(s, i); continue; }
        if (ch === '`') { i = skipTemplate(s, i); continue; }
        if (ch === '{') depth += 1;
        else if (ch === '}') depth -= 1;
        i += 1;
      }
      continue;
    }
    i += 1;
  }
  return i;
}
function skipComment(s, i) {
  if (s[i + 1] === '/') {
    const nl = s.indexOf('\n', i);
    return nl < 0 ? s.length : nl;
  }
  const end = s.indexOf('*/', i + 2);
  return end < 0 ? s.length : end + 2;
}
function matchBracket(s, openIdx, open, close) {
  let depth = 0;
  let i = openIdx;
  while (i < s.length) {
    const c = s[i];
    if (c === '"' || c === "'") { i = skipString(s, i); continue; }
    if (c === '`') { i = skipTemplate(s, i); continue; }
    if (c === '/' && (s[i + 1] === '/' || s[i + 1] === '*')) { i = skipComment(s, i); continue; }
    if (c === open) depth += 1;
    else if (c === close) { depth -= 1; if (depth === 0) return i; }
    i += 1;
  }
  return -1;
}
function extractFunction(src, name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(', 'g');
  let m;
  while ((m = re.exec(src)) !== null) {
    let start = m.index;
    const before = src.slice(Math.max(0, start - 80), start);
    const am = /(?:^|[^\w$])async\s+$/.exec(before);
    if (am) start -= am[0].length;
    while (start < src.length && /\s/.test(src[start])) start += 1;
    if (!/^(async\s+)?function\s/.test(src.slice(start, start + 40))) continue;
    const parenOpen = src.indexOf('(', start);
    const parenClose = matchBracket(src, parenOpen, '(', ')');
    const braceOpen = src.indexOf('{', parenClose);
    const braceClose = matchBracket(src, braceOpen, '{', '}');
    if (braceClose < 0) continue;
    return src.slice(start, braceClose + 1);
  }
  throw new Error('app.js 里找不到函数 ' + name);
}

const src = fs.readFileSync(process.env.APP_JS_PATH, 'utf8');
const FNS = new Function(
  extractFunction(src, 'isRealDate') + '\n' +
  extractFunction(src, 'todayCst') + '\n' +
  extractFunction(src, 'filterReportDays') + '\n' +
  'return { isRealDate: isRealDate, todayCst: todayCst, filterReportDays: filterReportDays };'
)();

const SOURCE = process.env.SOURCE_ID || 'koreader';
const RULES = JSON.parse(process.env.RULES_JSON);

/* ---------- 「今天」（两边都用同一个，别让时钟差一天） ---------- */
const today = FNS.todayCst();

/** 在 YYYY-MM-DD 上加减天数（按 UTC 走，不受本机时区影响）。 */
function shift(day, n) {
  const p = day.split('-').map(Number);
  const d = new Date(Date.UTC(p[0], p[1] - 1, p[2] + n));
  return d.toISOString().slice(0, 10);
}

/* ---------- 1. todayCst 对拍（含跨 UTC+8 午夜的边界） ---------- */
const TS = [
  '2026-11-25T15:30:00Z',   // 北京时间当天 23:30
  '2026-11-25T16:00:00Z',   // 北京时间**第二天** 00:00（午夜边界）
  '2026-11-25T17:30:00Z',   // 北京时间第二天 01:30
  '2026-11-24T16:00:00Z',   // 北京时间 2026-11-25 当天 00:00
  '2026-02-28T16:00:00Z',   // 平年 2 月 29 日不存在 ⇒ 那天是 2-28
  '2024-02-29T16:00:00Z',   // 闰年
  '2026-12-31T15:59:59Z',   // 年末边界（还差一秒）
  '2026-12-31T16:00:00Z',   // 年末边界（跨过去了）
];
const todayCstCases = TS.map(ts => {
  const ms = Date.parse(ts);
  return { ts: ts, client: FNS.todayCst(ms), worker: V.todayCst(ms) };
});

/* ---------- 2. isRealDate / isDate 对拍 ---------- */
const EDGE_DAYS = [
  '2026-02-30',             // 2 月没有 30 号
  '2026-13-01',             // 没有 13 月
  '2026-11-31',             // 11 月没有 31 号
  '2026-02-29',             // 2026 不是闰年
  '2024-02-29',             // 闰年（真日子）
  '2020-02-29',             // 闰年（真日子）
  '2026-00-10',             // 没有 0 月
  '2026-01-00',             // 没有 0 号
  '2026-1-05',              // 格式不对（少一位）
  '2026-01-05',             // 真日子
  '',                       // 空串
  'x',                      // 垃圾
  '2026-11-25T00:00:00Z',   // 带时间
  '0000-01-01',             // 极早
  '9999-12-31',             // 极晚（真日子，但在未来）
];
const isDateCases = EDGE_DAYS.map(day => ({
  day: day, client: FNS.isRealDate(day), worker: V.isDate(day),
}));

/* ---------- 3. filterReportDays 对拍 ---------- */
const SETS = [];
SETS.push({ name: 'normal', days: { [today]: 10, [shift(today, -1)]: 20, [shift(today, -2)]: 30 } });
SETS.push({ name: 'future', days: { [shift(today, 1)]: 10, [today]: 10 } });
SETS.push({ name: 'fake', days: { '2026-02-30': 10, [today]: 10 } });
SETS.push({ name: 'mixed', days: { '2026-02-30': 10, [shift(today, 1)]: 10, [today]: 10, [shift(today, -1)]: 10 } });
const thirty = {};
for (let i = 0; i < 30; i += 1) thirty[shift(today, -i)] = 10;
SETS.push({ name: 'exactly30', days: thirty });
const thirtyOne = Object.assign({}, thirty);
thirtyOne[shift(today, -30)] = 10;
SETS.push({ name: 'thirtyOne', days: thirtyOne });
const many = {};
for (let i = 0; i < 45; i += 1) many[shift(today, -i)] = 10;
SETS.push({ name: 'fortyFive', days: many });

function workerAccepts(days) {
  return V.validateReport({ v: 1, source: SOURCE, days: days }, RULES, today).ok;
}

const setCases = SETS.map(s => {
  const client = FNS.filterReportDays(s.days);
  const kept = client.days;
  const perDay = {};
  Object.keys(s.days).sort().forEach(day => {
    const one = {};
    one[day] = 1;
    perDay[day] = {
      clientKeeps: Object.prototype.hasOwnProperty.call(kept, day),
      workerAccepts: workerAccepts(one),
    };
  });
  return {
    name: s.name,
    total: Object.keys(s.days).length,
    kept: Object.keys(kept).sort(),
    dropped: client.dropped.map(d => d.day).sort(),
    perDay: perDay,
    workerWholeSet: workerAccepts(s.days),
    workerKeptSet: Object.keys(kept).length ? workerAccepts(kept) : false,
  };
});

process.stdout.write(JSON.stringify({
  today: today,
  todayCst: todayCstCases,
  isDate: isDateCases,
  sets: setCases,
}));
"""


def _node() -> str | None:
    """找 node —— 找不到就优雅跳过（不能让整套测试挂掉）。"""
    return shutil.which("node")


def run_parity(app_js: pathlib.Path, source_id: str) -> dict:
    """在 node 里同时跑客户端的过滤与 Worker 的校验，返回观测结果。"""
    node = _node()
    if not node:
        raise unittest.SkipTest("找不到 node，跳过客户端/服务端对拍")
    with tempfile.TemporaryDirectory() as td:
        probe = pathlib.Path(td) / "probe.js"
        probe.write_text(NODE_PROBE, encoding="utf-8")
        env = dict(os.environ)
        env["APP_JS_PATH"] = str(app_js)
        env["VALIDATE_CJS"] = str(VALIDATE_CJS)
        env["SOURCE_ID"] = source_id
        env["RULES_JSON"] = json.dumps(RULES)
        proc = subprocess.run(
            [node, str(probe)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "对拍探针跑挂了（exit %d）：\n%s" % (proc.returncode, proc.stderr[-4000:])
            )
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise AssertionError("对拍探针的输出不是 JSON：%r / %s" % (proc.stdout[:400], exc))


class ClientFilterParityTest(unittest.TestCase):
    """客户端的日期过滤 ↔ Worker 的校验：逐条对拍。"""

    @classmethod
    def setUpClass(cls):
        if not _node():
            raise unittest.SkipTest("找不到 node，跳过客户端/服务端对拍")
        cls.obs = {}
        for name, app_js, source_id in PAGES:
            cls.obs[name] = run_parity(app_js, source_id)

    def _each_page(self):
        for name, _app_js, _source_id in PAGES:
            yield name, self.obs[name]

    def test_the_two_sides_agree_on_which_day_is_today(self):
        """★ 「今天」必须按 **UTC+8** 判 —— 两边一模一样（含跨午夜的边界）。"""
        for name, obs in self._each_page():
            for row in obs["todayCst"]:
                self.assertEqual(
                    row["client"], row["worker"],
                    "%s 页：%s 这一刻的「今天」客户端说 %s、Worker 说 %s"
                    % (name, row["ts"], row["client"], row["worker"]),
                )

    def test_the_two_sides_agree_on_which_day_is_real(self):
        """★ 「真日期」的口径必须一样（2026-02-30 剔掉、2024-02-29 留着）。"""
        for name, obs in self._each_page():
            for row in obs["isDate"]:
                self.assertEqual(
                    row["client"], row["worker"],
                    "%s 页：%r 是不是真日期，客户端说 %s、Worker 说 %s"
                    % (name, row["day"], row["client"], row["worker"]),
                )

    def test_the_client_keeps_exactly_what_the_worker_accepts(self):
        """★ **逐日**对拍：客户端留着的每一天，服务端都收；客户端剔掉的每一天，服务端都拒。

        （多剔 ⇒ 她的阅读时长被白扔；少剔 ⇒ 毒丸还是进队列。）
        """
        for name, obs in self._each_page():
            for case in obs["sets"]:
                if case["total"] > 30:
                    continue   # 超 30 天是**整份**拒收，单天都收 ⇒ 另有一条用例
                for day, row in case["perDay"].items():
                    self.assertEqual(
                        row["clientKeeps"], row["workerAccepts"],
                        "%s 页（%s）：%s 这一天，客户端%s、服务端%s"
                        % (name, case["name"], day,
                           "留着" if row["clientKeeps"] else "剔掉",
                           "收" if row["workerAccepts"] else "拒"),
                    )

    def test_the_kept_days_are_accepted_as_a_whole_report(self):
        """★ 客户端剔完之后剩下的那份报告，Worker 必须**整份收下**。"""
        for name, obs in self._each_page():
            for case in obs["sets"]:
                if not case["kept"]:
                    continue
                self.assertTrue(
                    case["workerKeptSet"],
                    "%s 页（%s）：客户端剔完之后剩下的 %d 天，服务端不收 ⇒ 剔多了"
                    % (name, case["name"], len(case["kept"])),
                )

    def test_more_than_thirty_days_keeps_the_newest_thirty(self):
        """★ 一次超过 30 天 ⇒ 服务端整份拒收 ⇒ 客户端只带**最近** 30 天。"""
        for name, obs in self._each_page():
            for case in obs["sets"]:
                if case["total"] <= 30:
                    continue
                self.assertEqual(
                    len(case["kept"]), 30,
                    "%s 页（%s）：%d 天里客户端只留了 %d 天（要留 30 天）"
                    % (name, case["name"], case["total"], len(case["kept"])),
                )
                self.assertFalse(
                    case["workerWholeSet"],
                    "%s 页（%s）：%d 天服务端居然整份收了（上限是 30 天）"
                    % (name, case["name"], case["total"]),
                )
                self.assertTrue(
                    case["workerKeptSet"],
                    "%s 页（%s）：客户端留下的 30 天服务端不收 ⇒ 留错了日子"
                    % (name, case["name"]),
                )

    def test_the_dropped_days_are_exactly_the_ones_not_kept(self):
        """★ 页面要说明「剔掉了哪些天」⇒ 剔掉的清单与留下的清单不能打架。"""
        for name, obs in self._each_page():
            for case in obs["sets"]:
                self.assertEqual(
                    sorted(case["dropped"]),
                    sorted(set(case["perDay"]) - set(case["kept"])),
                    "%s 页（%s）：剔掉的清单与留下的清单对不上" % (name, case["name"]),
                )


if __name__ == "__main__":
    unittest.main()
