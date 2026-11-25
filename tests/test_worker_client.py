"""三个客户端页面的契约断言：投递通道的形状、老办法入口、待发队列。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_client

★ 这一版**不再靠「子串出现过没有」糊弄**。
  队列顺序、队列上限、坏数据、隐私模式、老办法兜底、投递头、打开页面自动补发、
  连点两次只发一个 POST —— 全部用 node 把 app.js 里的**原函数**抠出来真跑一遍，
  跑出来的结果交给 Python 断言。
  探针脚本（NODE_PROBE）就嵌在下面，跑的时候临时写进临时目录，**不进仓库**。

★ 变异测试开关：环境变量 ``POM_APP_JS`` 可以指向 app.js 的另一份拷贝。
  审查时把 app.js 改坏（删掉老办法分支 / 把队列顺序反过来 / 去掉补发调用 /
  去掉 token 头 / 去掉连点守卫），用这个变量指过去，测试必须红。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

POM_DIR = ROOT / "site" / "pomodoro"
POM_JS = pathlib.Path(os.environ.get("POM_APP_JS") or (POM_DIR / "app.js"))

NODE = shutil.which("node")


# --------------------------------------------------------------------------
# 探针脚本：在 node 里真跑 app.js 的那几段逻辑，把观测结果吐成 JSON。
# 只读 app.js；抠函数用「花括号配平 + 跳过字符串/注释/模板串」的方式，
# 不改 app.js 一行（它得继续当普通顶层脚本在页面里跑）。
# --------------------------------------------------------------------------
NODE_PROBE = r"""
'use strict';
const fs = require('fs');
const path = require('path');

const APP = process.env.APP_JS_PATH || path.join(__dirname, 'app.js');
const src = fs.readFileSync(APP, 'utf8');

