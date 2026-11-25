"""三个客户端页面的契约断言：投递通道的形状、老办法入口、待发队列。

跑法：

    python3 -B run_tests.py            # 整套
    python3 -B -m tests.test_worker_client

★ 这一版**不再靠「子串出现过没有」糊弄**。
  队列顺序、队列上限、坏数据、隐私模式、老办法兜底、投递头、打开页面自动补发、
  连点两次只发一个 POST —— 全部用 node 把 app.js 里的**原函数**抠出来真跑一遍，
  跑出来的结果交给 Python 断言。
  探针脚本（NODE_PROBE）就嵌在下面，跑的时候临时写进临时目录，**不进仓库**。

★ 变异测试开关：环境变量 ``POM_APP_JS`` / ``WORDS_APP_JS`` / ``KO_APP_JS`` 可以指向 app.js 的
  另一份拷贝；``WORDS_SITE_DIR`` / ``KO_SITE_DIR`` 可以指向整个 site/ 的另一份拷贝（浏览器那部分
  是从 site/ 里取文件的）。审查时把 app.js 改坏（删掉老办法分支 / 把队列顺序
  反过来 / 去掉补发调用 / 去掉 token 头 / 去掉连点守卫 / 去掉调用处的 .catch /
  把两种通道的提示语改成一样 / 每次都重建 DOM / 少一次「结果提示作废后的重画」），用这些变量指过去，测试必须红。
"""

from __future__ import annotations

import asyncio
import datetime
import http.server
import json
import os
import pathlib
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest
import urllib.parse

from tests import browser_path

ROOT = pathlib.Path(__file__).resolve().parent.parent

POM_DIR = ROOT / "site" / "pomodoro"
POM_JS = pathlib.Path(os.environ.get("POM_APP_JS") or (POM_DIR / "app.js"))

# 背单词页（site/words/）
WORDS_DIR = ROOT / "site" / "words"
WORDS_JS = pathlib.Path(os.environ.get("WORDS_APP_JS") or (WORDS_DIR / "app.js"))
WORDS_HTML = WORDS_DIR / "index.html"
# 变异测试用：把整个 site/ 换掉（浏览器那部分是从 site/ 里取文件的）
WORDS_SITE_DIR = pathlib.Path(os.environ.get("WORDS_SITE_DIR") or (ROOT / "site"))

# KOReader 落地页（site/koreader/）
KO_DIR = ROOT / "site" / "koreader"
KO_JS = pathlib.Path(os.environ.get("KO_APP_JS") or (KO_DIR / "app.js"))
KO_HTML = KO_DIR / "index.html"
# 变异测试用：把整个 site/ 换掉（浏览器那部分是从 site/ 里取文件的）
KO_SITE_DIR = pathlib.Path(os.environ.get("KO_SITE_DIR") or (ROOT / "site"))

NODE = shutil.which("node")

# 浏览器（Playwright）—— 别写死版本号：沙箱刷新会换 Playwright 版本
try:
    CHROME = browser_path.chromium_path()
except Exception:  # pragma: no cover - 环境问题
    CHROME = ""

try:  # playwright 可能没装 —— 不能让整套测试挂掉
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_PLAYWRIGHT = False

try:  # 异步接口 —— 测「延迟响应」「连点」必须用它（同步版会串线程）
    from playwright.async_api import async_playwright

    HAVE_ASYNC_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 环境问题
    HAVE_ASYNC_PLAYWRIGHT = False


# --------------------------------------------------------------------------
# 源码切片的小工具
# --------------------------------------------------------------------------

def _skip_string(js: str, i: int) -> int:
    """跳过一段字符串（'...' 或 "..."），返回结束位置。"""
    quote = js[i]
    i += 1
    while i < len(js):
        if js[i] == "\\":
            i += 2
            continue
        if js[i] == quote:
            return i + 1
        i += 1
    return i


def _skip_template(js: str, i: int) -> int:
    """跳过一段模板串（`...`），``${...}`` 里的嵌套花括号也算进去。"""
    i += 1
    while i < len(js):
        if js[i] == "\\":
            i += 2
            continue
        if js[i] == "`":
            return i + 1
        if js[i:i + 2] == "${":
            depth = 1
            i += 2
            while i < len(js) and depth > 0:
                c = js[i]
                if c in "'\"":
                    i = _skip_string(js, i)
                    continue
                if c == "`":
                    i = _skip_template(js, i)
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                i += 1
            continue
        i += 1
    return i


def _function_body(js: str, header: str) -> str:
    """抠出某个函数的**完整**函数体（花括号配平，跳过字符串与注释）。

    ★ 为什么不用 `js[start:js.index("\\n}", start)]` 那种切法：
      背单词页的 app.js 整个包在 IIFE 里 ⇒ 所有右花括号都**缩进**着 ⇒
      `\\n}` 根本找不到 ⇒ 切片会一路切到**文件末尾** ⇒ 切片里什么都有 ⇒
      「切片里有 .catch(」这种断言会被**别处**满足（错因满足：
      注册表自己的 `.catch(err => {` 也在切片里）。

    :param header: 函数头的特征串（例如 "function loadRegistry("）
    :returns: 从函数头到配平的右花括号那一整段源码
    """
    start = js.index(header)
    i = js.index("{", start)
    depth = 0
    n = len(js)
    while i < n:
        if js[i:i + 2] == "//":
            nl = js.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if js[i:i + 2] == "/*":
            end = js.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        c = js[i]
        if c in "'\"":
            i = _skip_string(js, i)
            continue
        if c == "`":
            i = _skip_template(js, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
        i += 1
    raise AssertionError("函数体没配平：%s" % header)


def _make_server(directory: pathlib.Path):
    """在 directory 上起一个静态服务器（端口 0 = 随机端口，免跟别的测试撞车）。"""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):  # 别把日志糊满终端
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd



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
const DATE_FNS = ['isRealDate', 'todayCst', 'filterReportDays'].map(extractFunction);
const REPORT = { v: 1, source: 'pom', days: { '2026-11-25': 50 } };

function queueApi(storage) {
  return sandbox(
    ['var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;'].join('\n'),
    QUEUE_FNS,
    ['pendingQueue', 'savePending', 'enqueueReport', 'shiftPending']
  )({ window: { localStorage: storage }, PENDING_KEY: PENDING_KEY });
}

/** flushPending 的沙箱（补发用）。 */
function makeFlushApi(storage, deps) {
  return sandbox(
    [
      'var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;',
      'var STORAGE_OK = true;',
      'var ingestAvailable = deps.ingestAvailable;', 'var postIngest = deps.postIngest;',
      'var setResult = deps.setResult;',
    ].join('\n'),
    QUEUE_FNS.concat([extractFunction('flushMsg'), extractFunction('flushPending')]),
    ['flushPending']
  )({
    window: { localStorage: storage },
    PENDING_KEY: PENDING_KEY,
    ingestAvailable: deps.ingestAvailable,
    postIngest: deps.postIngest,
    setResult: deps.setResult,
  });
}

