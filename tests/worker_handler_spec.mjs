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
  const allow = resp.headers.get('Access-Control-Allow-Headers') || '';
  assert.ok(allow.indexOf('X-Dex-Token') >= 0);
  // ★ X-Dex-Admin 也要在 —— 少了这个头，浏览器预检直接失败 ⇒ 客户端连都连不上。
  assert.ok(allow.indexOf('X-Dex-Admin') >= 0);
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
// ★ 这是**软上限**（尽力而为，不是硬保证）：KV 没有原子操作、list() 又是最终一致的
//   ⇒ 并发写入时可能短暂超出一条（实测：队列 198 条时 5 个并发 ingest 全部 200 ⇒ 203 条）。
//   ★ 想要硬保证得上 Durable Object / 计数键 CAS —— 超出本任务范围，不做。
//   ⇒ 这里只验「单条请求看到满队列时」的行为：拒收（429）且**一条都不进**
//   （上限检查在真正写入之前）。
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

// ══════════════════════════════════════════════════════════
// 审查发现（修复轮 1）—— 补的是「代码里写了但没人验」的地方
// ══════════════════════════════════════════════════════════

// ── 顶层兜底：未捕获的异常 ⇒ 500，**而且必须带 CORS 头** ──
//   （不带 CORS 头的 500 会被浏览器整个吞掉 ⇒ 前端只看到「网络错误」）
{
  const kv = fakeKV();
  kv.list = async () => null;                 // 返回形状不对 ⇒ 取 keys 时抛 TypeError
  const env = { ...ENV, INGEST_KV: kv };
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 500);
  assert.strictEqual(r.data.ok, false);
  assert.strictEqual(r.data.error, '服务端错误');
  assert.strictEqual(r.headers.get('Access-Control-Allow-Origin'), '*');   // ★ 兜底也得带 CORS 头
}

// ── KV 没绑 ⇒ 503（而不是崩成没 CORS 头的默认 500）────────
{
  const env = { ...ENV };                     // 没有 INGEST_KV
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.ok, false);
  assert.strictEqual(r.headers.get('Access-Control-Allow-Origin'), '*');
}

// ── KV 的 list 抛错 ⇒ 503（文案说清是哪个动作失败）────────
{
  const kv = fakeKV();
  kv.list = async () => { throw new Error('KV 炸了'); };
  const env = { ...ENV, INGEST_KV: kv };
  const r = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.error, '队列列表读取失败');
  assert.strictEqual(r.headers.get('Access-Control-Allow-Origin'), '*');
}

// ── KV 的 get 抛错 ⇒ 503（pull 侧）────────────────────────
{
  const kv = fakeKV();
  const env = { ...ENV, INGEST_KV: kv };
  await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);   // 先塞一条
  kv.get = async () => { throw new Error('KV 炸了'); };
  const r = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'tok-admin' } }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.error, '队列条目读取失败');
}

// ── KV 的 get 抛错 ⇒ 503（ack 侧）─────────────────────────
{
  const kv = fakeKV();
  const env = { ...ENV, INGEST_KV: kv };
  const ins = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  kv.get = async () => { throw new Error('KV 炸了'); };
  const r = await call(post('/ack', { ids: [ins.data.id] }, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.error, '队列条目读取失败');
}

// ── KV 的 delete 抛错 ⇒ 503（ack 侧）──────────────────────
{
  const kv = fakeKV();
  const env = { ...ENV, INGEST_KV: kv };
  const ins = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  kv.delete = async () => { throw new Error('KV 炸了'); };
  const r = await call(post('/ack', { ids: [ins.data.id] }, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.data.error, '队列条目删除失败');
}

// ── /ack 的 body 是 JSON 字面量 null ⇒ 400（不是崩）────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r = await call(post('/ack', null, { 'X-Dex-Admin': 'tok-admin' }), env);   // body = "null"
  assert.strictEqual(r.status, 400);
  assert.strictEqual(r.data.ok, false);
  assert.strictEqual(r.headers.get('Access-Control-Allow-Origin'), '*');
}

// ── /ack 的 body 不是 JSON 对象 ⇒ 400（数组、数字都算）────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const r1 = await call(post('/ack', [1, 2], { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(r1.status, 400);
  const r2 = await call(post('/ack', 42, { 'X-Dex-Admin': 'tok-admin' }), env);
  assert.strictEqual(r2.status, 400);
}

// ── removed 数的是「真删掉的」，不是「请求删了几条」──────────
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const ins = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-ingest' }), env);
  const r = await call(
    post('/ack', { ids: [ins.data.id, 'q:根本不存在的条目'] }, { 'X-Dex-Admin': 'tok-admin' }),
    env,
  );
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.removed, 1);          // 只数真删掉的那一条
  assert.strictEqual(env.INGEST_KV.map.size, 0);
}

// ── 两个 token 不能互绕（ingest 的头不能当 admin 用，反之亦然）──
{
  const env = { ...ENV, INGEST_KV: fakeKV() };
  const pull = await call(new Request('https://w.example/pull', { headers: { 'X-Dex-Admin': 'tok-ingest' } }), env);
  assert.strictEqual(pull.status, 401);           // 拿 ingest token 当 admin ⇒ 拒
  const ack = await call(post('/ack', { ids: [] }, { 'X-Dex-Admin': 'tok-ingest' }), env);
  assert.strictEqual(ack.status, 401);
  const ing = await call(post('/ingest', REPORT, { 'X-Dex-Token': 'tok-admin' }), env);
  assert.strictEqual(ing.status, 401);            // 拿 admin token 当 ingest ⇒ 拒
}

console.log('Worker 入口：全部通过');