/* ---------- 抠函数 ---------- */
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
function extractFunction(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(', 'g');
  let m;
  while ((m = re.exec(src)) !== null) {
    let start = m.index;
    const before = src.slice(Math.max(0, start - 80), start);
    const am = /(?:^|[^\w$])async\s+$/.exec(before);
    if (am) start -= am[0].length;
    // 去掉前导空白再校验（async 前面可能隔着换行）
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
function constStr(name, dflt) {
  const m = new RegExp('const\\s+' + name + "\\s*=\\s*['\"]([^'\"]*)['\"]").exec(src);
  return m ? m[1] : dflt;
}

/* ---------- 假环境 ---------- */
function fakeStorage(seed) {
  const data = {};
  if (seed) for (const k of Object.keys(seed)) data[k] = seed[k];
  const st = {
    getItem(k) {
      if (st.throwGet) throw new Error('隐私模式：localStorage 读不了');
      return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null;
    },
    setItem(k, v) {
      if (st.throwSet) throw new Error('配额满了');
      data[k] = String(v);
    },
    removeItem(k) { delete data[k]; },
    _data: data,
    throwGet: false,
    throwSet: false,
  };
  return st;
}
function fakeEl() {
  return {
    innerHTML: '', textContent: '', value: '', disabled: false, title: '',
    style: {}, dataset: {}, children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, insertBefore() {}, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, click() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getContext() {
      return {
        clearRect() {}, beginPath() {}, arc() {}, fill() {}, stroke() {}, fillText() {},
        measureText() { return { width: 10 }; }, save() {}, restore() {}, moveTo() {},
        lineTo() {}, setTransform() {}, closePath() {}, fillRect() {}, strokeRect() {},
      };
    },
  };
}
function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }

/* ---------- 沙箱：把抠出来的原函数放进一个可控的作用域里跑 ---------- */
function sandbox(decls, fns, exposed) {
  const body = [
    'var deps = arguments[0];',
    decls,
    fns.join('\n'),
    'return {' + exposed.join(', ') + '};',
  ].join('\n');
  return new Function(body);
}

const PENDING_KEY = constStr('PENDING_KEY', 'pendingIngest');
const QUEUE_FNS = ['pendingQueue', 'savePending', 'enqueueReport', 'shiftPending'].map(extractFunction);
const REPORT = { v: 1, source: 'pom', days: { '2026-11-25': 50 } };

function queueApi(storage) {
  return sandbox(
    ['var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;'].join('\n'),
    QUEUE_FNS,
    ['pendingQueue', 'savePending', 'enqueueReport', 'shiftPending']
  )({ window: { localStorage: storage }, PENDING_KEY: PENDING_KEY });
}

function syncApi(deps) {
  const storage = deps.storage || fakeStorage();
  const win = Object.assign({ localStorage: storage }, deps.window || {});
  return sandbox(
    [
      'var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;', 'var INGESTING = false;',
      'var syncDays = deps.syncDays;', 'var toast = deps.toast;',
      'var ingestAvailable = deps.ingestAvailable;', 'var S = deps.S;', 'var Store = deps.Store;',
      'var renderSync = deps.renderSync;', 'var buildIssueUrl = deps.buildIssueUrl;',
      'var buildReport = deps.buildReport;', 'var postIngest = deps.postIngest;',
    ].join('\n'),
    QUEUE_FNS.concat([extractFunction('doSync')]),
    ['doSync', 'pendingQueue', 'getIngesting: function () { return INGESTING; }']
  )({
    window: win,
    PENDING_KEY: PENDING_KEY,
    syncDays: deps.syncDays,
    toast: deps.toast,
    ingestAvailable: deps.ingestAvailable,
    S: deps.S,
    Store: deps.Store,
    renderSync: deps.renderSync,
    buildIssueUrl: deps.buildIssueUrl,
    buildReport: deps.buildReport,
    postIngest: deps.postIngest,
  });
}

async function main() {
  const obs = {};

  /* ---- 1. 入队：最老在前 ---- */
  const st1 = fakeStorage();
  const q1 = queueApi(st1);
  q1.enqueueReport({ id: 'A' });
  q1.enqueueReport({ id: 'B' });
  obs.queueAfterAB = q1.pendingQueue();

  /* ---- 2. shiftPending 取队首，取完少一条 ---- */
  obs.shifted = q1.shiftPending();
  obs.queueAfterShift = q1.pendingQueue();

  /* ---- 3. toFront 放回队首 ---- */
  q1.enqueueReport({ id: 'C' }, true);
  obs.queueAfterToFront = q1.pendingQueue();

  /* ---- 4. 超过 50 条只留最近 50 条 ---- */
  const st2 = fakeStorage();
  const q2 = queueApi(st2);
  for (let i = 1; i <= 60; i += 1) q2.enqueueReport({ n: i });
  const over = q2.pendingQueue();
  obs.overLimit = {
    len: over.length,
    first: over.length ? over[0].n : null,
    last: over.length ? over[over.length - 1].n : null,
  };

  /* ---- 5. 坏 JSON 当空队列（不许抛） ---- */
  const st3 = fakeStorage({});
  st3.setItem(PENDING_KEY, '这不是 JSON');
  const q3 = queueApi(st3);
  try { obs.badJsonQueue = q3.pendingQueue(); }
  catch (e) { obs.badJsonQueue = { threw: String(e && e.message) }; }

  /* ---- 6. 隐私模式：localStorage 抛异常也不许炸 ---- */
  const st4 = fakeStorage();
  st4.throwGet = true;
  const q4 = queueApi(st4);
  try { obs.throwGetQueue = q4.pendingQueue(); }
  catch (e) { obs.throwGetQueue = { threw: String(e && e.message) }; }
  const st5 = fakeStorage();
  st5.throwSet = true;
  const q5 = queueApi(st5);
  try { q5.enqueueReport({ id: 'X' }); obs.throwSetThrew = null; }
  catch (e) { obs.throwSetThrew = String(e && e.message); }

  /* ---- 7. flushPending：补发失败要放回队首 ---- */
  const st6 = fakeStorage({});
  st6.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  let flushPosts = 0;
  const flushApi = sandbox(
    [
      'var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;',
      'var ingestAvailable = deps.ingestAvailable;', 'var postIngest = deps.postIngest;',
      'var toast = deps.toast;', 'var renderSync = deps.renderSync;',
    ].join('\n'),
    QUEUE_FNS.concat([extractFunction('flushPending')]),
    ['flushPending']
  )({
    window: { localStorage: st6 },
    PENDING_KEY: PENDING_KEY,
    ingestAvailable: () => true,
    postIngest: () => { flushPosts += 1; return Promise.reject(new Error('服务端满了')); },
    toast: () => {},
    renderSync: () => {},
  });
  await flushApi.flushPending();
  obs.flushFail = { postCalls: flushPosts, queue: JSON.parse(st6._data[PENDING_KEY] || '[]') };

  /* ---- 8. flushPending：通道没开 ⇒ 什么都不做 ---- */
  const st7 = fakeStorage({});
  st7.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }]));
  let flushPostsOff = 0;
  const flushApiOff = sandbox(
    [
      'var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;',
      'var ingestAvailable = deps.ingestAvailable;', 'var postIngest = deps.postIngest;',
      'var toast = deps.toast;', 'var renderSync = deps.renderSync;',
    ].join('\n'),
    QUEUE_FNS.concat([extractFunction('flushPending')]),
    ['flushPending']
  )({
    window: { localStorage: st7 },
    PENDING_KEY: PENDING_KEY,
    ingestAvailable: () => false,
    postIngest: () => { flushPostsOff += 1; return Promise.resolve({ ok: true, id: 'x' }); },
    toast: () => {},
    renderSync: () => {},
  });
  await flushApiOff.flushPending();
  obs.flushOff = { postCalls: flushPostsOff, queue: JSON.parse(st7._data[PENDING_KEY] || '[]') };

  /* ---- 9. postIngest：方法、token 头、请求体 ---- */
  let seen = null;
  const postApi = sandbox(
    [
      'var INGEST = deps.INGEST;', 'var INGEST_TIMEOUT_MS = deps.INGEST_TIMEOUT_MS;',
      'var ingestAvailable = deps.ingestAvailable;', 'var fetch = deps.fetch;',
    ].join('\n'),
    [extractFunction('postIngest')],
    ['postIngest']
  )({
    INGEST: { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true },
    INGEST_TIMEOUT_MS: 15000,
    ingestAvailable: () => true,
    fetch: (url, options) => {
      seen = { url: url, options: options };
      return Promise.resolve({
        ok: true, status: 200,
        text: () => Promise.resolve(JSON.stringify({ ok: true, id: 'abc123' })),
      });
    },
  });
  obs.postResult = await postApi.postIngest(REPORT);
  obs.postFetch = {
    url: seen.url,
    method: seen.options.method,
    token: (seen.options.headers || {})['X-Dex-Token'],
    contentType: (seen.options.headers || {})['Content-Type'],
    body: JSON.parse(seen.options.body),
  };

  /* ---- 10. postIngest：服务端说不行 ⇒ 要抛 ---- */
  const postApiBad = sandbox(
    [
      'var INGEST = deps.INGEST;', 'var INGEST_TIMEOUT_MS = deps.INGEST_TIMEOUT_MS;',
      'var ingestAvailable = deps.ingestAvailable;', 'var fetch = deps.fetch;',
    ].join('\n'),
    [extractFunction('postIngest')],
    ['postIngest']
  )({
    INGEST: { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true },
    INGEST_TIMEOUT_MS: 15000,
    ingestAvailable: () => true,
    fetch: () => Promise.resolve({
      ok: false, status: 429,
      text: () => Promise.resolve(JSON.stringify({ ok: false, error: '队列满了' })),
    }),
  });
  try { await postApiBad.postIngest(REPORT); obs.postError = null; }
  catch (e) { obs.postError = String(e && e.message); }

  /* ---- 11. doSync：通道没开 ⇒ 走老办法（开 GitHub 页，不发 POST） ---- */
  const opened = [];
  const toastsOld = [];
  let oldPosts = 0;
  const syncOld = syncApi({
    window: { open: (url) => opened.push(url) },
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: (m) => toastsOld.push(m),
    ingestAvailable: () => false,
    S: () => ({}),
    Store: { write: () => {} },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: () => { oldPosts += 1; return Promise.reject(new Error('通道没开时不该发 POST')); },
  });
  syncOld.doSync();
  await tick();
  obs.oldWay = { opened: opened, postCalls: oldPosts, toasts: toastsOld };

  /* ---- 12. doSync：通道开着 ⇒ 发一个 POST，成功才记同步时间 ---- */
  const postsNew = [];
  const toastsNew = [];
  let writesNew = 0;
  const syncNew = syncApi({
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: (m) => toastsNew.push(m),
    ingestAvailable: () => true,
    S: () => ({}),
    Store: { write: () => { writesNew += 1; } },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: (r) => { postsNew.push(r); return Promise.resolve({ ok: true, id: 'abc123' }); },
  });
  syncNew.doSync();
  await tick();
  obs.newWay = {
    postCalls: postsNew.length,
    report: postsNew[0] || null,
    writes: writesNew,
    toasts: toastsNew,
    queue: syncNew.pendingQueue(),
    ingesting: syncNew.getIngesting(),
  };

  /* ---- 13. doSync：投递失败 ⇒ 报告入队 ---- */
  const postsFail = [];
  const syncFail = syncApi({
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: () => {},
    ingestAvailable: () => true,
    S: () => ({}),
    Store: { write: () => {} },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: (r) => { postsFail.push(r); return Promise.reject(new Error('网络抽风')); },
  });
  syncFail.doSync();
  await tick();
  obs.newWayFail = {
    postCalls: postsFail.length,
    queue: syncFail.pendingQueue(),
    ingesting: syncFail.getIngesting(),
  };

  /* ---- 14. 连点两次同步 ⇒ 只发一个 POST ---- */
  let release = null;
  const gate = new Promise(function (res) { release = res; });
  const postsDouble = [];
  const toastsDouble = [];
  const syncDouble = syncApi({
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: (m) => toastsDouble.push(m),
    ingestAvailable: () => true,
    S: () => ({}),
    Store: { write: () => {} },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: (r) => { postsDouble.push(r); return gate; },
  });
  syncDouble.doSync();
  syncDouble.doSync();
  obs.doubleClick = {
    postCalls: postsDouble.length,
    ingesting: syncDouble.getIngesting(),
    toasts: toastsDouble.slice(),
  };
  release({ ok: true, id: 'abc123' });
  await tick();
  obs.doubleClickAfter = { ingesting: syncDouble.getIngesting() };

  /* ---- 15. 页面打开（sources.json 到了）⇒ 自动补发 ---- */
  const fetchCalls = [];
  let flushCalls = 0;
  const loadApi = sandbox(
    [
      'var fetch = deps.fetch;', 'var $ = deps.$;', 'var POKEMON = null;', 'var LIST_OK = false;',
      'var SERVER_OWNED = null;', 'var UNIT_PER_CREDIT = 25;', 'var SOURCE_ID = deps.SOURCE_ID;',
      'var CDN = deps.CDN;', 'var INGEST = null;', 'var renderTop = deps.renderTop;',
      'var renderDex = deps.renderDex;', 'var renderSync = deps.renderSync;',
      'var flushPending = deps.flushPending;',
    ].join('\n'),
    [extractFunction('loadList')],
    ['loadList', 'getIngest: function () { return INGEST; }']
  )({
    fetch: (url) => {
      const u = String(url);
      fetchCalls.push(u);
      let data = {};
      if (u.indexOf('pokedex-lite.json') >= 0) data = { pokemon: [] };
      else if (u.indexOf('dex.json') >= 0) data = { collected: [] };
      else if (u.indexOf('sources.json') >= 0) {
        data = {
          sources: { pom: { per: 25 } },
          ingest: { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true },
        };
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
    },
    $: () => fakeEl(),
    renderTop: () => {},
    renderDex: () => {},
    renderSync: () => {},
    flushPending: () => { flushCalls += 1; return Promise.resolve(); },
    SOURCE_ID: 'pom',
    CDN: 'https://cdn.example/',
  });
  loadApi.loadList();
  await tick();
  obs.loadList = {
    flushCalls: flushCalls,
    ingestUrl: (loadApi.getIngest() || {}).url || null,
    fetchCalls: fetchCalls.length,
  };

  process.stdout.write(JSON.stringify(obs));
}