function syncApi(deps) {
  const storage = deps.storage || fakeStorage();
  const win = Object.assign({ localStorage: storage }, deps.window || {});
  return sandbox(
    [
      'var window = deps.window;', 'var PENDING_KEY = deps.PENDING_KEY;', 'var INGESTING = false;',
      'var REGISTRY_LOADED = deps.REGISTRY_LOADED !== false;',
      'var syncDays = deps.syncDays;', 'var toast = deps.toast;',
      'var setResult = deps.setResult;',
      'var ingestAvailable = deps.ingestAvailable;', 'var S = deps.S;', 'var Store = deps.Store;',
      'var renderSync = deps.renderSync;', 'var buildIssueUrl = deps.buildIssueUrl;',
      'var buildReport = deps.buildReport;', 'var postIngest = deps.postIngest;',
    ].join('\n'),
    QUEUE_FNS.concat([extractFunction('doSync')]),
    ['doSync', 'pendingQueue', 'getIngesting: function () { return INGESTING; }']
  )({
    window: win,
    PENDING_KEY: PENDING_KEY,
    REGISTRY_LOADED: deps.REGISTRY_LOADED,
    syncDays: deps.syncDays,
    toast: deps.toast,
    setResult: deps.setResult || deps.toast,
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

  /* ---- 16. ★ 客户端提前拦：未来的日期 / 假日期 / 超过 30 天 ---- */
  const filterApi = sandbox(
    [],
    DATE_FNS,
    ['filterReportDays', 'isRealDate', 'todayCst']
  )({});
  const many = {};
  for (let i = 0; i < 35; i += 1) {
    const d = new Date(Date.UTC(2026, 8, 27 + i));   // 2026-09-27 起 35 天（都是真日子）
    many[d.toISOString().slice(0, 10)] = 10;
  }
  const filtered = filterApi.filterReportDays(Object.assign({}, many, {
    '2026-02-30': 10,      // 假日期（2 月没有 30 号）
    '2099-01-01': 10,      // 未来的日期
  }));
  obs.filter = {
    kept: Object.keys(filtered.days).sort(),
    dropped: filtered.dropped.map(d => d.day).sort(),
    keptCount: Object.keys(filtered.days).length,
  };

  /* ---- 17. ★ 要上报的天里，未来的 / 假的日子要剔掉 ---- */
  const syncApi2 = sandbox(
    ['var S = deps.S;', 'var MAX_SYNC_DAYS = deps.MAX_SYNC_DAYS;'].join('\n'),
    DATE_FNS.concat([extractFunction('syncDaysRaw'), extractFunction('syncDays')]),
    ['syncDays']
  )({
    S: () => ({
      days: {
        '2026-11-25': { minutes: 50, poms: 2 },
        '2099-01-01': { minutes: 30, poms: 1 },   // 未来
        '2026-02-30': { minutes: 30, poms: 1 },   // 假日期
      },
    }),
    MAX_SYNC_DAYS: 30,
  });
  obs.syncFilter = { days: syncApi2.syncDays() };

  /* ---- 18. ★ 毒丸报告（服务端 400 拒收）不许堵住后面的报告 ---- */
  const stPoison = fakeStorage({});
  stPoison.setItem(PENDING_KEY, JSON.stringify([
    { v: 1, source: 'pom', days: { '2099-01-01': 30 } },   // 服务端永远拒收
    { v: 1, source: 'pom', days: { '2026-11-20': 50 } },   // 这条是好的
  ]));
  const poisonPosts = [];
  const poisonResults = [];
  const poisonApi = makeFlushApi(stPoison, {
    ingestAvailable: () => true,
    postIngest: (r) => {
      poisonPosts.push(r);
      const err = new Error('日期在未来：2099-01-01');
      err.status = 400;
      err.permanent = true;                       // ★ 4xx ⇒ 永久失败
      return Promise.reject(err);
    },
    setResult: (m) => poisonResults.push(m),
  });
  await poisonApi.flushPending();
  obs.poison = {
    posts: poisonPosts.length,
    posted: poisonPosts,
    queue: JSON.parse(stPoison._data[PENDING_KEY] || '[]'),
    results: poisonResults,
  };

  /* ---- 18. ★ 注册表还没到就点「同步」⇒ 什么都不做（不许降级成老办法） ---- */
  const raceOpened = [];
  const raceToasts = [];
  let racePosts = 0;
  const raceSync = syncApi({
    REGISTRY_LOADED: false,               // ★ 注册表还没回来
    window: { open: (url) => raceOpened.push(url) },
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: (m) => raceToasts.push(m),
    ingestAvailable: () => true,           // 通道其实是开着的（配置到了才知道）
    S: () => ({}),
    Store: { write: () => {} },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: () => { racePosts += 1; return Promise.resolve({ ok: true }); },
  });
  raceSync.doSync();
  await tick();
  obs.registryRace = {
    postCalls: racePosts,
    opened: raceOpened.slice(),
    toasts: raceToasts.slice(),
  };

  /* ---- 19. ★ 存储写不进去时，别再谎称「已存着」 ---- */
  const stNoWrite = fakeStorage();
  stNoWrite.throwSet = true;
  const noWriteQueue = queueApi(stNoWrite);
  const noWriteSaved = noWriteQueue.enqueueReport({ id: 'X' });
  const noWriteToasts = [];
  const syncNoWrite = syncApi({
    storage: stNoWrite,                       // ★ 这台设备的浏览器不让存
    syncDays: () => ({ '2026-11-25': 50 }),
    toast: (m) => noWriteToasts.push(m),
    ingestAvailable: () => true,
    S: () => ({}),
    Store: { write: () => {} },
    renderSync: () => {},
    buildIssueUrl: () => 'https://github.com/lilia-shound/pokemon-dex/issues/new?title=x',
    buildReport: () => REPORT,
    postIngest: () => Promise.reject(new Error('网络抽风')),
  });
  syncNoWrite.doSync();
  await tick();
  obs.storageRefused = {
    enqueueReturned: noWriteSaved,
    toasts: noWriteToasts.slice(),
  };

  /* ---- 20. ★ 存储读不了时，补发要说真话 ---- */
  const stNoRead = fakeStorage({});
  stNoRead.throwGet = true;
  const noReadResults = [];
  const flushNoRead = makeFlushApi(stNoRead, {
    ingestAvailable: () => true,
    postIngest: () => Promise.resolve({ ok: true }),
    setResult: (m) => noReadResults.push(m),
  });
  await flushNoRead.flushPending();
  obs.storageUnreadable = { results: noReadResults.slice() };

  /* ---- 21. ★ 暂时性失败（5xx）⇒ 队列留着，后面的先不发 ---- */
  const stTemp = fakeStorage({});
  stTemp.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  const tempPosts = [];
  const tempResults = [];
  const tempApi = makeFlushApi(stTemp, {
    ingestAvailable: () => true,
    postIngest: (r) => {
      tempPosts.push(r);
      const err = new Error('服务端满了');
      err.status = 503;                           // ★ 5xx ⇒ 暂时失败
      return Promise.reject(err);
    },
    setResult: (m) => tempResults.push(m),
  });
  await tempApi.flushPending();
  obs.tempFail = {
    posts: tempPosts.length,
    queue: JSON.parse(stTemp._data[PENDING_KEY] || '[]'),
    results: tempResults,
  };

  process.stdout.write(JSON.stringify(obs));
}

main().catch(function (e) {
  process.stderr.write('探针跑挂了：' + String((e && e.stack) || e) + '\n');
  process.exitCode = 1;
});
"""


# --------------------------------------------------------------------------
# 背单词页（site/words/）的探针。
#
# ★ 这一页的 app.js 是 **IIFE** —— 番茄钟页那种「在 node 里抠函数重跑」的路子
#   在这儿不合适（抠出来的是**重拼的函数**，不是页面上真在跑的那几个）。
#   ⇒ 改成**直接 require 页面那份 app.js**：
#   跑的就是页面真正在跑的那几个函数（state 也是页面上那个 state）。
#   页面里的 DOM 由 `ui.hint` / `ui.render` 两个口子顶掉，
#   网络由 global.fetch 顶掉，本机存储由 global.window.localStorage 顶掉。
# --------------------------------------------------------------------------
WORDS_NODE_PROBE = r"""
'use strict';
const path = require('path');

const APP = process.env.APP_JS_PATH || path.join(__dirname, 'app.js');

/* ---------- 假件 ---------- */
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

let storage = fakeStorage();
const opened = [];
global.window = {
  get localStorage() { return storage; },
  open: function (url) { opened.push(url); },
};

const api = require(APP);
const PENDING_KEY = api.PENDING_KEY;
const REPORT = { v: 1, source: 'words', days: { '2026-11-25': 150 } };
const INGEST = { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true };

/* 提示 / 重画：页面里是 DOM，这里换成收集器 */
const hints = [];
let renders = 0;
api.ui.hint = function (t) { hints.push(String(t)); };
api.ui.render = function () { renders += 1; };

/* 网络：页面里是 fetch，这里换成假件 */
const fetchCalls = [];
let fetchImpl = null;
global.fetch = function (url, opts) {
  fetchCalls.push({ url: String(url), options: opts || {} });
  return fetchImpl(url, opts);
};

function okResponse(payload) {
  return Promise.resolve({
    ok: true, status: 200,
    text: function () { return Promise.resolve(JSON.stringify(payload || { ok: true })); },
  });
}

function reset(over) {
  storage = fakeStorage();
  opened.length = 0;
  hints.length = 0;
  renders = 0;
  fetchCalls.length = 0;
  fetchImpl = null;
  api.state.days = {};
  api.state.draft = null;
  api.state.ingest = null;
  api.state.lastSubmit = null;
  api.state.registryLoaded = true;      // 默认「注册表已经到了」（单独测竞态时再关掉）
  api.state.cap = 500;
  api.state.per = 100;
  api.state.enabled = true;
  if (over) over();
}

function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }

async function main() {
  const obs = {};

  /* ---- 1. 入队：最老在前 ---- */
  reset();
  api.enqueueReport({ id: 'A' });
  api.enqueueReport({ id: 'B' });
  obs.queueAfterAB = api.pendingQueue();

  /* ---- 2. shiftPending 取队首，取完少一条 ---- */
  obs.shifted = api.shiftPending();
  obs.queueAfterShift = api.pendingQueue();

  /* ---- 3. toFront 放回队首 ---- */
  api.enqueueReport({ id: 'C' }, true);
  obs.queueAfterToFront = api.pendingQueue();

  /* ---- 4. 超过 50 条只留最近 50 条 ---- */
  reset();
  for (let i = 1; i <= 60; i += 1) api.enqueueReport({ n: i });
  const over = api.pendingQueue();
  obs.overLimit = {
    len: over.length,
    first: over.length ? over[0].n : null,
    last: over.length ? over[over.length - 1].n : null,
  };

  /* ---- 5. 坏 JSON 当空队列（不许抛） ---- */
  reset();
  storage.setItem(PENDING_KEY, '这不是 JSON');
  try { obs.badJsonQueue = api.pendingQueue(); }
  catch (e) { obs.badJsonQueue = { threw: String(e && e.message) }; }

  /* ---- 6. 隐私模式：localStorage 抛异常也不许炸 ---- */
  reset();
  storage.throwGet = true;
  try { obs.throwGetQueue = api.pendingQueue(); }
  catch (e) { obs.throwGetQueue = { threw: String(e && e.message) }; }
  storage.throwGet = false;
  storage.throwSet = true;
  try { api.enqueueReport({ id: 'X' }); obs.throwSetThrew = null; }
  catch (e) { obs.throwSetThrew = String(e && e.message); }

  /* ---- 7. ★ 队列键按源分开：别页（番茄钟）的队列不许被碰到 ---- */
  reset();
  storage.setItem('pendingIngest', JSON.stringify([{ id: 'POM' }]));
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'WORDS' }]));
  obs.isolated = {
    key: PENDING_KEY,
    own: api.pendingQueue(),
    other: JSON.parse(storage._data['pendingIngest']),
  };
  api.enqueueReport({ id: 'WORDS2' });
  obs.isolated.otherAfter = JSON.parse(storage._data['pendingIngest']);
  obs.isolated.ownAfter = api.pendingQueue();

  /* ---- 8. postIngest：方法、token 头、请求体 ---- */
  reset();
  api.state.ingest = INGEST;
  let seen = null;
  fetchImpl = function (url, opts) {
    seen = { url: String(url), options: opts || {} };
    return okResponse({ ok: true, id: 'abc123' });
  };
  obs.postResult = await api.postIngest(REPORT);
  obs.postFetch = {
    url: seen.url,
    method: seen.options.method,
    token: (seen.options.headers || {})['X-Dex-Token'],
    contentType: (seen.options.headers || {})['Content-Type'],
    body: JSON.parse(seen.options.body),
  };

  /* ---- 9. postIngest：服务端说不行 ⇒ 要抛 ---- */
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 429,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '队列满了' })); },
    });
  };
  try { await api.postIngest(REPORT); obs.postError = null; }
  catch (e) { obs.postError = String(e && e.message); }

  /* ---- 10. 通道没开 ⇒ 走老办法（开 GitHub 页，一个 POST 都不许发） ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  fetchImpl = function () { throw new Error('通道没开时不该发请求'); };
  await api.doSubmit();
  obs.oldWay = {
    opened: opened.slice(),
    postCalls: fetchCalls.length,
    hints: hints.slice(),
  };

  /* ---- 11. 通道开着 ⇒ 发一个 POST ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  api.state.ingest = INGEST;
  const posts = [];
  fetchImpl = function (url, opts) {
    posts.push(JSON.parse(opts.body));
    return okResponse({ ok: true, id: 'abc123' });
  };
  await api.doSubmit();
  obs.newWay = {
    postCalls: posts.length,
    report: posts[0] || null,
    hints: hints.slice(),
    queue: api.pendingQueue(),
    ingesting: api.isIngesting(),
  };

  /* ---- 12. ★ 投递要发夹过单日上限的那些天 ---- */
  reset();
  api.state.days = { '2026-11-25': 9999 };
  api.state.cap = 500;
  api.state.ingest = INGEST;
  const postsClamped = [];
  fetchImpl = function (url, opts) {
    postsClamped.push(JSON.parse(opts.body));
    return okResponse({ ok: true });
  };
  await api.doSubmit();
  obs.clamped = { report: postsClamped[0] || null, raw: api.state.days };

  /* ---- 13. 投递失败 ⇒ 报告入队 ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  api.state.ingest = INGEST;
  fetchImpl = function () { return Promise.reject(new Error('网络抽风')); };
  await api.doSubmit();
  obs.newWayFail = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
    ingesting: api.isIngesting(),
  };

  /* ---- 14. 连点两次 ⇒ 只发一个 POST ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  api.state.ingest = INGEST;
  let release = null;
  const gate = new Promise(function (res) { release = res; });
  fetchImpl = function () { return gate; };
  api.doSubmit();
  api.doSubmit();
  obs.doubleClick = {
    postCalls: fetchCalls.length,
    ingesting: api.isIngesting(),
    hints: hints.slice(),
  };
  release({ ok: true, status: 200, text: function () { return Promise.resolve(JSON.stringify({ ok: true })); } });
  await tick();
  await tick();
  obs.doubleClickAfter = { ingesting: api.isIngesting(), queue: api.pendingQueue() };

  /* ---- 15. flushPending：补发失败要放回队首 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () { return Promise.reject(new Error('服务端满了')); };
  await api.flushPending();
  obs.flushFail = { postCalls: fetchCalls.length, queue: api.pendingQueue() };

  /* ---- 16. flushPending：通道没开 ⇒ 什么都不做 ---- */
  reset();
  api.state.ingest = null;                   // 通道关着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }]));
  fetchImpl = function () { return okResponse({ ok: true }); };
  await api.flushPending();
  obs.flushOff = { postCalls: fetchCalls.length, queue: api.pendingQueue() };

  /* ---- 17. flushPending：成功 ⇒ 补发掉并清空队列 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () { return okResponse({ ok: true }); };
  await api.flushPending();
  obs.flushOk = { postCalls: fetchCalls.length, queue: api.pendingQueue(), hints: hints.slice() };

  /* ---- 18. 页面打开（sources.json 到了）⇒ 读顶层 ingest 段 + 自动补发 ---- */
  reset();
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'OLD' }]));
  const registry = {
    version: 1,
    perAward: 2,
    sources: { words: { per: 100, maxPerDay: 500, enabled: true } },
    ingest: { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true },
  };
  fetchImpl = function (url) {
    if (String(url).indexOf('sources.json') >= 0) {
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve(registry); } });
    }
    return okResponse({ ok: true });
  };
  await api.loadRegistry();
  await tick();
  await tick();
  obs.registry = {
    ingestUrl: (api.state.ingest || {}).url || null,
    ingestEnabled: (api.state.ingest || {}).enabled === true,
    per: api.state.per,
    cap: api.state.cap,
    queue: api.pendingQueue(),
    urls: fetchCalls.map(function (f) { return f.url; }),
  };

  /* ---- 19. 注册表拉不到 ⇒ 通道当没开（退回老办法），别炸 ---- */
  reset();
  api.state.ingest = INGEST;          // 先假设通道是开着的
  fetchImpl = function () { return Promise.reject(new Error('网络断了')); };
  const reg = await api.loadRegistry();
  obs.registryFail = {
    ingest: api.state.ingest,
    per: reg ? reg.per : null,
    enabled: reg ? reg.enabled : null,
  };

  /* ---- 20. ★ 未来的日期 / 假日期要剔掉（服务端是**整份拒收**） ---- */
  reset();
  const many = {};
  for (let i = 0; i < 35; i += 1) {
    const d = new Date(Date.UTC(2026, 8, 27 + i));   // 2026-09-27 起 35 天（都是真日子）
    many[d.toISOString().slice(0, 10)] = 100;
  }
  const filtered = api.filterReportDays(Object.assign({}, many, {
    '2026-02-30': 100,     // 假日期（2 月没有 30 号）
    '2099-01-01': 100,     // 未来的日期
  }));
  obs.filter = {
    kept: Object.keys(filtered.days).sort(),
    dropped: filtered.dropped.map(d => d.day).sort(),
    keptCount: Object.keys(filtered.days).length,
  };

  /* ---- 21. ★ 真的交上去的那份报告里不许有未来的 / 假的日子 ---- */
  reset();
  api.state.days = { '2026-11-25': 150, '2099-01-01': 150, '2026-02-30': 150 };
  api.state.ingest = INGEST;
  const postsFuture = [];
  fetchImpl = function (url, opts) {
    postsFuture.push(JSON.parse(opts.body));
    return okResponse({ ok: true });
  };
  await api.doSubmit();
  obs.futureDays = { report: postsFuture[0] || null };

  /* ---- 22. ★ 毒丸报告（服务端 400 拒收）不许堵住后面的报告 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([
    { v: 1, source: 'words', days: { '2099-01-01': 30 } },   // 服务端永远拒收
    { v: 1, source: 'words', days: { '2026-11-20': 50 } },   // 这条是好的
  ]));
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 400,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '日期在未来：2099-01-01' })); },
    });
  };
  await api.flushPending();
  obs.poison = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
  };

  /* ---- 22. ★ 注册表还没到就点「交上去」⇒ 什么都不做（不许降级成老办法） ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  api.state.ingest = INGEST;                 // 通道其实是开着的（配置到了才知道）
  api.state.registryLoaded = false;           // ★ 注册表还没回来
  fetchImpl = function () { throw new Error('配置没到时不该发请求'); };
  await api.doSubmit();
  obs.registryRace = {
    postCalls: fetchCalls.length,
    opened: opened.slice(),
    hints: hints.slice(),
  };

  /* ---- 23. ★ 存储写不进去时，别再谎称「已存着」 ---- */
  reset();
  api.state.days = { '2026-11-25': 150 };
  api.state.ingest = INGEST;
  storage.throwSet = true;                 // ★ 这台设备的浏览器不让存
  fetchImpl = function () { return Promise.reject(new Error('网络抽风')); };
  await api.doSubmit();
  obs.storageRefused = {
    enqueueReturned: api.enqueueReport({ id: 'X' }),
    hints: hints.slice(),
    queue: api.pendingQueue(),
  };
  storage.throwSet = false;

  /* ---- 24. ★ 存储读不了时，补发要说真话 ---- */
  reset();
  api.state.ingest = INGEST;
  storage.throwGet = true;
  fetchImpl = function () { return Promise.resolve(okResponse({ ok: true })); };
  await api.flushPending();
  obs.storageUnreadable = { hints: hints.slice() };
  storage.throwGet = false;

  /* ---- 25. ★ 暂时性失败（5xx）⇒ 队列留着，后面的先不发 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 503,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '服务端满了' })); },
    });
  };
  await api.flushPending();
  obs.tempFail = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
  };

  process.stdout.write(JSON.stringify(obs));
}

main().catch(function (e) {
    process.stderr.write('背单词页探针跑挂了：' + String((e && e.stack) || e) + '\n');
    process.exitCode = 1;
});
"""


def run_probe(app_js: pathlib.Path, script: str = NODE_PROBE) -> dict:
    """在 node 里真跑一遍 app.js 的那几段逻辑，返回观测结果。"""
    if not NODE:
        raise unittest.SkipTest("这台机没有 node，跑不了行为探针")
    with tempfile.TemporaryDirectory() as td:
        probe = pathlib.Path(td) / "probe.js"
        probe.write_text(script, encoding="utf-8")
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

    # ---------------- 毒丸报告（永久失败不许堵队列） ----------------

    def test_a_permanently_rejected_report_does_not_block_the_queue(self):
        """★ 服务端**永久**拒收的那条（4xx）⇒ 丢掉它、**继续发下一条**。

        为什么：以前失败就 break ⇒ 一条永远交不上的报告能把整条队列堵死，
        排在它后面的、本来能交上去的报告也永远发不出去，而且**没有任何提示**。
        """
        poison = self.obs.get("poison") or {}
        self.assertEqual(
            poison.get("posts"), 2,
            "毒丸那条被拒之后，**后面那条也要发**（不能 break 停手）",
        )
        self.assertEqual(
            poison.get("queue"), [],
            "被服务端永久拒收的那条要丢掉（再试一万次也一样）",
        )
        self.assertTrue(
            any("已丢弃" in t for t in poison.get("results") or []),
            "要告诉她「有 N 条服务端拒收，已丢弃」（现在的提示：%r）" % (poison.get("results"),),
        )

    # ---------------- 存储写不进去时不许说谎 ----------------

    def test_the_enqueue_tells_the_caller_whether_it_saved(self):
        """★ enqueueReport 要**说真话**：存不住就返回 false。

        （调用方要靠它决定怎么说话 —— 说「已存着」而其实没存住，那份报告就真没了。）
        """
        obs = self.obs.get("storageRefused") or {}
        self.assertIs(
            obs.get("enqueueReturned"), False,
            "存不进去时 enqueueReport 必须返回 false",
        )

    def test_the_hint_does_not_claim_it_saved_when_the_storage_refuses(self):
        """★ 报告没存住时，提示**不许**说「已存着」。"""
        obs = self.obs.get("storageRefused") or {}
        texts = obs.get("toasts") or []
        self.assertTrue(
            any("没存住" in t for t in texts),
            "存不住时要说「没存住」（现在的提示：%r）" % (texts,),
        )
        self.assertFalse(
            any("已存着" in t for t in texts),
            "存不住时**不许**说「已存着」（那份报告真的没了）：%r" % (texts,),
        )

    def test_the_flush_says_the_storage_is_unusable(self):
        """★ 存储读不了 ⇒ 待发队列用不了 ⇒ 必须说出来（不许假装一切正常）。"""
        obs = self.obs.get("storageUnreadable") or {}
        texts = obs.get("results") or []
        self.assertTrue(
            any("队列" in t for t in texts),
            "存储不可用时要提示「待发队列用不了」（现在的提示：%r）" % (texts,),
        )

    # ---------------- 注册表还没到就点提交 ----------------

    def test_clicking_before_the_registry_arrives_does_nothing(self):
        """★ 注册表还没到就点「交上去」⇒ **什么都不做**。

        （以前会被静默降级成「打开 GitHub 新建 issue 页」—— 而 GitHub 恰是
        那条在国内经常抽风的路；提示还说「已打开提交页」⇒ 她会以为功能没上线。）
        """
        race = self.obs.get("registryRace") or {}
        self.assertEqual(race.get("postCalls"), 0, "配置没读到时一个 POST 都不许发")
        self.assertEqual(race.get("opened"), [], "配置没读到时不许打开 GitHub 新建 issue 页")
        self.assertTrue(
            any("还在读通道配置" in t for t in race.get("toasts") or []),
            "要提示「还在读通道配置，稍等一下再点」（现在的提示：%r）" % (race.get("toasts"),),
        )

    def test_a_temporarily_failed_report_keeps_the_queue(self):
        """★ 暂时性失败（5xx / 网络抽风）⇒ 放回队首、**停手**，后面的先不发。"""
        temp = self.obs.get("tempFail") or {}
        self.assertEqual(temp.get("posts"), 1, "暂时失败时要停手（后面的留着下次再发）")
        self.assertEqual(
            temp.get("queue"),
            [{"id": "X"}, {"id": "Y"}],
            "失败的那条要放回**队首**（后面的那条不能丢）",
        )
        self.assertEqual(temp.get("results"), [], "一条都没发成功时不该说「补发了 N 条」")

    # ---------------- 客户端提前拦 ----------------

    def test_the_future_and_fake_days_are_dropped_before_sending(self):
        """★ 未来的日期 / 假日期要在客户端就剔掉 —— 服务端是**整份拒收**。"""
        f = self.obs.get("filter") or {}
        self.assertNotIn("2099-01-01", f.get("kept") or [], "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", f.get("kept") or [], "假日期（2026-02-30）不许进报告")
        self.assertIn("2099-01-01", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")
        self.assertIn("2026-02-30", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")

    def test_only_the_newest_thirty_days_are_sent(self):
        """★ 一次最多带 30 天（与服务端对齐）—— 超了只带最近 30 天。"""
        f = self.obs.get("filter") or {}
        self.assertEqual(f.get("keptCount"), 30, "超过 30 天时只带最近 30 天")

    def test_the_days_to_report_exclude_future_and_fake_ones(self):
        """★ 这一页真正要交上去的天数里，不许有未来的 / 假的日子。"""
        days = (self.obs.get("syncFilter") or {}).get("days") or {}
        self.assertIn("2026-11-25", days, "正常那天要在报告里")
        self.assertNotIn("2099-01-01", days, "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", days, "假日期不许进报告")

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


    def test_the_button_waits_for_the_registry(self):
        """★ 注册表还没读到 ⇒ 按钮要置灰（别让她点出一个老办法降级）。"""
        body = _function_body(self.js, "function renderSync(")
        self.assertIn("REGISTRY_LOADED", body,
                      "按钮的禁用条件要看 REGISTRY_LOADED（注册表没到就置灰）")

    def test_the_post_marks_permanent_failures(self):
        """★ 4xx（429 除外）= **永久**失败 ⇒ 调用方才能丢掉这条、继续发下一条。

        （分不出来的话，调用方只能把每一条都当成「下次还能成」
        ⇒ 一条永远交不上的报告就能把整条待发队列堵死。）
        """
        body = _function_body(self.js, "async function postIngest(")
        self.assertIn("status", body, "postIngest 要把 HTTP 状态码带出来")
        self.assertIn("permanent", body, "postIngest 要标出「这次失败是永久的」")
        self.assertIn("429", body, "429（限流）是暂时失败 ⇒ 那条不能丢")


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


class WordsIngestBehaviorTest(unittest.TestCase):
    """背单词页的投递通道：**真跑一遍** app.js 的原函数，看行为对不对。

    ★ 这一页的 app.js 是 IIFE ⇒ 探针**直接 require 页面那份文件**，
      跑的就是页面真正在跑的那几个函数（不是抠出来重拼的）。
    """

    @classmethod
    def setUpClass(cls):
        cls.obs = run_probe(WORDS_JS, WORDS_NODE_PROBE)

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

    def test_the_queue_key_is_separated_per_source(self):
        """★ 三个子页同源 ⇒ 共用同一份 localStorage ⇒ 队列键必须按源分开。

        番茄钟页用的是 `pendingIngest`；本页必须是 `pendingIngest.words`。
        不分开的话，两个页面的条目混在一个数组里 ⇒ 超 50 条时
        `slice(-50)` 会**静默丢掉别页最老的那条报告**。
        """
        iso = self.obs.get("isolated") or {}
        self.assertEqual(
            iso.get("key"),
            "pendingIngest.words",
            "背单词页的队列键必须是 pendingIngest.words（不许跟番茄钟页共用）",
        )
        self.assertEqual(
            iso.get("own"),
            [{"id": "WORDS"}],
            "本页只该看得到自己那个队列",
        )
        self.assertEqual(
            iso.get("other"),
            [{"id": "POM"}],
            "番茄钟页的队列原封不动",
        )
        self.assertEqual(
            iso.get("otherAfter"),
            [{"id": "POM"}],
            "本页入队**不许**动别页的队列（否则会挤掉别页的报告）",
        )
        self.assertEqual(
            iso.get("ownAfter"),
            [{"id": "WORDS"}, {"id": "WORDS2"}],
            "本页的条目只进本页的队列",
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

    def test_flush_sends_the_whole_queue_when_the_channel_works(self):
        """通道开着 ⇒ 补发掉整个队列，并告诉她补了几条。"""
        flush = self.obs.get("flushOk") or {}
        self.assertEqual(flush.get("postCalls"), 2, "队列里有两条 ⇒ 补发两次")
        self.assertEqual(flush.get("queue"), [], "补发成功的要出队")
        self.assertTrue(
            any("补发了 2 条" in t for t in flush.get("hints") or []),
            "补发完要告诉她补了几条（现在的提示：%r）" % (flush.get("hints"),),
        )

    # ---------------- 毒丸报告（永久失败不许堵队列） ----------------

    def test_a_permanently_rejected_report_does_not_block_the_queue(self):
        """★ 服务端**永久**拒收的那条（4xx）⇒ 丢掉它、**继续发下一条**。

        为什么：以前失败就 break ⇒ 一条永远交不上的报告能把整条队列堵死，
        排在它后面的、本来能交上去的报告也永远发不出去，而且**没有任何提示**。
        """
        poison = self.obs.get("poison") or {}
        self.assertEqual(
            poison.get("postCalls"), 2,
            "毒丸那条被拒之后，**后面那条也要发**（不能 break 停手）",
        )
        self.assertEqual(
            poison.get("queue"), [],
            "被服务端永久拒收的那条要丢掉（再试一万次也一样）",
        )
        self.assertTrue(
            any("已丢弃" in t for t in poison.get("hints") or []),
            "要告诉她「有 N 条服务端拒收，已丢弃」（现在的提示：%r）" % (poison.get("hints"),),
        )

    # ---------------- 存储写不进去时不许说谎 ----------------

    def test_the_enqueue_tells_the_caller_whether_it_saved(self):
        """★ enqueueReport 要**说真话**：存不住就返回 false。

        （调用方要靠它决定怎么说话 —— 说「已存着」而其实没存住，那份报告就真没了。）
        """
        obs = self.obs.get("storageRefused") or {}
        self.assertIs(
            obs.get("enqueueReturned"), False,
            "存不进去时 enqueueReport 必须返回 false",
        )

    def test_the_hint_does_not_claim_it_saved_when_the_storage_refuses(self):
        """★ 报告没存住时，提示**不许**说「已存着」。"""
        obs = self.obs.get("storageRefused") or {}
        texts = obs.get("hints") or []
        self.assertTrue(
            any("没存住" in t for t in texts),
            "存不住时要说「没存住」（现在的提示：%r）" % (texts,),
        )
        self.assertFalse(
            any("已存着" in t for t in texts),
            "存不住时**不许**说「已存着」（那份报告真的没了）：%r" % (texts,),
        )

    def test_the_flush_says_the_storage_is_unusable(self):
        """★ 存储读不了 ⇒ 待发队列用不了 ⇒ 必须说出来（不许假装一切正常）。"""
        obs = self.obs.get("storageUnreadable") or {}
        texts = obs.get("hints") or []
        self.assertTrue(
            any("队列" in t for t in texts),
            "存储不可用时要提示「待发队列用不了」（现在的提示：%r）" % (texts,),
        )

    # ---------------- 注册表还没到就点提交 ----------------

    def test_clicking_before_the_registry_arrives_does_nothing(self):
        """★ 注册表还没到就点「交上去」⇒ **什么都不做**。

        （以前会被静默降级成「打开 GitHub 新建 issue 页」—— 而 GitHub 恰是
        那条在国内经常抽风的路；提示还说「已打开提交页」⇒ 她会以为功能没上线。）
        """
        race = self.obs.get("registryRace") or {}
        self.assertEqual(race.get("postCalls"), 0, "配置没读到时一个 POST 都不许发")
        self.assertEqual(race.get("opened"), [], "配置没读到时不许打开 GitHub 新建 issue 页")
        self.assertTrue(
            any("还在读通道配置" in t for t in race.get("hints") or []),
            "要提示「还在读通道配置，稍等一下再点」（现在的提示：%r）" % (race.get("hints"),),
        )

    def test_a_temporarily_failed_report_keeps_the_queue(self):
        """★ 暂时性失败（5xx / 网络抽风）⇒ 放回队首、**停手**，后面的先不发。"""
        temp = self.obs.get("tempFail") or {}
        self.assertEqual(temp.get("postCalls"), 1, "暂时失败时要停手（后面的留着下次再发）")
        self.assertEqual(
            temp.get("queue"),
            [{"id": "X"}, {"id": "Y"}],
            "失败的那条要放回**队首**（后面的那条不能丢）",
        )
        self.assertEqual(temp.get("hints"), [], "一条都没发成功时不该说「补发了 N 条」")

    # ---------------- 客户端提前拦 ----------------

    def test_the_future_and_fake_days_are_dropped_before_sending(self):
        """★ 未来的日期 / 假日期要在客户端就剔掉 —— 服务端是**整份拒收**。"""
        f = self.obs.get("filter") or {}
        self.assertNotIn("2099-01-01", f.get("kept") or [], "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", f.get("kept") or [], "假日期（2026-02-30）不许进报告")
        self.assertIn("2099-01-01", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")
        self.assertIn("2026-02-30", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")

    def test_only_the_newest_thirty_days_are_sent(self):
        """★ 一次最多带 30 天（与服务端对齐）—— 超了只带**最近** 30 天。"""
        f = self.obs.get("filter") or {}
        self.assertEqual(f.get("keptCount"), 30, "超过 30 天时只带最近 30 天")

    def test_the_report_carries_no_future_or_fake_day(self):
        """★ 页面重算之后要交上去的那份里，不许有未来的 / 假的日子。"""
        rec = self.obs.get("recomputed") or {}
        days = rec.get("reportDays") or {}
        self.assertIn("2026-11-25", days, "正常那天要在报告里")
        self.assertNotIn("2099-01-01", days, "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", days, "假日期不许进报告")
        self.assertIn("2099-01-01", rec.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")
        self.assertIn("2026-02-30", rec.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")

    def test_the_report_carries_no_future_or_fake_day(self):
        """★ 真的交上去的那份报告里，不许有未来的 / 假的日子。"""
        report = (self.obs.get("futureDays") or {}).get("report") or {}
        days = report.get("days") or {}
        self.assertIn("2026-11-25", days, "正常那天要在报告里")
        self.assertNotIn("2099-01-01", days, "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", days, "假日期不许进报告")

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
            {"v": 1, "source": "words", "days": {"2026-11-25": 150}},
            "请求体就是那份报告",
        )

    def test_a_rejected_post_raises(self):
        """服务端拒收（队列满了）⇒ postIngest 要抛，调用方才知道要排队。"""
        self.assertEqual(self.obs.get("postError"), "队列满了", "服务端拒收时要把错误抛出来")

    # ---------------- 点「交上去」 ----------------

    def test_the_old_way_is_used_when_the_channel_is_off(self):
        """通道没开 ⇒ 打开 GitHub 的新建 issue 页，**一个 POST 都不许发**。"""
        old = self.obs.get("oldWay") or {}
        self.assertEqual(
            len(old.get("opened") or []),
            1,
            "通道没开时要点开 GitHub 的新建 issue 页（老办法）",
        )
        self.assertTrue(
            (old.get("opened") or [""])[0].startswith(
                "https://github.com/lilia-shound/pokemon-dex/issues/new?"),
            "老办法要开的是 GitHub 的新建 issue 页：%r" % (old.get("opened"),),
        )
        self.assertEqual(old.get("postCalls"), 0, "通道没开时不许发投递请求")
        self.assertTrue(old.get("hints"), "老办法要点一下提示（告诉她还要再点一下 Submit）")

    def test_the_new_way_posts_once_when_the_channel_is_on(self):
        """通道开着 ⇒ 发一个 POST，成功就报「已记录」。"""
        new = self.obs.get("newWay") or {}
        self.assertEqual(new.get("postCalls"), 1, "点一次「交上去」只该发一个 POST")
        self.assertEqual(
            new.get("report"),
            {"v": 1, "source": "words", "days": {"2026-11-25": 150}},
            "投递的要是那份报告",
        )
        self.assertEqual(new.get("queue"), [], "成功了就不该留队列")
        self.assertFalse(new.get("ingesting"), "投递完要把「正在投递」的标志清掉")
        self.assertTrue(
            any("已记录" in t for t in new.get("hints") or []),
            "成功要原地提示「已记录」（现在的提示：%r）" % (new.get("hints"),),
        )

    def test_the_report_carries_the_clamped_days(self):
        """★ 投递要发**夹过单日上限**的那些天（不是 state.days 里的原值）。"""
        clamped = self.obs.get("clamped") or {}
        self.assertEqual(
            (clamped.get("report") or {}).get("days"),
            {"2026-11-25": 500},
            "超过单日上限（500）的那天要按 500 报 —— 服务端会把超上限的整份丢掉",
        )
        self.assertEqual(
            clamped.get("raw"),
            {"2026-11-25": 9999},
            "页面上的原值不动（夹取只作用在发出去的那份报告上）",
        )

    def test_a_failed_post_queues_the_report(self):
        """投递失败 ⇒ 报告入队（等下次补发）。"""
        fail = self.obs.get("newWayFail") or {}
        self.assertEqual(fail.get("postCalls"), 1, "失败前确实发了一次")
        self.assertEqual(
            fail.get("queue"),
            [{"v": 1, "source": "words", "days": {"2026-11-25": 150}}],
            "投递失败的报告要存进待发队列",
        )
        self.assertFalse(fail.get("ingesting"), "失败之后也要把「正在投递」的标志清掉")
        self.assertTrue(
            any("已存着" in t for t in fail.get("hints") or []),
            "失败要告诉她「已存着，下次自动补发」（现在的提示：%r）" % (fail.get("hints"),),
        )

    def test_clicking_submit_twice_sends_only_one_post(self):
        """连点两次 ⇒ 只发一个 POST（workers.dev 在国内慢，很容易点第二下）。"""
        double = self.obs.get("doubleClick") or {}
        self.assertEqual(
            double.get("postCalls"),
            1,
            "连点两次「交上去」只能发一个 POST —— 两个都失败的话同一份报告会入队两次",
        )
        self.assertTrue(double.get("ingesting"), "投递期间应该有个「正在投递」的标志")
        self.assertTrue(
            any("稍等" in t for t in double.get("hints") or []),
            "重复点击要给她一句提示（现在的提示：%r）" % (double.get("hints"),),
        )
        after = self.obs.get("doubleClickAfter") or {}
        self.assertFalse(after.get("ingesting"), "投递结束后要清掉标志，否则她再也点不动")
        self.assertEqual(after.get("queue"), [], "投递成功就不该留队列")

    # ---------------- 页面打开 ----------------

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置从**已经拉过的那份** sources.json 顶层读 ⇒ 不新增网络请求。"""
        reg = self.obs.get("registry") or {}
        self.assertEqual(
            reg.get("ingestUrl"),
            "https://worker.example/ingest",
            "ingest 配置要从 sources.json 顶层读（不新增网络请求）",
        )
        self.assertTrue(reg.get("ingestEnabled"), "ingest 段要照原样读进来")
        self.assertEqual(reg.get("per"), 100, "口径还是从同一份注册表读")
        self.assertEqual(reg.get("cap"), 500, "单日上限还是从同一份注册表读")

    def test_the_page_flushes_the_queue_when_the_registry_arrives(self):
        """页面打开（sources.json 到了）⇒ 自动补发待发队列。"""
        reg = self.obs.get("registry") or {}
        self.assertEqual(reg.get("queue"), [], "页面打开时要把上次没交上去的补发掉")
        self.assertIn(
            "https://worker.example/ingest",
            reg.get("urls") or [],
            "补发要打到通道地址上",
        )
        self.assertEqual(
            len([u for u in (reg.get("urls") or []) if "sources.json" in u]),
            1,
            "注册表只许拉一次（ingest 配置从**同一份**里读）",
        )

    def test_a_broken_registry_leaves_the_channel_off(self):
        """注册表拉不到 ⇒ 通道当没开（退回老办法），别炸。"""
        fail = self.obs.get("registryFail") or {}
        self.assertIsNone(fail.get("ingest"), "拉不到注册表 ⇒ 没有通道配置 ⇒ 走老办法")
        self.assertEqual(fail.get("per"), 100, "拉不到注册表 ⇒ 退回兜底口径")
        self.assertTrue(fail.get("enabled"), "拉不到注册表 ⇒ 不知道源停没停 ⇒ 照旧让她记")


class WordsIngestContractTest(unittest.TestCase):
    """背单词页：源码层面的契约断言（行为由 WordsIngestBehaviorTest 真跑一遍）。"""

    @classmethod
    def setUpClass(cls):
        cls.js = WORDS_JS.read_text(encoding="utf-8")
        cls.html = WORDS_HTML.read_text(encoding="utf-8")

    def test_the_page_posts_to_the_ingest_endpoint(self):
        self.assertIn("postIngest", self.js, "要有 postIngest")
        self.assertIn("X-Dex-Token", self.js, "要带 ingest token 头")
        self.assertIn("method: 'POST'", self.js, "要用 POST")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置来自**已经拉过的** sources.json ⇒ 不许新增网络请求。"""
        self.assertTrue(
            re.search(r"d\.ingest|\['ingest'\]", self.js) is not None,
            "要从 sources.json 顶层读 ingest 段",
        )
        # 页面本来就有 1 个请求（sources.json），加了投递之后最多 2 个
        # —— **不许再多来一个注册表请求**
        self.assertLessEqual(
            len(re.findall(r"fetch\(", self.js)),
            2,
            "背单词页的网络请求不该变多（只有 sources.json 一个 + 投递那一个）",
        )

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        """老办法不许删 —— workers.dev 在国内可能抽风。"""
        self.assertIn("buildIssueUrl", self.js, "老办法的 URL 构造要留着")
        self.assertIn("oldWayBtn", self.html, "页面里要有老办法链接")
        self.assertIn("ingestAvailable", self.js, "通道没开时要能退回老办法")

    def test_the_submit_button_is_a_button_not_a_link(self):
        """主按钮改成 <button>（它不再跳去 GitHub）。"""
        self.assertRegex(self.html, r'<button[^>]*id="submitBtn"')
        self.assertNotRegex(self.html, r'<a[^>]*id="submitBtn"')

    def test_the_page_queues_failed_reports(self):
        """交不上去要存下来，下次打开自动补发。"""
        self.assertIn("pendingIngest", self.js, "要有待发队列")
        self.assertIn("flushPending", self.js, "页面打开时要补发")
        self.assertIn("localStorage", self.js, "待发队列要落 localStorage")

    def test_the_pending_queue_key_is_separated_per_source(self):
        """★ 三个子页同源 ⇒ 共用一份 localStorage ⇒ 队列键必须按源分开。"""
        self.assertIn(
            "pendingIngest.words",
            self.js,
            "背单词页的队列键必须是 pendingIngest.words（番茄钟页是 pendingIngest）",
        )
        self.assertNotRegex(
            self.js,
            r"PENDING_KEY\s*=\s*'pendingIngest'",
            "不许跟番茄钟页共用 pendingIngest —— 两个页面的条目会混在一个数组里",
        )

    def test_the_queue_can_put_a_report_back_at_the_head(self):
        """补发失败时要放回队首（unshift），不能只有 push。"""
        body = _function_body(self.js, "function enqueueReport(")
        self.assertIn("unshift", body, "enqueueReport 要能把报告放回队首")
        self.assertIn("push", body, "enqueueReport 也要能把报告排到队尾")

    def test_the_queue_keeps_the_newest_50(self):
        """队列上限：只留最近 50 条（slice(-50)），不是最老 50 条。"""
        self.assertIn(
            "slice(-50)",
            self.js,
            "待发队列超限时只留**最近** 50 条（slice(-50)）",
        )

    def test_the_page_has_an_in_flight_guard(self):
        """连点两次「交上去」⇒ 只发一个 POST。"""
        self.assertIn("let INGESTING", self.js, "要有一个「正在投递」的标志")
        body = _function_body(self.js, "async function doSubmit(")
        self.assertIn("if (INGESTING)", body, "提交入口要判重")
        self.assertIn("INGESTING = true", body, "发投递之前要把标志置上")
        self.assertIn("INGESTING = false", body, "finally 里要把标志清掉")
        self.assertIn("finally", body, "标志必须在 finally 里清（成功/失败/异常都要清）")

    def test_the_old_way_branch_comes_before_the_post(self):
        """doSubmit 里「通道没开 ⇒ 走老办法」的判断必须在 postIngest **之前**。"""
        body = _function_body(self.js, "async function doSubmit(")
        guard = body.find("!ingestAvailable()")
        post = body.find("postIngest(")
        self.assertGreaterEqual(guard, 0, "doSubmit 里要有「通道没开」的分支")
        self.assertGreater(post, guard, "「退回老办法」的判断必须在投递之前")
        self.assertIn("window.open(buildIssueUrl(", body, "老办法要真的打开 GitHub 页面")


    def test_the_button_waits_for_the_registry(self):
        """★ 注册表还没读到 ⇒ 按钮要置灰（别让她点出一个老办法降级）。"""
        body = _function_body(self.js, "function renderSubmit(")
        self.assertIn("registryLoaded", body,
                      "按钮的禁用条件要看 registryLoaded（注册表没到就置灰）")

    def test_the_post_marks_permanent_failures(self):
        """★ 4xx（429 除外）= **永久**失败 ⇒ 调用方才能丢掉这条、继续发下一条。

        （分不出来的话，调用方只能把每一条都当成「下次还能成」
        ⇒ 一条永远交不上的报告就能把整条待发队列堵死。）
        """
        body = _function_body(self.js, "async function postIngest(")
        self.assertIn("status", body, "postIngest 要把 HTTP 状态码带出来")
        self.assertIn("permanent", body, "postIngest 要标出「这次失败是永久的」")
        self.assertIn("429", body, "429（限流）是暂时失败 ⇒ 那条不能丢")


    def test_the_page_flushes_the_queue_after_loading_the_registry(self):
        """页面打开时要调 flushPending（配置到了就补发）。"""
        body = _function_body(self.js, "function loadRegistry(")
        self.assertIn("flushPending()", body, "sources.json 到了之后要补发待发队列")
        # ★ 精确匹配「flushPending() 后面紧跟 .catch(」——
        #   只查「切片里有 .catch(」会被**注册表自己的** `.catch(err => {` 满足
        #   （错因满足：把 flushPending().catch(...) 换成 flushPending(); 测试照样绿）。
        self.assertRegex(
            body,
            r"flushPending\(\)\s*\.catch\(",
            "补发是异步的 ⇒ 调用处必须挂 .catch（flushPending() 后面要**紧跟** .catch(）",
        )

    def test_the_submit_call_site_catches_the_promise(self):
        """★ doSubmit() 是异步的 ⇒ 调用处必须挂 .catch（漏了控制台会红）。

        没有这条断言的话，把 `doSubmit().catch(...)` 换成 `doSubmit();` 测试照样绿。
        """
        body = _function_body(self.js, "function bind(")
        self.assertRegex(
            body,
            r"doSubmit\(\)\s*\.catch\(",
            "提交按钮的点击处理里，doSubmit() 后面要**紧跟** .catch(",
        )

    @unittest.skipUnless(NODE, "这台机没有 node")
    def test_the_page_script_is_valid_javascript(self):
        """app.js 得是能跑的脚本（不能改坏成模块或半截代码）。"""
        proc = subprocess.run(
            [NODE, "--check", str(WORDS_JS)], capture_output=True, text=True
        )
        self.assertEqual(
            proc.returncode, 0, "app.js 语法有问题：\n%s" % proc.stderr[-2000:]
        )


class WordsSubmitHintBrowserTest(unittest.TestCase):
    """真实浏览器（Playwright）里，**提交提示行**（#submitHint）的行为断言。

    测三件零覆盖的事：

    1. **提示语按通道开关分** —— 通道关着点提交要说「已打开提交页…」（老办法），
       通道开着点提交要说「已记录」⇒ 两种状态下的文案**必须不同**。
    2. **内容没变就不重写 DOM** —— 重画时提示节点不许被重建
       （重建会把「按下鼠标到松开」那一下点空）。
    3. **结果提示不 lingering** —— 提交成功后换一天，那句「已记录 ✓」不许一直挂着。

    ★ 为什么不能只在 node 里测：这一页的 app.js 是 IIFE，node 探针顶不掉 DOM ⇒
      「点一下到底写出哪句话」「那个节点有没有被换掉」只能在真浏览器里看。
    """

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过提交提示行的浏览器测试")
        if not CHROME:
            raise unittest.SkipTest("找不到 chromium，跳过提交提示行的浏览器测试")
        cls.httpd = _make_server(WORDS_SITE_DIR)
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.base = cls.origin + "/words/index.html"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def setUp(self):
        self.page_errors = []
        self.registry_response = None
        self.ingest_requests = []
        self.page = self._new_page()

    def tearDown(self):
        try:
            self.page.close()
        except Exception:
            pass

    # -- 辅助 -----------------------------------------------------------

    def _new_page(self):
        """开一个标签页：拦下注册表与投递请求，并把 window.open 顶掉。

        ★ window.open 顶掉的原因：老办法会 `window.open(github 的链接)` ⇒
          真开一个标签页去连外网（沙箱里连不上，还会挂在那儿）。
          这里只**记下**那个 URL（「点主按钮确实会开新页」那条用例留给后面统一处理）。
        """
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.add_init_script("""
          window.__opened = [];
          window.open = function (u) { window.__opened.push(String(u)); return null; };
        """)

        def handle_registry(route):
            if self.registry_response is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.registry_response))

        def handle_ingest(route):
            self.ingest_requests.append(route.request.url)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "id": "mock-1"}))

        page.route("**/sources.json", handle_registry)
        page.route("**/ingest-mock", handle_ingest)
        return page

    def _registry(self, channel: bool) -> dict:
        """给页面看的一份 sources.json。

        channel=True  ⇒ 免登录通道**开着**（ingest 段有地址）
        channel=False ⇒ 通道**关着**（ingest 段空着 —— 部署前的常态）
        """
        reg = {
            "version": 1,
            "perAward": 2,
            "sources": {"words": {"per": 100, "maxPerDay": 500, "enabled": True}},
            "ingest": {"url": "", "token": "", "enabled": False},
        }
        if channel:
            reg["ingest"] = {
                "url": self.origin + "/ingest-mock",
                "token": "tok-mock",
                "enabled": True,
            }
        return reg

    def _open(self, page, channel: bool):
        """打开页面并等**注册表真的生效**（口径与通道配置都换上去了）。"""
        self.registry_response = self._registry(channel)
        page.goto(self.base, wait_until="commit")
        page.wait_for_function(
            "() => !!(window.WordsLanding && WordsLanding.state.registryLoaded)",
            timeout=30000,
        )

    def _fill(self, page, value: str):
        """往输入框里填个数（并等它真的填上了）。"""
        page.fill("#countInput", value)
        page.wait_for_function(
            "(v) => document.querySelector('#countInput').value === v",
            arg=value, timeout=10000,
        )

    def _hint(self, page) -> str:
        return page.inner_text("#submitHint")

    def _shift_key(self, page, days: int) -> str:
        """把日期键挪几天（-1 = 昨天）—— 用页面自己算的「今天」。"""
        today = page.evaluate("() => WordsLanding.todayKey()")
        y, m, d = (int(x) for x in today.split("-"))
        return (datetime.date(y, m, d) + datetime.timedelta(days=days)).isoformat()

    def _wait_hint(self, page, predicate: str, arg=None, timeout: int = 10000):
        """等提示行满足某个条件。

        ★ 等不到**不抛异常** —— 让后面的断言去报**实际文案**：
          超时错误只会说「等超时了」，断言失败会直接说「她看到的是哪句话」。
        """
        try:
            page.wait_for_function(predicate, arg=arg, timeout=timeout)
        except Exception:
            pass

    def _submit_once(self, channel: bool) -> str:
        """在指定通道状态下点一次「交上去」，返回提示行最后的文案。"""
        page = self._new_page()
        try:
            self._open(page, channel)
            self._fill(page, "150")
            before = self._hint(page)
            page.click("#submitBtn")
            # 等提示行**变了**（变了才算点出了结果）
            self._wait_hint(
                page,
                "(b) => (document.querySelector('#submitHint').textContent || '') !== b",
                arg=before,
            )
            return self._hint(page)
        finally:
            page.close()

    # -- 用例 -----------------------------------------------------------

    def test_the_submit_hint_differs_between_the_two_channels(self):
        """★ 通道关着 ⇒「已打开提交页…」；通道开着 ⇒「已记录」—— 文案必须不同。

        为什么：这是**两条完全不同的路**（老办法 vs 直投）⇒
        她看到的话必须不一样 —— 否则她不知道到底交没交上去。
        """
        off_text = self._submit_once(False)
        on_text = self._submit_once(True)
        self.assertIn(
            "已打开提交页", off_text,
            "通道关着 ⇒ 要说「已打开提交页」（老办法还要再点一下 Submit）",
        )
        self.assertIn("已记录", on_text, "通道开着 ⇒ 要说「已记录」")
        self.assertNotEqual(
            off_text, on_text,
            "两种通道状态下的提示文案必须不同（现在两句话一样：%r）" % (on_text,),
        )

    def test_the_hint_node_is_not_rebuilt_when_the_page_redraws(self):
        """★ 重画时提示行**不许被重建** —— 重建会把「按下鼠标到松开」那一下点空。

        测法：给提示节点打一个标记，再触发三次**内容没变**的重画 ⇒
        - 节点上的标记还在 ⇒ 那个节点没被换掉；
        - 里面那个子元素还是**同一个对象** ⇒ 那段内容没被重写
          （重写会把子元素换掉 —— 换掉的话，她「按下鼠标到松开」之间
          点到的就不是刚才那个元素了）。

        ★ 子元素用「比身份」而不是「打标记」来测：往子元素上加属性会改掉
          `innerHTML` ⇒ 页面那句「内容没变就别重写」的判断就永远不成立 ⇒
          测出来的是「内容变了所以重写」，不是「内容没变却重写了」。
        """
        page = self._new_page()
        try:
            # 老办法那句提示里有 <b> 子元素（「点一下 <b>Submit</b>」）
            self._open(page, channel=False)
            self._fill(page, "150")
            marked = page.evaluate("""
              (() => {
                const el = document.querySelector('#submitHint');
                el.dataset.mark = 'x';
                // 存下子元素的**引用**（只存引用，不动它的内容）
                window.__hintChild = el.querySelector('b');
                return { hasChild: !!window.__hintChild };
              })()
            """)
            self.assertTrue(marked.get("hasChild"), "提示里没有子元素了 —— 这条用例测不到「内容没被重写」")
            # 三次重画：换一天、+20、再换一天（提示那句话都不变）
            today = self._shift_key(page, 0)
            yesterday = self._shift_key(page, -1)
            page.click('.dcell[data-day="%s"]' % yesterday)
            page.click('#quick button[data-add="20"]')
            page.click('.dcell[data-day="%s"]' % today)
            after = page.evaluate("""
              (() => {
                const el = document.querySelector('#submitHint');
                const b = el.querySelector('b');
                return {
                  nodeMark: el.dataset.mark || null,
                  hasChild: !!b,
                  sameChild: (!!b && b === window.__hintChild),
                };
              })()
            """)
        finally:
            page.close()
        self.assertEqual(after["nodeMark"], "x", "提示节点被重建了（标记没了）")
        self.assertTrue(after["hasChild"], "提示里的子元素不见了")
        self.assertTrue(
            after["sameChild"],
            "提示内容被重写了（里面的子元素被换掉了）—— 内容没变就不该重写",
        )

    def test_the_result_hint_does_not_linger_after_switching_day(self):
        """提交成功后点另一个日期 ⇒「已记录 ✓」不许一直挂着。

        （只清 state 不重画的话，那句话会一直挂到下一次重画为止。）
        """
        page = self._new_page()
        try:
            self._open(page, channel=True)
            self._fill(page, "150")
            page.click("#submitBtn")
            self._wait_hint(
                page,
                "() => (document.querySelector('#submitHint').textContent || '').indexOf('已记录') >= 0",
            )
            yesterday = self._shift_key(page, -1)
            page.click('.dcell[data-day="%s"]' % yesterday)
            # 等那张卡片变成「选中」的样子 ⇒ 重画已经完成
            page.wait_for_function(
                """(k) => {
                  const el = document.querySelector('.dcell[data-day="' + k + '"]');
                  return !!el && el.classList.contains('on');
                }""",
                arg=yesterday, timeout=10000,
            )
            hint = self._hint(page)
        finally:
            page.close()
        self.assertNotIn(
            "已记录", hint,
            "换了一天之后「已记录 ✓」还挂着 —— 那是**过时**的提示（她已经换到别的日子）",
        )

    def test_the_page_has_no_uncaught_error(self):
        """改完重画逻辑之后，页面不许有未捕获的异常。"""
        self._open(self.page, channel=True)
        self._fill(self.page, "150")
        self.page.click("#submitBtn")
        self._wait_hint(
            self.page,
            "() => (document.querySelector('#submitHint').textContent || '').indexOf('已记录') >= 0",
        )
        self.page.click('#quick button[data-add="20"]')
        self.assertEqual(self.page_errors, [])


