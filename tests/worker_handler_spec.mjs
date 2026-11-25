/* Worker 入口的 node 单测：假 env + 假 KV，不联网、不碰 Cloudflare。
   跑法：node tests/worker_handler_spec.mjs */

import assert from 'node:assert';
import { handleRequest } from '../worker/index.js';
import defaultExport from '../worker/index.js';

/** 假的 KV：只实现 Worker 用到的那几个方法。 */
function fakeKV() {
  const map = new Map();
  return {
    map,
    async get(key) { return map.has(key) ? map.get(key) : null; },
    async put(key, value) { map.set(key, value); },
    async delete(key) { map.delete(key); },
    async list({ prefix, limit }) {
      const keys = [...map.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name }));
      return { keys: keys.slice(0, limit) };
    },
  };
}

const ENV = { INGEST_TOKEN: 'tok-ingest', ADMIN_TOKEN: 'tok-admin' };

function post(path, body, headers) {
  return new Request('https://w.example' + path, {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
    body: JSON.stringify(body),
  });
}

async function call(req, env) {
  const resp = await handleRequest(req, env || ENV);
  const text = await resp.text();
  return { status: resp.status, data: text ? JSON.parse(text) : null, headers: resp.headers };
}

// ── CORS 预检 ────────────────────────────────────────────
{
  const resp = await handleRequest(new Request('https://w.example/ingest', { method: 'OPTIONS' }), ENV);
  assert.strictEqual(resp.status, 204);
  assert.strictEqual(resp.headers.get('Access-Control-Allow-Origin'), '*');
  assert.ok((resp.headers.get('Access-Control-Allow-Headers') || '').indexOf('X-Dex-Token') >= 0);
}

// ── ingest：凭证不对 ─────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'wrong' }), env);
  assert.strictEqual(r.status, 401);
}

// ── ingest：正常 ─────────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.ok, true);
  assert.ok(r.data.id.startsWith('q:'));
  assert.strictEqual(env.INGEST_KV.map.size, 1);
}

// ── ingest：非法 body ────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ingest', { v: 1, source: 'weread', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 400);
  assert.strictEqual(r.data.ok, false);
}

// ── pull：凭证不对 / 正常 ────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const bad = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'nope' } }), env);
  assert.strictEqual(bad.status, 401);

  await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  const good = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'tok-admin' } }), env);
  assert.strictEqual(good.status, 200);
  assert.strictEqual(good.data.items.length, 1);
  assert.strictEqual(good.data.items[0].body.source, 'pom');
  assert.deepStrictEqual(good.data.items[0].body.days, { '2026-11-21': 37 });
}

// ── ack：删掉条目 ────────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const ins = await call(post('/ingest', { v: 1, source: 'pom', days: { '2026-11-21': 37 } }, { 'X-Dex-Token': 'tok-ingest' }), env);
  const id = ins.data.id;
  const ack = await call(post('/ack', { ids: [id] }, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(ack.status, 200);
  assert.strictEqual(ack.data.removed, 1);
  assert.strictEqual(env.INGEST_KV.map.size, 0);
}

// ── health / 未知接口 ────────────────────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const h = await call(new Request('https://w.example/health'), env);
  assert.strictEqual(h.status, 200);
  assert.strictEqual(h.data.pending, 0);
  const u = await call(new Request('https://w.example/nope'), env);
  assert.strictEqual(u.status, 404);
}

// ══════════════════════════════════════════════════════════
// 额外断言（简报之外补的）—— 补的是「代码里写了但没人验」的地方
// ══════════════════════════════════════════════════════════

/** 假 KV 的变体：把 put 的第三个参数（options）记下来。 */
function recordingKV() {
  const kv = fakeKV();
  const puts = [];
  return {
    map: kv.map,
    puts,
    async get(key) { return kv.get(key); },
    async put(key, value, options) { puts.push({ key, value, options }); return kv.put(key, value); },
    async delete(key) { return kv.delete(key); },
    async list(opts) { return kv.list(opts); },
  };
}

const REPORT = { v: 1, source: 'pom', days: { '2026-11-21': 37 } };

// ── wrangler 要的形状：默认导出 { fetch: handleRequest } ──
{
  assert.strictEqual(typeof defaultExport.fetch, 'function');
  assert.strictEqual(defaultExport.fetch, handleRequest);
}

// ── 坏条目不许把整批拉取搞挂（代码里那个 try/catch 得有人验）──
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  env.INGEST_KV.map.set('q:bad', '这不是 JSON');
  await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  const r = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'tok-admin' } }), env);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.items.length, 1);          // 坏的那条被跳过，好的一条还在
  assert.strictEqual(r.data.items[0].body.source, 'pom');
}

// ── 队列满了要拒收（429），且不许静默丢数据 ──────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  for (let i = 0; i < 200; i++) env.INGEST_KV.map.set('q:seed-' + i, '{}');   // MAX_QUEUE = 200
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 429);
  assert.strictEqual(env.INGEST_KV.map.size, 200);      // 满了就是一条都不进
}

// ── 写进队列的条目要带 TTL（14 天）──────────────────────
{
  const kv = recordingKV();
  const env = { ...ENV, INGEST_KV: kv };
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(kv.puts.length, 1);
  assert.strictEqual(kv.puts[0].key, r.data.id);
  assert.strictEqual(kv.puts[0].options.expirationTtl, 60 * 60 * 24 * 14);
}

// ── KV 写失败要回 503（spec §9 的错误表）────────────────
{
  const kv = fakeKV();
  kv.put = async () => { throw new Error('KV 炸了'); };
  const env = { ...ENV, INGEST_KV: kv };
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.ok, false);
}

// ── ack 只许动队列里的条目（别的 KV key 不许被 ack 删掉）──
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  env.INGEST_KV.map.set('别的键', 'x');
  const r = await call(post('/ack', { ids: ['别的键', 42] }, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.removed, 0);       // 两个 id 都不在 q: 命名空间里
  assert.strictEqual(env.INGEST_KV.map.size, 1); // 那个键还在
}

// ── CORS 头每条响应都要带（401/404 也带）────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r401 = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'wrong' }), env);
  assert.strictEqual(r401.status, 401);
  assert.strictEqual(r401.headers.get('Access-Control-Allow-Origin'), '*');
  const r404 = await call(new Request('https://w.example/nope'), env);
  assert.strictEqual(r404.status, 404);
  assert.strictEqual(r404.headers.get('Access-Control-Allow-Origin'), '*');
}

// ── 路径末尾多个斜杠也算同一个接口 ─────────────────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(new Request('https://w.example/pull/', { headers: { 'X-Dex-Admin': 'tok-admin' } }), env);
  assert.strictEqual(r.status, 200);
}

console.log('Worker 入口：全部通过');