main().catch(function (e) {
  process.stderr.write('探针跑挂了：' + String((e && e.stack) || e) + '\n');
  process.exitCode = 1;
});
"""


def run_probe(app_js: pathlib.Path) -> dict:
    """在 node 里真跑一遍 app.js 的那几段逻辑，返回观测结果。"""
    if not NODE:
        raise unittest.SkipTest("这台机没有 node，跑不了行为探针")
    with tempfile.TemporaryDirectory() as td:
        probe = pathlib.Path(td) / "probe.js"
        probe.write_text(NODE_PROBE, encoding="utf-8")
        env = dict(os.environ)
        env["APP_JS_PATH"] = str(app_js)
        proc = subprocess.run(
            [NODE, str(probe)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "行为探针跑挂了（exit %d）：\n%s" % (proc.returncode, proc.stderr[-4000:])
            )
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise AssertionError("行为探针的输出不是 JSON：%r / %s" % (proc.stdout[:400], exc))


class PomodoroIngestBehaviorTest(unittest.TestCase):
    """番茄钟页的投递通道：**真跑一遍** app.js 的原函数，看行为对不对。"""

    @classmethod
    def setUpClass(cls):
        cls.obs = run_probe(POM_JS)

    # ---------------- 队列 ----------------

    def test_the_queue_keeps_the_oldest_first(self):
        """先入 A 再入 B ⇒ 队列是 [A, B]（最老在前）。"""
        self.assertEqual(
            self.obs.get("queueAfterAB"),
            [{"id": "A"}, {"id": "B"}],
            "队列必须是最老在前：先进去的排前面",
        )

    def test_shift_pending_takes_the_head(self):
        """shiftPending 取的是队首，取完队列少一条。"""
        self.assertEqual(self.obs.get("shifted"), {"id": "A"}, "shiftPending 要取队首")
        self.assertEqual(
            self.obs.get("queueAfterShift"),
            [{"id": "B"}],
            "取走队首之后队列要少一条",
        )

    def test_enqueue_to_front_puts_the_report_back_at_the_head(self):
        """补发失败时放回队首（不是队尾）。"""
        self.assertEqual(
            self.obs.get("queueAfterToFront"),
            [{"id": "C"}, {"id": "B"}],
            "toFront 的报告要放回队首 —— 放队尾会让老报告越攒越晚才交上去",
        )

    def test_the_queue_keeps_only_the_latest_50(self):
        """超过 50 条 ⇒ 只留最近 50 条（slice(-50)）。"""
        over = self.obs.get("overLimit") or {}
        self.assertEqual(over.get("len"), 50, "队列上限是 50 条")
        self.assertEqual(over.get("first"), 11, "留的应该是**最近** 50 条（第 11~60 条）")
        self.assertEqual(over.get("last"), 60, "最后一条是最新的那条")

    def test_broken_localstorage_data_is_treated_as_an_empty_queue(self):
        """localStorage 里是坏 JSON ⇒ 当空队列，不许抛。"""
        self.assertEqual(
            self.obs.get("badJsonQueue"),
            [],
            "坏数据要当空队列处理（不能炸、也不能把坏数据原样发出去）",
        )

    def test_private_mode_localstorage_does_not_crash_the_page(self):
        """隐私模式下 localStorage 抛异常 ⇒ 读队列当空、写队列吞掉。"""
        self.assertEqual(
            self.obs.get("throwGetQueue"),
            [],
            "读不到 localStorage 时要当空队列",
        )
        self.assertIsNone(
            self.obs.get("throwSetThrew"),
            "localStorage 写不进去时不能把异常抛出去",
        )

    # ---------------- 补发 ----------------

    def test_flush_puts_a_failed_report_back_at_the_head(self):
        """补发失败 ⇒ 放回队首（不是队尾）。"""
        flush = self.obs.get("flushFail") or {}
        self.assertEqual(flush.get("postCalls"), 1, "补发失败后要停手（别把队列里的全试一遍）")
        self.assertEqual(
            flush.get("queue"),
            [{"id": "X"}, {"id": "Y"}],
            "补发失败的那条要放回**队首**",
        )

    def test_flush_does_nothing_when_the_channel_is_off(self):
        """通道没开 ⇒ 不补发（队列原封不动）。"""
        flush = self.obs.get("flushOff") or {}
        self.assertEqual(flush.get("postCalls"), 0, "通道没开时不该发任何请求")
        self.assertEqual(flush.get("queue"), [{"id": "X"}], "通道没开时队列要原封不动")

    # ---------------- 投递本身 ----------------

    def test_the_post_carries_the_ingest_token_header(self):
        """投递要带 token 头、方法是 POST、请求体就是那份报告。"""
        post = self.obs.get("postFetch") or {}
        self.assertEqual(post.get("method"), "POST", "投递要用 POST")
        self.assertEqual(
            post.get("token"),
            "tok-abc",
            "投递必须带 X-Dex-Token 头（没这个头服务端会拒）",
        )
        self.assertEqual(post.get("contentType"), "application/json", "请求头要声明 JSON")
        self.assertEqual(
            post.get("body"),
            {"v": 1, "source": "pom", "days": {"2026-11-25": 50}},
            "请求体就是那份报告",
        )

    def test_a_rejected_post_raises(self):
        """服务端拒收（队列满了）⇒ postIngest 要抛，调用方才知道要排队。"""
        self.assertEqual(self.obs.get("postError"), "队列满了", "服务端拒收时要把错误抛出来")

    # ---------------- 点同步 ----------------

    def test_the_old_way_is_used_when_the_channel_is_off(self):
        """通道没开 ⇒ 打开 GitHub 的新建 issue 页，**一个 POST 都不许发**。"""
        old = self.obs.get("oldWay") or {}
        self.assertEqual(
            old.get("opened"),
            ["https://github.com/lilia-shound/pokemon-dex/issues/new?title=x"],
            "通道没开时要点开 GitHub 的新建 issue 页（老办法）",
        )
        self.assertEqual(old.get("postCalls"), 0, "通道没开时不许发投递请求")
        self.assertTrue(old.get("toasts"), "老办法要点一下提示")

    def test_the_new_way_posts_once_when_the_channel_is_on(self):
        """通道开着 ⇒ 发一个 POST，成功才记同步时间。"""
        new = self.obs.get("newWay") or {}
        self.assertEqual(new.get("postCalls"), 1, "点一次同步只该发一个 POST")
        self.assertEqual(new.get("report"), {"v": 1, "source": "pom", "days": {"2026-11-25": 50}},
                         "投递的要是那份报告")
        self.assertEqual(new.get("writes"), 1, "成功之后要写一次存储")
        self.assertEqual(new.get("queue"), [], "成功了就不该留队列")
        self.assertFalse(new.get("ingesting"), "投递完要把「正在投递」的标志清掉")

    def test_a_failed_post_queues_the_report(self):
        """投递失败 ⇒ 报告入队（等下次补发）。"""
        fail = self.obs.get("newWayFail") or {}
        self.assertEqual(fail.get("postCalls"), 1, "失败前确实发了一次")
        self.assertEqual(
            fail.get("queue"),
            [{"v": 1, "source": "pom", "days": {"2026-11-25": 50}}],
            "投递失败的报告要存进待发队列",
        )
        self.assertFalse(fail.get("ingesting"), "失败之后也要把「正在投递」的标志清掉")

    def test_clicking_sync_twice_sends_only_one_post(self):
        """连点两次 ⇒ 只发一个 POST（workers.dev 在国内慢，很容易点第二下）。"""
        double = self.obs.get("doubleClick") or {}
        self.assertEqual(
            double.get("postCalls"),
            1,
            "连点两次同步只能发一个 POST —— 两个都失败的话同一份报告会入队两次",
        )
        self.assertTrue(double.get("ingesting"), "投递期间应该有个「正在投递」的标志")
        self.assertTrue(
            any("稍等" in t for t in double.get("toasts") or []),
            "重复点击要给她一句提示（现在的提示：%r）" % (double.get("toasts"),),
        )
        after = self.obs.get("doubleClickAfter") or {}
        self.assertFalse(after.get("ingesting"), "投递结束后要清掉标志，否则她再也点不动")

    # ---------------- 页面打开 ----------------

    def test_the_page_flushes_the_queue_when_it_opens(self):
        """页面打开（sources.json 到了）⇒ 自动补发。"""
        load = self.obs.get("loadList") or {}
        self.assertEqual(load.get("flushCalls"), 1, "页面打开时要补发一次")
        self.assertEqual(
            load.get("ingestUrl"),
            "https://worker.example/ingest",
            "ingest 配置要从 sources.json 顶层读（不新增网络请求）",
        )


class PomodoroIngestContractTest(unittest.TestCase):
    """源码层面的契约断言（不依赖 node，作为行为测试的兜底）。"""

    @classmethod
    def setUpClass(cls):
        cls.js = POM_JS.read_text(encoding="utf-8")

    def test_the_page_posts_to_the_ingest_endpoint(self):
        self.assertIn("postIngest", self.js, "要有 postIngest")
        self.assertIn("X-Dex-Token", self.js, "要带 ingest token 头")
        self.assertIn("method: 'POST'", self.js, "要用 POST")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置来自**已经 fetch 的** sources.json ⇒ 不许新增网络请求。"""
        self.assertTrue(
            re.search(r"d\.ingest|\['ingest'\]", self.js) is not None,
            "要从 sources.json 顶层读 ingest 段",
        )
        # 页面本来就有 3 个 fetch（pokedex-lite.json / dex.json / sources.json），
        # 加了投递之后最多 4 个 —— **不许再多来一个注册表请求**
        self.assertLessEqual(
            len(re.findall(r"fetch\(", self.js)),
            4,
            "番茄钟页的网络请求不该变多",
        )

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        """老办法不许删 —— workers.dev 在国内可能抽风。"""
        self.assertIn("buildIssueUrl", self.js, "老办法的 URL 构造要留着")
        self.assertIn("issues/new", self.js, "老办法的 GitHub 链接要留着")
        self.assertIn("syncOldWay", self.js, "老办法链接要有固定的 id")
        self.assertIn("ingestAvailable", self.js, "通道没开时要能退回老办法")

    def test_the_page_queues_failed_reports(self):
        """交不上去要存下来，下次打开自动补发。"""
        self.assertIn("pendingIngest", self.js, "要有待发队列")
        self.assertIn("flushPending", self.js, "页面打开时要补发")
        self.assertIn("localStorage", self.js, "待发队列要落 localStorage")

    def test_the_old_way_branch_comes_before_the_post(self):
        """doSync 里「通道没开 ⇒ 走老办法」的判断必须在 postIngest **之前**。"""
        start = self.js.index("function doSync(")
        body = self.js[start:self.js.index("\n}", start)]
        guard = body.find("!ingestAvailable()")
        post = body.find("postIngest(")
        self.assertGreaterEqual(guard, 0, "doSync 里要有「通道没开」的分支")
        self.assertGreater(post, guard, "「退回老办法」的判断必须在投递之前")
        self.assertIn("window.open(buildIssueUrl()", body, "老办法要真的打开 GitHub 页面")

    def test_the_queue_keeps_the_newest_50(self):
        """队列上限：只留最近 50 条（slice(-50)），不是最老 50 条。"""
        self.assertIn(
            "slice(-50)",
            self.js,
            "待发队列超限时只留**最近** 50 条（slice(-50)）",
        )

    def test_the_queue_can_put_a_report_back_at_the_head(self):
        """补发失败时要放回队首（unshift），不能只有 push。"""
        start = self.js.index("function enqueueReport(")
        body = self.js[start:self.js.index("\n}", start)]
        self.assertIn("unshift", body, "enqueueReport 要能把报告放回队首")
        self.assertIn("push", body, "enqueueReport 也要能把报告排到队尾")

    def test_the_page_flushes_the_queue_after_loading_the_registry(self):
        """页面打开时要调 flushPending（配置到了就补发）。"""
        start = self.js.index("function loadList(")
        body = self.js[start:self.js.index("\n}", start)]
        self.assertIn("flushPending()", body, "sources.json 到了之后要补发待发队列")

    def test_the_page_has_an_in_flight_guard(self):
        """连点两次同步 ⇒ 只发一个 POST。"""
        self.assertIn("let INGESTING", self.js, "要有一个「正在投递」的标志")
        start = self.js.index("function doSync(")
        body = self.js[start:self.js.index("\n}", start)]
        self.assertIn("if (INGESTING)", body, "doSync 入口要判重")
        self.assertIn("INGESTING = true", body, "发投递之前要把标志置上")
        self.assertIn("INGESTING = false", body, "finally 里要把标志清掉")
        self.assertIn("finally", body, "标志必须在 finally 里清（成功/失败/异常都要清）")

    @unittest.skipUnless(NODE, "这台机没有 node")
    def test_the_page_script_is_valid_javascript(self):
        """app.js 得是能跑的脚本（不能改坏成模块或半截代码）。"""
        proc = subprocess.run(
            [NODE, "--check", str(POM_JS)], capture_output=True, text=True
        )
        self.assertEqual(
            proc.returncode, 0, "app.js 语法有问题：\n%s" % proc.stderr[-2000:]
        )


if __name__ == "__main__":
    unittest.main()