# --------------------------------------------------------------------------
# KOReader 落地页（site/koreader/）的探针。
#
# ★ 这一页的 app.js 是 **IIFE** ⇒ 跟背单词页一个路子：
#   探针**直接 require 页面那份 app.js**，跑的就是页面真正在跑的那几个函数
#   （不是抠出来重拼的）。页面里的 DOM 由 `ui.hint` / `ui.render` 两个口子顶掉，
#   网络由 global.fetch 顶掉，本机存储由 global.window.localStorage 顶掉。
# --------------------------------------------------------------------------
KO_NODE_PROBE = r"""
'use strict';
const path = require('path');

const APP = process.env.APP_JS_PATH || path.join(__dirname, 'app.js');

/* ---------- 假件 ---------- */
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

let storage = fakeStorage();
const opened = [];
global.window = {
  get localStorage() { return storage; },
  open: function (url) { opened.push(url); },
};

const api = require(APP);
const PENDING_KEY = api.PENDING_KEY;
const INGEST = { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true };
const DAYS = { '2026-11-25': 50 };
const REPORT = { v: 1, source: 'koreader', days: { '2026-11-25': 50 } };

/* 提示 / 重画：页面里是 DOM，这里换成收集器 */
const hints = [];
let renders = 0;
api.ui.hint = function (t) { hints.push(String(t)); };
api.ui.render = function () { renders += 1; };

/* 网络：页面里是 fetch，这里换成假件 */
const fetchCalls = [];
let fetchImpl = null;
global.fetch = function (url, opts) {
  fetchCalls.push({ url: String(url), options: opts || {} });
  return fetchImpl(url, opts);
};

function okResponse(payload) {
  return Promise.resolve({
    ok: true, status: 200,
    text: function () { return Promise.resolve(JSON.stringify(payload || { ok: true })); },
  });
}

function reset(over) {
  storage = fakeStorage();
  opened.length = 0;
  hints.length = 0;
  renders = 0;
  fetchCalls.length = 0;
  fetchImpl = null;
  api.state.days = {};
  api.state.reportDays = {};
  api.state.clamped = [];
  api.state.dropped = [];
  api.state.ingest = null;
  api.state.lastSubmit = null;
  api.state.registryLoaded = true;      // 默认「注册表已经到了」（单独测竞态时再关掉）
  api.state.per = 15;
  api.state.cap = 1440;
  api.state.credits = 0;
  api.state.issueUrl = '';
  api.state.issueTitle = '';
  if (over) over();
}

/** 让页面处于「有几天数可交」的状态（reportDays 是夹过上限的那份） */
function withDays(days) {
  api.state.reportDays = Object.assign({}, days);
  api.state.issueUrl = api.buildIssueUrl(days);
}

function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }

async function main() {
  const obs = {};

  /* ---- 1. 入队：最老在前 ---- */
  reset();
  api.enqueueReport({ id: 'A' });
  api.enqueueReport({ id: 'B' });
  obs.queueAfterAB = api.pendingQueue();

  /* ---- 2. shiftPending 取队首，取完少一条 ---- */
  obs.shifted = api.shiftPending();
  obs.queueAfterShift = api.pendingQueue();

  /* ---- 3. toFront 放回队首 ---- */
  api.enqueueReport({ id: 'C' }, true);
  obs.queueAfterToFront = api.pendingQueue();

  /* ---- 4. 超过 50 条只留最近 50 条 ---- */
  reset();
  for (let i = 1; i <= 60; i += 1) api.enqueueReport({ n: i });
  const over = api.pendingQueue();
  obs.overLimit = {
    len: over.length,
    first: over.length ? over[0].n : null,
    last: over.length ? over[over.length - 1].n : null,
  };

  /* ---- 5. 坏 JSON 当空队列（不许抛） ---- */
  reset();
  storage.setItem(PENDING_KEY, '这不是 JSON');
  try { obs.badJsonQueue = api.pendingQueue(); }
  catch (e) { obs.badJsonQueue = { threw: String(e && e.message) }; }

  /* ---- 6. 隐私模式：localStorage 抛异常也不许炸 ---- */
  reset();
  storage.throwGet = true;
  try { obs.throwGetQueue = api.pendingQueue(); }
  catch (e) { obs.throwGetQueue = { threw: String(e && e.message) }; }
  storage.throwGet = false;
  storage.throwSet = true;
  try { api.enqueueReport({ id: 'X' }); obs.throwSetThrew = null; }
  catch (e) { obs.throwSetThrew = String(e && e.message); }

  /* ---- 7. ★ 队列键按源分开：别页（番茄钟 / 背单词）的队列不许被碰到 ---- */
  reset();
  storage.setItem('pendingIngest', JSON.stringify([{ id: 'POM' }]));
  storage.setItem('pendingIngest.words', JSON.stringify([{ id: 'WORDS' }]));
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'KO' }]));
  obs.isolated = {
    key: PENDING_KEY,
    own: api.pendingQueue(),
    other: JSON.parse(storage._data['pendingIngest']),
    otherWords: JSON.parse(storage._data['pendingIngest.words']),
  };
  api.enqueueReport({ id: 'KO2' });
  obs.isolated.otherAfter = JSON.parse(storage._data['pendingIngest']);
  obs.isolated.otherWordsAfter = JSON.parse(storage._data['pendingIngest.words']);
  obs.isolated.ownAfter = api.pendingQueue();

  /* ---- 8. postIngest：方法、token 头、请求体 ---- */
  reset();
  api.state.ingest = INGEST;
  let seen = null;
  fetchImpl = function (url, opts) {
    seen = { url: String(url), options: opts || {} };
    return okResponse({ ok: true, id: 'abc123' });
  };
  obs.postResult = await api.postIngest(REPORT);
  obs.postFetch = {
    url: seen.url,
    method: seen.options.method,
    token: (seen.options.headers || {})['X-Dex-Token'],
    contentType: (seen.options.headers || {})['Content-Type'],
    body: JSON.parse(seen.options.body),
  };

  /* ---- 9. postIngest：服务端说不行 ⇒ 要抛 ---- */
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 429,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '队列满了' })); },
    });
  };
  try { await api.postIngest(REPORT); obs.postError = null; }
  catch (e) { obs.postError = String(e && e.message); }

  /* ---- 10. 通道没开 ⇒ 走老办法（开 GitHub 页，一个 POST 都不许发） ---- */
  reset();
  withDays(DAYS);
  fetchImpl = function () { throw new Error('通道没开时不该发请求'); };
  await api.doSubmit();
  obs.oldWay = {
    opened: opened.slice(),
    postCalls: fetchCalls.length,
    hints: hints.slice(),
  };

  /* ---- 11. 通道开着 ⇒ 发一个 POST ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;
  const posts = [];
  fetchImpl = function (url, opts) {
    posts.push(JSON.parse(opts.body));
    return okResponse({ ok: true, id: 'abc123' });
  };
  await api.doSubmit();
  obs.newWay = {
    postCalls: posts.length,
    report: posts[0] || null,
    hints: hints.slice(),
    queue: api.pendingQueue(),
    ingesting: api.isIngesting(),
  };

  /* ---- 12. ★ 投递要发夹过单日上限的那些天（state.reportDays，不是 state.days） ---- */
  reset();
  api.state.days = { '2026-11-25': 9999 };        // 页面上的原值
  api.state.reportDays = { '2026-11-25': 1440 };  // 夹过上限的那份
  api.state.issueUrl = api.buildIssueUrl(api.state.reportDays);
  api.state.ingest = INGEST;
  const postsClamped = [];
  fetchImpl = function (url, opts) {
    postsClamped.push(JSON.parse(opts.body));
    return okResponse({ ok: true });
  };
  await api.doSubmit();
  obs.clamped = { report: postsClamped[0] || null, raw: api.state.days };

  /* ---- 13. 投递失败 ⇒ 报告入队 ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;
  fetchImpl = function () { return Promise.reject(new Error('网络抽风')); };
  await api.doSubmit();
  obs.newWayFail = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
    ingesting: api.isIngesting(),
  };

  /* ---- 14. 连点两次 ⇒ 只发一个 POST ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;
  let release = null;
  const gate = new Promise(function (res) { release = res; });
  fetchImpl = function () { return gate; };
  api.doSubmit();
  api.doSubmit();
  obs.doubleClick = {
    postCalls: fetchCalls.length,
    ingesting: api.isIngesting(),
    hints: hints.slice(),
  };
  release({ ok: true, status: 200, text: function () { return Promise.resolve(JSON.stringify({ ok: true })); } });
  await tick();
  await tick();
  obs.doubleClickAfter = { ingesting: api.isIngesting(), queue: api.pendingQueue() };

  /* ---- 15. flushPending：补发失败要放回队首 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () { return Promise.reject(new Error('服务端满了')); };
  await api.flushPending();
  obs.flushFail = { postCalls: fetchCalls.length, queue: api.pendingQueue() };

  /* ---- 16. flushPending：通道没开 ⇒ 什么都不做 ---- */
  reset();
  api.state.ingest = null;                   // 通道关着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }]));
  fetchImpl = function () { return okResponse({ ok: true }); };
  await api.flushPending();
  obs.flushOff = { postCalls: fetchCalls.length, queue: api.pendingQueue() };

  /* ---- 17. flushPending：成功 ⇒ 补发掉并清空队列 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () { return okResponse({ ok: true }); };
  await api.flushPending();
  obs.flushOk = { postCalls: fetchCalls.length, queue: api.pendingQueue(), hints: hints.slice() };

  /* ---- 18. 页面打开（sources.json 到了）⇒ 读顶层 ingest 段（不新增请求） ---- */
  reset();
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'OLD' }]));
  const registry = {
    version: 1,
    perAward: 2,
    // per / cap 故意用**非默认值**（默认是 15 / 1440）⇒ 断言才不会「错因满足」
    sources: { koreader: { per: 20, maxPerDay: 600, enabled: true } },
    ingest: { url: 'https://worker.example/ingest', token: 'tok-abc', enabled: true },
  };
  fetchImpl = function (url) {
    if (String(url).indexOf('sources.json') >= 0) {
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve(registry); } });
    }
    return okResponse({ ok: true });
  };
  const reg = await api.loadRegistry();
  await tick();
  await tick();
  obs.registry = {
    ingestUrl: (api.state.ingest || {}).url || null,
    ingestEnabled: (api.state.ingest || {}).enabled === true,
    per: reg ? reg.per : null,
    cap: reg ? reg.cap : null,
    urls: fetchCalls.map(function (f) { return f.url; }),
  };

  /* ---- 19. 注册表拉不到 ⇒ 通道当没开（退回老办法），别炸 ---- */
  reset();
  api.state.ingest = INGEST;          // 先假设通道是开着的
  fetchImpl = function () { return Promise.reject(new Error('网络断了')); };
  const regFail = await api.loadRegistry();
  obs.registryFail = {
    ingest: api.state.ingest,
    per: regFail ? regFail.per : null,
    cap: regFail ? regFail.cap : null,
  };

  /* ---- 20. ★ 提交前那句状态提示：两种通道状态下文案必须不同 ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;
  const hintOn = api.statusHint();
  api.state.ingest = null;
  const hintOff = api.statusHint();
  obs.hints = { on: hintOn, off: hintOff, same: hintOn === hintOff };

  /* ---- 21. ★ 未来的日期 / 假日期要剔掉（服务端是**整份拒收**） ---- */
  reset();
  const many = {};
  for (let i = 0; i < 35; i += 1) {
    const d = new Date(Date.UTC(2026, 8, 27 + i));   // 2026-09-27 起 35 天（都是真日子）
    many[d.toISOString().slice(0, 10)] = 50;
  }
  const filtered = api.filterReportDays(Object.assign({}, many, {
    '2026-02-30': 50,       // 假日期（2 月没有 30 号）
    '2099-01-01': 50,       // 未来的日期
  }));
  obs.filter = {
    kept: Object.keys(filtered.days).sort(),
    dropped: filtered.dropped.map(d => d.day).sort(),
    keptCount: Object.keys(filtered.days).length,
  };

  /* ---- 22. ★ 页面真正要交上去的那份报告里不许有未来的 / 假的日子 ---- */
  reset();
  api.state.days = { '2026-11-25': 50, '2099-01-01': 30, '2026-02-30': 30 };
  api.recompute();
  obs.recomputed = {
    reportDays: api.state.reportDays,
    dropped: (api.state.dropped || []).map(d => d.day).sort(),
    credits: api.state.credits,
  };

  /* ---- 23. ★ 毒丸报告（服务端 400 拒收）不许堵住后面的报告 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([
    { v: 1, source: 'koreader', days: { '2099-01-01': 30 } },   // 服务端永远拒收
    { v: 1, source: 'koreader', days: { '2026-11-20': 50 } },   // 这条是好的
  ]));
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 400,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '日期在未来：2099-01-01' })); },
    });
  };
  await api.flushPending();
  obs.poison = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
  };

  /* ---- 23. ★ 注册表还没到就点「交上去」⇒ 什么都不做（不许降级成老办法） ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;                 // 通道其实是开着的（配置到了才知道）
  api.state.registryLoaded = false;           // ★ 注册表还没回来
  fetchImpl = function () { throw new Error('配置没到时不该发请求'); };
  await api.doSubmit();
  obs.registryRace = {
    postCalls: fetchCalls.length,
    opened: opened.slice(),
    hints: hints.slice(),
  };

  /* ---- 24. ★ 存储写不进去时，别再谎称「已存着」 ---- */
  reset();
  withDays(DAYS);
  api.state.ingest = INGEST;
  storage.throwSet = true;                 // ★ 这台设备的浏览器不让存
  fetchImpl = function () { return Promise.reject(new Error('网络抽风')); };
  await api.doSubmit();
  obs.storageRefused = {
    enqueueReturned: api.enqueueReport({ id: 'X' }),
    hints: hints.slice(),
    queue: api.pendingQueue(),
  };
  storage.throwSet = false;

  /* ---- 25. ★ 存储读不了时，补发要说真话 ---- */
  reset();
  api.state.ingest = INGEST;
  storage.throwGet = true;
  fetchImpl = function () { return Promise.resolve(okResponse({ ok: true })); };
  await api.flushPending();
  obs.storageUnreadable = { hints: hints.slice() };
  storage.throwGet = false;

  /* ---- 26. ★ 暂时性失败（5xx）⇒ 队列留着，后面的先不发 ---- */
  reset();
  api.state.ingest = INGEST;                 // 通道开着
  storage.setItem(PENDING_KEY, JSON.stringify([{ id: 'X' }, { id: 'Y' }]));
  fetchImpl = function () {
    return Promise.resolve({
      ok: false, status: 503,
      text: function () { return Promise.resolve(JSON.stringify({ ok: false, error: '服务端满了' })); },
    });
  };
  await api.flushPending();
  obs.tempFail = {
    postCalls: fetchCalls.length,
    queue: api.pendingQueue(),
    hints: hints.slice(),
  };

  process.stdout.write(JSON.stringify(obs));
}

main().catch(function (e) {
  process.stderr.write('KOReader 页探针跑挂了：' + String((e && e.stack) || e) + '\n');
  process.exitCode = 1;
});
"""


class KoreaderIngestBehaviorTest(unittest.TestCase):
    """KOReader 落地页的投递通道：**真跑一遍** app.js 的原函数，看行为对不对。

    ★ 这一页的 app.js 是 IIFE ⇒ 探针**直接 require 页面那份文件**，
      跑的就是页面真正在跑的那几个函数（不是抠出来重拼的）。
    """

    @classmethod
    def setUpClass(cls):
        cls.obs = run_probe(KO_JS, KO_NODE_PROBE)

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

    def test_the_queue_key_is_separated_per_source(self):
        """★ 三个子页同源 ⇒ 共用同一份 localStorage ⇒ 队列键必须按源分开。

        番茄钟页用的是 `pendingIngest`；背单词页是 `pendingIngest.words`；
        本页必须是 `pendingIngest.koreader`。
        不分开的话，几个页面的条目混在一个数组里 ⇒ 超 50 条时
        `slice(-50)` 会**静默丢掉别页最老的那条报告**。
        """
        iso = self.obs.get("isolated") or {}
        self.assertEqual(
            iso.get("key"),
            "pendingIngest.koreader",
            "KOReader 页的队列键必须是 pendingIngest.koreader（不许跟别的页共用）",
        )
        self.assertEqual(
            iso.get("own"),
            [{"id": "KO"}],
            "本页只该看得到自己那个队列",
        )
        self.assertEqual(
            iso.get("other"),
            [{"id": "POM"}],
            "番茄钟页的队列原封不动",
        )
        self.assertEqual(
            iso.get("otherWords"),
            [{"id": "WORDS"}],
            "背单词页的队列原封不动",
        )
        self.assertEqual(
            iso.get("otherAfter"),
            [{"id": "POM"}],
            "本页入队**不许**动番茄钟页的队列（否则会挤掉它的报告）",
        )
        self.assertEqual(
            iso.get("otherWordsAfter"),
            [{"id": "WORDS"}],
            "本页入队**不许**动背单词页的队列（否则会挤掉它的报告）",
        )
        self.assertEqual(
            iso.get("ownAfter"),
            [{"id": "KO"}, {"id": "KO2"}],
            "本页的条目只进本页的队列",
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

    def test_flush_sends_the_whole_queue_when_the_channel_works(self):
        """通道开着 ⇒ 补发掉整个队列，并告诉她补了几条。"""
        flush = self.obs.get("flushOk") or {}
        self.assertEqual(flush.get("postCalls"), 2, "队列里有两条 ⇒ 补发两次")
        self.assertEqual(flush.get("queue"), [], "补发成功的要出队")
        self.assertTrue(
            any("补发了 2 条" in t for t in flush.get("hints") or []),
            "补发完要告诉她补了几条（现在的提示：%r）" % (flush.get("hints"),),
        )

    # ---------------- 毒丸报告（永久失败不许堵队列） ----------------

    def test_a_permanently_rejected_report_does_not_block_the_queue(self):
        """★ 服务端**永久**拒收的那条（4xx）⇒ 丢掉它、**继续发下一条**。

        为什么：以前失败就 break ⇒ 一条永远交不上的报告能把整条队列堵死，
        排在它后面的、本来能交上去的报告也永远发不出去，而且**没有任何提示**。
        """
        poison = self.obs.get("poison") or {}
        self.assertEqual(
            poison.get("postCalls"), 2,
            "毒丸那条被拒之后，**后面那条也要发**（不能 break 停手）",
        )
        self.assertEqual(
            poison.get("queue"), [],
            "被服务端永久拒收的那条要丢掉（再试一万次也一样）",
        )
        self.assertTrue(
            any("已丢弃" in t for t in poison.get("hints") or []),
            "要告诉她「有 N 条服务端拒收，已丢弃」（现在的提示：%r）" % (poison.get("hints"),),
        )

    # ---------------- 存储写不进去时不许说谎 ----------------

    def test_the_enqueue_tells_the_caller_whether_it_saved(self):
        """★ enqueueReport 要**说真话**：存不住就返回 false。

        （调用方要靠它决定怎么说话 —— 说「已存着」而其实没存住，那份报告就真没了。）
        """
        obs = self.obs.get("storageRefused") or {}
        self.assertIs(
            obs.get("enqueueReturned"), False,
            "存不进去时 enqueueReport 必须返回 false",
        )

    def test_the_hint_does_not_claim_it_saved_when_the_storage_refuses(self):
        """★ 报告没存住时，提示**不许**说「已存着」。"""
        obs = self.obs.get("storageRefused") or {}
        texts = obs.get("hints") or []
        self.assertTrue(
            any("没存住" in t for t in texts),
            "存不住时要说「没存住」（现在的提示：%r）" % (texts,),
        )
        self.assertFalse(
            any("已存着" in t for t in texts),
            "存不住时**不许**说「已存着」（那份报告真的没了）：%r" % (texts,),
        )

    def test_the_flush_says_the_storage_is_unusable(self):
        """★ 存储读不了 ⇒ 待发队列用不了 ⇒ 必须说出来（不许假装一切正常）。"""
        obs = self.obs.get("storageUnreadable") or {}
        texts = obs.get("hints") or []
        self.assertTrue(
            any("队列" in t for t in texts),
            "存储不可用时要提示「待发队列用不了」（现在的提示：%r）" % (texts,),
        )

    # ---------------- 注册表还没到就点提交 ----------------

    def test_clicking_before_the_registry_arrives_does_nothing(self):
        """★ 注册表还没到就点「交上去」⇒ **什么都不做**。

        （以前会被静默降级成「打开 GitHub 新建 issue 页」—— 而 GitHub 恰是
        那条在国内经常抽风的路；提示还说「已打开提交页」⇒ 她会以为功能没上线。）
        """
        race = self.obs.get("registryRace") or {}
        self.assertEqual(race.get("postCalls"), 0, "配置没读到时一个 POST 都不许发")
        self.assertEqual(race.get("opened"), [], "配置没读到时不许打开 GitHub 新建 issue 页")
        self.assertTrue(
            any("还在读通道配置" in t for t in race.get("hints") or []),
            "要提示「还在读通道配置，稍等一下再点」（现在的提示：%r）" % (race.get("hints"),),
        )

    def test_a_temporarily_failed_report_keeps_the_queue(self):
        """★ 暂时性失败（5xx / 网络抽风）⇒ 放回队首、**停手**，后面的先不发。"""
        temp = self.obs.get("tempFail") or {}
        self.assertEqual(temp.get("postCalls"), 1, "暂时失败时要停手（后面的留着下次再发）")
        self.assertEqual(
            temp.get("queue"),
            [{"id": "X"}, {"id": "Y"}],
            "失败的那条要放回**队首**（后面的那条不能丢）",
        )
        self.assertEqual(temp.get("hints"), [], "一条都没发成功时不该说「补发了 N 条」")

    # ---------------- 客户端提前拦 ----------------

    def test_the_future_and_fake_days_are_dropped_before_sending(self):
        """★ 未来的日期 / 假日期要在客户端就剔掉 —— 服务端是**整份拒收**。"""
        f = self.obs.get("filter") or {}
        self.assertNotIn("2099-01-01", f.get("kept") or [], "未来的日期不许进报告")
        self.assertNotIn("2026-02-30", f.get("kept") or [], "假日期（2026-02-30）不许进报告")
        self.assertIn("2099-01-01", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")
        self.assertIn("2026-02-30", f.get("dropped") or [], "剔掉的天要记下来（页面上要说明）")

    def test_only_the_newest_thirty_days_are_sent(self):
        """★ 一次最多带 30 天（与服务端对齐）—— 超了只带**最近** 30 天。"""
        f = self.obs.get("filter") or {}
        self.assertEqual(f.get("keptCount"), 30, "超过 30 天时只带最近 30 天")

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
            {"v": 1, "source": "koreader", "days": {"2026-11-25": 50}},
            "请求体就是那份报告",
        )

    def test_a_rejected_post_raises(self):
        """服务端拒收（队列满了）⇒ postIngest 要抛，调用方才知道要排队。"""
        self.assertEqual(self.obs.get("postError"), "队列满了", "服务端拒收时要把错误抛出来")

    # ---------------- 点「交上去」 ----------------

    def test_the_old_way_is_used_when_the_channel_is_off(self):
        """通道没开 ⇒ 打开 GitHub 的新建 issue 页，**一个 POST 都不许发**。"""
        old = self.obs.get("oldWay") or {}
        self.assertEqual(
            len(old.get("opened") or []),
            1,
            "通道没开时要点开 GitHub 的新建 issue 页（老办法）",
        )
        self.assertTrue(
            (old.get("opened") or [""])[0].startswith(
                "https://github.com/lilia-shound/pokemon-dex/issues/new?"),
            "老办法要开的是 GitHub 的新建 issue 页：%r" % (old.get("opened"),),
        )
        self.assertEqual(old.get("postCalls"), 0, "通道没开时不许发投递请求")
        self.assertTrue(old.get("hints"), "老办法要点一下提示（告诉她还要再点一下 Submit）")

    def test_the_new_way_posts_once_when_the_channel_is_on(self):
        """通道开着 ⇒ 发一个 POST，成功就报「已记录」。"""
        new = self.obs.get("newWay") or {}
        self.assertEqual(new.get("postCalls"), 1, "点一次「交上去」只该发一个 POST")
        self.assertEqual(
            new.get("report"),
            {"v": 1, "source": "koreader", "days": {"2026-11-25": 50}},
            "投递的要是那份报告",
        )
        self.assertEqual(new.get("queue"), [], "成功了就不该留队列")
        self.assertFalse(new.get("ingesting"), "投递完要把「正在投递」的标志清掉")
        self.assertTrue(
            any("已记录" in t for t in new.get("hints") or []),
            "成功要原地提示「已记录」（现在的提示：%r）" % (new.get("hints"),),
        )

    def test_the_report_carries_the_clamped_days(self):
        """★ 投递要发**夹过单日上限**的那些天（不是 state.days 里的原值）。"""
        clamped = self.obs.get("clamped") or {}
        self.assertEqual(
            (clamped.get("report") or {}).get("days"),
            {"2026-11-25": 1440},
            "超过单日上限（1440）的那天要按 1440 报 —— 服务端会把超上限的整份丢掉",
        )
        self.assertEqual(
            clamped.get("raw"),
            {"2026-11-25": 9999},
            "页面上的原值不动（夹取只作用在发出去的那份报告上）",
        )

    def test_a_failed_post_queues_the_report(self):
        """投递失败 ⇒ 报告入队（等下次补发）。"""
        fail = self.obs.get("newWayFail") or {}
        self.assertEqual(fail.get("postCalls"), 1, "失败前确实发了一次")
        self.assertEqual(
            fail.get("queue"),
            [{"v": 1, "source": "koreader", "days": {"2026-11-25": 50}}],
            "投递失败的报告要存进待发队列",
        )
        self.assertFalse(fail.get("ingesting"), "失败之后也要把「正在投递」的标志清掉")
        self.assertTrue(
            any("已存着" in t for t in fail.get("hints") or []),
            "失败要告诉她「已存着，下次自动补发」（现在的提示：%r）" % (fail.get("hints"),),
        )

    def test_clicking_submit_twice_sends_only_one_post(self):
        """连点两次 ⇒ 只发一个 POST（workers.dev 在国内慢，很容易点第二下）。"""
        double = self.obs.get("doubleClick") or {}
        self.assertEqual(
            double.get("postCalls"),
            1,
            "连点两次「交上去」只能发一个 POST —— 两个都失败的话同一份报告会入队两次",
        )
        self.assertTrue(double.get("ingesting"), "投递期间应该有个「正在投递」的标志")
        self.assertTrue(
            any("稍等" in t for t in double.get("hints") or []),
            "重复点击要给她一句提示（现在的提示：%r）" % (double.get("hints"),),
        )
        after = self.obs.get("doubleClickAfter") or {}
        self.assertFalse(after.get("ingesting"), "投递结束后要清掉标志，否则她再也点不动")
        self.assertEqual(after.get("queue"), [], "投递成功就不该留队列")

    # ---------------- 提示 ----------------

    def test_the_status_hint_differs_between_the_two_channels(self):
        """★ 提交前那句状态提示：通道关着与开着**说法必须不同**。

        为什么：这是**两条完全不同的路**（老办法 vs 直投）⇒
        她看到的话必须不一样 —— 否则她不知道到底交没交上去。
        """
        hints = self.obs.get("hints") or {}
        self.assertTrue(hints.get("on"), "通道开着时也要有一句状态提示")
        self.assertTrue(hints.get("off"), "通道关着时也要有一句状态提示")
        self.assertFalse(
            hints.get("same"),
            "两种通道状态下的状态提示必须不同（现在两句话一样：%r）" % (hints.get("on"),),
        )
        self.assertIn("直接交", hints.get("on") or "", "通道开着 ⇒ 说「直接交」（不用登录 GitHub）")
        self.assertIn("Submit", hints.get("off") or "", "通道关着 ⇒ 说老办法（还要点一下 Submit）")

    # ---------------- 页面打开 ----------------

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置从**已经拉过的那份** sources.json 顶层读 ⇒ 不新增网络请求。"""
        reg = self.obs.get("registry") or {}
        self.assertEqual(
            reg.get("ingestUrl"),
            "https://worker.example/ingest",
            "ingest 配置要从 sources.json 顶层读（不新增网络请求）",
        )
        self.assertTrue(reg.get("ingestEnabled"), "ingest 段要照原样读进来")
        self.assertEqual(reg.get("per"), 20, "口径还是从同一份注册表读")
        self.assertEqual(reg.get("cap"), 600, "单日上限还是从同一份注册表读")
        self.assertEqual(
            len([u for u in (reg.get("urls") or []) if "sources.json" in u]),
            1,
            "注册表只许拉一次（ingest 配置从**同一份**里读）",
        )

    def test_a_broken_registry_leaves_the_channel_off(self):
        """注册表拉不到 ⇒ 通道当没开（退回老办法），别炸。"""
        fail = self.obs.get("registryFail") or {}
        self.assertIsNone(fail.get("ingest"), "拉不到注册表 ⇒ 没有通道配置 ⇒ 走老办法")
        self.assertEqual(fail.get("per"), 15, "拉不到注册表 ⇒ 退回兜底口径")
        self.assertEqual(fail.get("cap"), 1440, "拉不到注册表 ⇒ 退回兜底单日上限")


class KoreaderIngestContractTest(unittest.TestCase):
    """KOReader 落地页：源码层面的契约断言（行为由 KoreaderIngestBehaviorTest 真跑一遍）。"""

    @classmethod
    def setUpClass(cls):
        cls.js = KO_JS.read_text(encoding="utf-8")
        cls.html = KO_HTML.read_text(encoding="utf-8")

    def test_the_page_posts_to_the_ingest_endpoint(self):
        self.assertIn("postIngest", self.js, "要有 postIngest")
        self.assertIn("X-Dex-Token", self.js, "要带 ingest token 头")
        self.assertIn("method: 'POST'", self.js, "要用 POST")

    def test_the_page_reads_the_ingest_config_from_the_registry(self):
        """ingest 配置来自**已经拉过的** sources.json ⇒ 不许新增网络请求。"""
        self.assertTrue(
            re.search(r"d\.ingest|\['ingest'\]", self.js) is not None,
            "要从 sources.json 顶层读 ingest 段",
        )
        # 页面本来就有 1 个请求（sources.json），加了投递之后最多 2 个
        # —— **不许再多来一个注册表请求**
        self.assertLessEqual(
            len(re.findall(r"fetch\(", self.js)),
            2,
            "KOReader 落地页的网络请求不该变多（只有 sources.json 一个 + 投递那一个）",
        )

    def test_the_page_keeps_the_github_way_as_a_fallback(self):
        """老办法不许删 —— workers.dev 在国内可能抽风。"""
        self.assertIn("buildIssueUrl", self.js, "老办法的 URL 构造要留着")
        self.assertIn("oldWayBtn", self.html, "页面里要有老办法链接")
        self.assertIn("ingestAvailable", self.js, "通道没开时要能退回老办法")

    def test_the_submit_button_is_a_button_not_a_link(self):
        """主按钮改成 <button>（它不再跳去 GitHub）。"""
        self.assertRegex(self.html, r'<button[^>]*id="submitBtn"')
        self.assertNotRegex(self.html, r'<a[^>]*id="submitBtn"')

    def test_the_page_queues_failed_reports(self):
        """交不上去要存下来，下次打开自动补发。"""
        self.assertIn("pendingIngest", self.js, "要有待发队列")
        self.assertIn("flushPending", self.js, "页面打开时要补发")
        self.assertIn("localStorage", self.js, "待发队列要落 localStorage")

    def test_the_pending_queue_key_is_separated_per_source(self):
        """★ 三个子页同源 ⇒ 共用一份 localStorage ⇒ 队列键必须按源分开。"""
        self.assertIn(
            "pendingIngest.koreader",
            self.js,
            "KOReader 页的队列键必须是 pendingIngest.koreader（番茄钟页是 pendingIngest）",
        )
        self.assertNotRegex(
            self.js,
            r"PENDING_KEY\s*=\s*'pendingIngest'",
            "不许跟番茄钟页共用 pendingIngest —— 两个页面的条目会混在一个数组里",
        )

    def test_the_queue_can_put_a_report_back_at_the_head(self):
        """补发失败时要放回队首（unshift），不能只有 push。"""
        body = _function_body(self.js, "function enqueueReport(")
        self.assertIn("unshift", body, "enqueueReport 要能把报告放回队首")
        self.assertIn("push", body, "enqueueReport 也要能把报告排到队尾")

    def test_the_queue_keeps_the_newest_50(self):
        """队列上限：只留最近 50 条（slice(-50)），不是最老 50 条。"""
        self.assertIn(
            "slice(-50)",
            self.js,
            "待发队列超限时只留**最近** 50 条（slice(-50)）",
        )

    def test_the_page_has_an_in_flight_guard(self):
        """连点两次「交上去」⇒ 只发一个 POST。"""
        self.assertIn("let INGESTING", self.js, "要有一个「正在投递」的标志")
        body = _function_body(self.js, "async function doSubmit(")
        self.assertIn("if (INGESTING)", body, "提交入口要判重")
        self.assertIn("INGESTING = true", body, "发投递之前要把标志置上")
        self.assertIn("INGESTING = false", body, "finally 里要把标志清掉")
        self.assertIn("finally", body, "标志必须在 finally 里清（成功/失败/异常都要清）")

    def test_the_old_way_branch_comes_before_the_post(self):
        """doSubmit 里「通道没开 ⇒ 走老办法」的判断必须在 postIngest **之前**。"""
        body = _function_body(self.js, "async function doSubmit(")
        guard = body.find("!ingestAvailable()")
        post = body.find("postIngest(")
        self.assertGreaterEqual(guard, 0, "doSubmit 里要有「通道没开」的分支")
        self.assertGreater(post, guard, "「退回老办法」的判断必须在投递之前")
        self.assertIn("window.open(", body, "老办法要真的打开 GitHub 页面")


    def test_the_button_waits_for_the_registry(self):
        """★ 注册表还没读到 ⇒ 按钮要置灰（别让她点出一个老办法降级）。"""
        body = _function_body(self.js, "function renderDays(")
        self.assertIn("registryLoaded", body,
                      "按钮的禁用条件要看 registryLoaded（注册表没到就置灰）")

    def test_the_post_marks_permanent_failures(self):
        """★ 4xx（429 除外）= **永久**失败 ⇒ 调用方才能丢掉这条、继续发下一条。

        （分不出来的话，调用方只能把每一条都当成「下次还能成」
        ⇒ 一条永远交不上的报告就能把整条待发队列堵死。）
        """
        body = _function_body(self.js, "async function postIngest(")
        self.assertIn("status", body, "postIngest 要把 HTTP 状态码带出来")
        self.assertIn("permanent", body, "postIngest 要标出「这次失败是永久的」")
        self.assertIn("429", body, "429（限流）是暂时失败 ⇒ 那条不能丢")


    def test_the_page_flushes_the_queue_after_loading_the_registry(self):
        """页面打开时要调 flushPending（配置到了就补发）。"""
        body = _function_body(self.js, "function boot(")
        self.assertIn("flushPending()", body, "sources.json 到了之后要补发待发队列")
        # ★ 精确匹配「flushPending() 后面紧跟 .catch(」——
        #   只查「切片里有 .catch(」会被**别处**满足（错因满足：
        #   把 flushPending().catch(...) 换成 flushPending(); 测试照样绿）。
        self.assertRegex(
            body,
            r"flushPending\(\)\s*\.catch\(",
            "补发是异步的 ⇒ 调用处必须挂 .catch（flushPending() 后面要**紧跟** .catch(）",
        )

    def test_the_submit_call_site_catches_the_promise(self):
        """★ doSubmit() 是异步的 ⇒ 调用处必须挂 .catch（漏了控制台会红）。"""
        body = _function_body(self.js, "function bindSubmit(")
        self.assertRegex(
            body,
            r"doSubmit\(\)\s*\.catch\(",
            "提交按钮的点击处理里，doSubmit() 后面要**紧跟** .catch(",
        )

    def test_the_result_hint_is_cleared_with_a_full_redraw(self):
        """★ 结果提示作废时要**整页重画**（否则旧文案一直挂着）。"""
        body = _function_body(self.js, "function clearResult(")
        self.assertIn("lastSubmit = null", body, "作废要把结果提示清掉")
        self.assertRegex(
            body,
            r"ui\.render\(\)|renderDays\(\)",
            "作废之后必须重画一遍（只清 state 的话，旧提示会一直挂到下一次重画）",
        )

    def test_the_page_does_not_rewrite_the_hint_when_it_did_not_change(self):
        """★ 内容没变就别重写 DOM（重写会把「按下鼠标到松开」那一下点空）。"""
        body = _function_body(self.js, "function renderDays(")
        self.assertRegex(
            body,
            r"textContent\s*!==\s*msg",
            "提示行内容没变时不许重写（比较一下再写）",
        )

    @unittest.skipUnless(NODE, "这台机没有 node")
    def test_the_page_script_is_valid_javascript(self):
        """app.js 得是能跑的脚本（不能改坏成模块或半截代码）。"""
        proc = subprocess.run(
            [NODE, "--check", str(KO_JS)], capture_output=True, text=True
        )
        self.assertEqual(
            proc.returncode, 0, "app.js 语法有问题：\n%s" % proc.stderr[-2000:]
        )


class KoreaderSubmitHintBrowserTest(unittest.TestCase):
    """真实浏览器（Playwright）里，**提交提示行**（#submitHint）的行为断言。

    测四件零覆盖的事：

    1. **提示语按通道开关分** —— 通道关着点提交要说「已打开提交页…」（老办法），
       通道开着点提交要说「已记录」⇒ 两种状态下的文案**必须不同**。
    2. **内容没变就不重写 DOM** —— 重画时提示节点不许被换掉、内容不许被重写
       （重写会把「按下鼠标到松开」那一下点空）。
    3. **结果提示不 lingering** —— 提交成功后补录一天，那句「已记录 ✓」不许一直挂着。
    4. **页面打开时自动补发** —— 上次没交上去的，页面一开就补发掉。

    ★ 为什么不能只在 node 里测：这一页的 app.js 是 IIFE，node 探针顶不掉 DOM ⇒
      「点一下到底写出哪句话」「那个节点有没有被换掉」只能在真浏览器里看。
    """

    WIRE = "2026-11-21:25,2026-11-20:50"

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("没装 playwright，跳过提交提示行的浏览器测试")
        if not CHROME:
            raise unittest.SkipTest("找不到 chromium，跳过提交提示行的浏览器测试")
        cls.httpd = _make_server(KO_SITE_DIR)
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.base = cls.origin + "/koreader/index.html"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()

    def setUp(self):
        self.page_errors = []
        self.registry_response = None
        self.ingest_requests = []
        self.page = self._new_page()

    def tearDown(self):
        try:
            self.page.close()
        except Exception:
            pass

    # -- 辅助 -----------------------------------------------------------

    def _new_page(self):
        """开一个标签页：拦下注册表与投递请求，并把 window.open 顶掉。

        ★ window.open 顶掉的原因：老办法会 `window.open(github 的链接)` ⇒
          真开一个标签页去连外网（沙箱里连不上，还会挂在那儿）。
          这里只**记下**那个 URL。
        """
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.add_init_script("""
          window.__opened = [];
          window.open = function (u) { window.__opened.push(String(u)); return null; };
        """)

        def handle_registry(route):
            if self.registry_response is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(self.registry_response))

        def handle_ingest(route):
            self.ingest_requests.append({
                "url": route.request.url,
                "body": route.request.post_data,
            })
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "id": "mock-1"}))

        page.route("**/sources.json", handle_registry)
        page.route("**/ingest-mock", handle_ingest)
        return page

    def _registry(self, channel: bool) -> dict:
        """给页面看的一份 sources.json。

        channel=True  ⇒ 免登录通道**开着**（ingest 段有地址）
        channel=False ⇒ 通道**关着**（ingest 段空着 —— 部署前的常态）
        """
        reg = {
            "version": 1,
            "perAward": 2,
            "sources": {
                "koreader": {
                    "name": "KOReader",
                    "icon": "📚",
                    "unit": "分钟",
                    "per": 15,
                    "mode": "push",
                    "enabled": True,
                    "maxPerDay": 1440,
                }
            },
            "ingest": {"url": "", "token": "", "enabled": False},
        }
        if channel:
            reg["ingest"] = {
                "url": self.origin + "/ingest-mock",
                "token": "tok-mock",
                "enabled": True,
            }
        return reg

    def _open(self, page, channel: bool, wire: str = None):
        """打开页面并等**注册表真的生效**（口径与通道配置都换上去了）。"""
        self.registry_response = self._registry(channel)
        q = urllib.parse.quote(self.WIRE if wire is None else wire, safe="")
        page.goto(self.base + "?days=" + q, wait_until="commit")
        page.wait_for_function(
            "() => !!(window.KoreaderLanding && KoreaderLanding.state.registryLoaded)",
            timeout=30000,
        )

    def _hint(self, page) -> str:
        return page.inner_text("#submitHint")

    def _wait_hint(self, page, predicate: str, arg=None, timeout: int = 10000):
        """等提示行满足某个条件。

        ★ 等不到**不抛异常** —— 让后面的断言去报**实际文案**：
          超时错误只会说「等超时了」，断言失败会直接说「她看到的是哪句话」。
        """
        try:
            page.wait_for_function(predicate, arg=arg, timeout=timeout)
        except Exception:
            pass

    def _submit_once(self, channel: bool) -> str:
        """在指定通道状态下点一次「交上去」，返回提示行最后的文案。"""
        page = self._new_page()
        try:
            self._open(page, channel)
            before = self._hint(page)
            page.click("#submitBtn")
            # 等提示行**变了**（变了才算点出了结果）
            self._wait_hint(
                page,
                "(b) => (document.querySelector('#submitHint').textContent || '') !== b",
                arg=before,
            )
            return self._hint(page)
        finally:
            page.close()

    # -- 用例 -----------------------------------------------------------

    def test_the_submit_hint_differs_between_the_two_channels(self):
        """★ 通道关着 ⇒「已打开提交页…」；通道开着 ⇒「已记录」—— 文案必须不同。

        为什么：这是**两条完全不同的路**（老办法 vs 直投）⇒
        她看到的话必须不一样 —— 否则她不知道到底交没交上去。
        """
        off_text = self._submit_once(False)
        on_text = self._submit_once(True)
        self.assertIn(
            "已打开提交页", off_text,
            "通道关着 ⇒ 要说「已打开提交页」（老办法还要再点一下 Submit）",
        )
        self.assertIn("已记录", on_text, "通道开着 ⇒ 要说「已记录」")
        self.assertNotEqual(
            off_text, on_text,
            "两种通道状态下的提示文案必须不同（现在两句话一样：%r）" % (on_text,),
        )

    def test_the_status_hint_differs_between_the_two_channels(self):
        """★ **提交前**那句状态提示，通道关着与开着也要不一样。

        （点之前她看到的也是一句提示 —— 那句话得说清楚「点下去会发生什么」。）
        """
        page = self._new_page()
        try:
            self._open(page, channel=False)
            off_text = self._hint(page)
        finally:
            page.close()
        page = self._new_page()
        try:
            self._open(page, channel=True)
            on_text = self._hint(page)
        finally:
            page.close()
        self.assertNotEqual(
            off_text, on_text,
            "两种通道状态下的状态提示必须不同（现在两句话一样：%r）" % (on_text,),
        )
        self.assertIn("直接交", on_text, "通道开着 ⇒ 说「直接交」（不用登录 GitHub）")
        self.assertIn("Submit", off_text, "通道关着 ⇒ 说老办法（还要点一下 Submit）")

    def test_the_hint_node_is_not_rebuilt_when_the_page_redraws(self):
        """★ 重画时提示行**不许被重建**、内容**不许被重写**。

        测法：给提示节点打一个标记，再存下它那个**文本节点的引用**，
        然后触发三次**内容没变**的重画 ⇒
        - 节点上的标记还在 ⇒ 那个节点没被换掉；
        - 文本节点还是**同一个对象** ⇒ 那段内容没被重写
          （重写会把文本节点换掉 —— 换掉的话，她「按下鼠标到松开」之间
          点到的就不是刚才那个元素了）。

        ★ 文本节点用「比身份」而不是「打标记」来测：往文本节点上加标记会改掉
          `textContent` ⇒ 页面那句「内容没变就别重写」的判断就永远不成立 ⇒
          测出来的是「内容变了所以重写」，不是「内容没变却重写了」。
        """
        page = self._new_page()
        try:
            self._open(page, channel=False)
            marked = page.evaluate("""
              (() => {
                const el = document.querySelector('#submitHint');
                el.dataset.mark = 'x';
                // 存下文本节点的**引用**（只存引用，不动它的内容）
                window.__hintText = el.firstChild;
                return { hasText: !!window.__hintText };
              })()
            """)
            self.assertTrue(marked.get("hasText"), "提示里没有文本节点了 —— 这条用例测不到「内容没被重写」")
            # 三次重画：内容不变（通道关着 ⇒ 状态说明那句话不变）
            page.evaluate("""() => {
              KoreaderLanding.ui.render();
              KoreaderLanding.ui.render();
              KoreaderLanding.ui.render();
            }""")
            after = page.evaluate("""
              (() => {
                const el = document.querySelector('#submitHint');
                return {
                  nodeMark: el.dataset.mark || null,
                  sameText: el.firstChild === window.__hintText,
                };
              })()
            """)
        finally:
            page.close()
        self.assertEqual(after["nodeMark"], "x", "提示节点被重建了（标记没了）")
        self.assertTrue(
            after["sameText"],
            "提示内容被重写了（文本节点被换掉了）—— 内容没变就不该重写",
        )

    def test_the_result_hint_does_not_linger_after_adding_a_day(self):
        """提交成功后补录一天 ⇒「已记录 ✓」不许一直挂着。

        （只清 state 不重画的话，那句话会一直挂到下一次重画为止。）
        """
        page = self._new_page()
        try:
            self._open(page, channel=True)
            page.click("#submitBtn")
            self._wait_hint(
                page,
                "() => (document.querySelector('#submitHint').textContent || '').indexOf('已记录') >= 0",
            )
            # 补录一天（手动加一天）
            page.fill("#manualDate", "2026-11-19")
            page.fill("#manualMinutes", "30")
            page.click("#manualAdd")
            # 等提示行不再说「已记录」
            self._wait_hint(
                page,
                "() => (document.querySelector('#submitHint').textContent || '').indexOf('已记录') < 0",
            )
            hint = self._hint(page)
        finally:
            page.close()
        self.assertNotIn(
            "已记录", hint,
            "补录了一天之后「已记录 ✓」还挂着 —— 那是**过时**的提示（她已经换了数）",
        )

    def test_the_page_flushes_the_queue_when_it_opens(self):
        """页面打开 ⇒ 把上次没交上去的补发掉。"""
        page = self._new_page()
        pending = {"v": 1, "source": "koreader", "days": {"2026-11-20": 50}}
        page.add_init_script(
            "window.localStorage.setItem('pendingIngest.koreader', JSON.stringify([%s]));" % json.dumps(pending)
        )
        try:
            self._open(page, channel=True)
            # 等补发真的发出去了
            self._wait_hint(page, "() => window.__opened.length >= 0")
            for _ in range(50):
                if self.ingest_requests:
                    break
                page.wait_for_timeout(100)
        finally:
            page.close()
        self.assertTrue(self.ingest_requests, "页面打开时没有补发待发队列")
        body = json.loads(self.ingest_requests[0]["body"])
        self.assertEqual(body, pending, "补发发出去的应该是队列里那条报告")

    def test_the_page_has_no_uncaught_error(self):
        """改完重画逻辑之后，页面不许有未捕获的异常。"""
        self._open(self.page, channel=True)
        self.page.click("#submitBtn")
        self._wait_hint(
            self.page,
            "() => (document.querySelector('#submitHint').textContent || '').indexOf('已记录') >= 0",
        )
        self.page.fill("#manualDate", "2026-11-19")
        self.page.fill("#manualMinutes", "30")
        self.page.click("#manualAdd")
        self.assertEqual(self.page_errors, [])


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# 注册表竞态 —— **异步** Playwright
#
# ★ 为什么必须用异步版：
#   同步版会把 route 回调和 click() 串在同一条线程上 ⇒
#   「把 sources.json 延迟 3 秒」根本插不进点击前面 ⇒
#   测出来的是「点的时候响应早就到了」⇒ 这条用例永远绿。
# --------------------------------------------------------------------------

class RegistryRaceBrowserTest(unittest.IsolatedAsyncioTestCase):
    """注册表（sources.json）还没到就点「交上去」⇒ **不许**被静默降级成老办法。

    为什么：以前「还没读到配置」与「通道被关了」被当成同一件事
    ⇒ 她明明能直投，却拿到 GitHub 新建 issue 页（而 GitHub 恰是那条
    在国内经常抽风的路），提示还说「已打开提交页」⇒ 她会以为功能没上线。
    """

    async def asyncSetUp(self):
        if not HAVE_ASYNC_PLAYWRIGHT:
            self.skipTest("没装 playwright 的异步接口，跳过注册表竞态的浏览器测试")
        if not CHROME:
            self.skipTest("找不到 chromium，跳过注册表竞态的浏览器测试")
        self.httpd = _make_server(KO_SITE_DIR)
        self.origin = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

    async def asyncTearDown(self):
        try:
            await self.browser.close()
        finally:
            await self._pw.stop()
            self.httpd.shutdown()
            self.httpd.server_close()

    async def _race(self, page_path, api_name, source_id, ready_selector, query="", fill=None):
        """把注册表**延迟 3 秒**，然后在它到达之前点「交上去」。"""
        reg = {
            "version": 1,
            "perAward": 2,
            "sources": {source_id: {"per": 15, "maxPerDay": 1440, "enabled": True}},
            "ingest": {
                "url": self.origin + "/ingest-mock",
                "token": "tok-mock",
                "enabled": True,
            },
        }
        posts = []

        async def handle_registry(route):
            await asyncio.sleep(3)          # ★ 延迟：让点击插在它前面
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(reg))

        async def handle_ingest(route):
            posts.append(route.request.url)
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"ok": True, "id": "mock-1"}))

        page = await self.browser.new_page(viewport={"width": 390, "height": 844})
        await page.add_init_script("""
          window.__opened = [];
          window.open = function (u) { window.__opened.push(String(u)); return null; };
        """)
        await page.route("**/sources.json", handle_registry)
        await page.route("**/ingest-mock", handle_ingest)
        try:
            await page.goto(self.origin + page_path + query, wait_until="commit")
            await page.wait_for_selector(ready_selector, timeout=20000)
            if fill:
                await page.fill("#countInput", fill)
            # ★ 注册表还没到 ⇒ 按钮是灰的
            disabled = await page.get_attribute("#submitBtn", "aria-disabled")
            # 绕过被禁用的按钮，直接调 doSubmit（看它自己会不会乱来）
            await page.evaluate("() => %s.doSubmit()" % api_name)
            await asyncio.sleep(0.4)
            hint = await page.inner_text("#submitHint")
            opened = await page.evaluate("() => window.__opened")
        finally:
            await page.close()
        return {"disabled": disabled, "posts": posts, "opened": opened, "hint": hint}

    async def test_the_koreader_page_waits_for_the_registry(self):
        """★ KOReader 页：注册表还没到 ⇒ 按钮灰着、一个 POST 都不发、不开 GitHub 页。"""
        r = await self._race("/koreader/index.html", "KoreaderLanding", "koreader",
                             "#dayList li",
                             query="?days=" + urllib.parse.quote("2026-11-21:25,2026-11-20:50", safe=""))
        self.assertEqual(r["disabled"], "true", "注册表还没读到时按钮要置灰")
        self.assertEqual(r["posts"], [], "配置没读到时一个 POST 都不许发")
        self.assertEqual(r["opened"], [], "配置没读到时不许打开 GitHub 新建 issue 页")
        self.assertIn("还在读通道配置", r["hint"],
                      "要提示「还在读通道配置，稍等一下再点」（现在的提示：%r）" % (r["hint"],))

    async def test_the_words_page_waits_for_the_registry(self):
        """★ 背单词页：同上。"""
        r = await self._race("/words/index.html", "WordsLanding", "words",
                             "#countInput", fill="150")
        self.assertEqual(r["disabled"], "true", "注册表还没读到时按钮要置灰")
        self.assertEqual(r["posts"], [], "配置没读到时一个 POST 都不许发")
        self.assertEqual(r["opened"], [], "配置没读到时不许打开 GitHub 新建 issue 页")
        self.assertIn("还在读通道配置", r["hint"],
                      "要提示「还在读通道配置，稍等一下再点」（现在的提示：%r）" % (r["hint"],))
