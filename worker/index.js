/**
 * 训练家图鉴 · 免登录投递接收端（Cloudflare Worker）。
 *
 * 四个端点：
 *   POST /ingest   客户端交报告（X-Dex-Token）
 *   GET  /pull     cron 拉队列（X-Dex-Admin）
 *   POST /ack      cron 确认已并账（X-Dex-Admin）
 *   GET  /health   排查用（不鉴权）
 *
 * ★ 校验逻辑**不在这里** —— 全在 lib/validate.cjs（node 也能单测），
 *   这里只做「取 header、拼 KV 读写、拼响应」。
 * ★ CORS 头**每条响应都要带**（包括 401/404/500）——
 *   少了浏览器会把响应整个吞掉，前端只看到「网络错误」，看不出哪里不对。
 * ★ **未捕获的异常一律在 handleRequest 顶层兜住** ⇒ 自己回 500（带 CORS 头）——
 *   绝不能让它抛给 Cloudflare（那会回一个**不带 CORS 头**的默认 500，
 *   浏览器照样把响应整个吞掉 ⇒ 前端只看到「网络错误」）。
 */

import validate from './lib/validate.cjs';
import rules from './lib/rules.cjs';

const { validateReport, todayCst } = validate;
const RULES = rules.RULES;

/**
 * 队列最多留多少条（防刷：别人拿公开的 ingest token 灌数据）。
 *
 * ★ 这是**软上限**（尽力而为），**不是硬保证**：
 *   KV 没有原子操作、`list()` 又是最终一致的 ⇒ 并发写入时可能短暂超出一条
 *   （实测：队列 198 条时 5 个并发 ingest 全部返回 200 ⇒ 队列变成 203 条）。
 *   ⇒ 队列满时新报告会**收不下**（429），客户端得留在待发队列里重试。
 * ★ 想要硬保证得上 Durable Object / 计数键 CAS —— 超出本任务范围，不做。
 */
const MAX_QUEUE = 200;
/** 队列条目的存活时间（秒）—— 14 天。 */
const TTL_SECONDS = 60 * 60 * 24 * 14;

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,X-Dex-Token,X-Dex-Admin',
  'Access-Control-Max-Age': '86400',
};

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: Object.assign({ 'Content-Type': 'application/json; charset=utf-8' }, CORS_HEADERS),
  });
}

function bearer(request, name) {
  return request.headers.get(name) || '';
}

/**
 * 取 KV 绑定。没绑（wrangler 配置漏了 binding）时回 503 ——
 * 这是服务端配置问题，不该让请求一路走到 `env.INGEST_KV.list` 上炸成 TypeError。
 * ★ 四个端点都要先过这一关 ⇒ 同一个故障，四条路都回同一种响应。
 */
function kvBinding(env) {
  return env && env.INGEST_KV ? env.INGEST_KV : null;
}

async function handleIngest(request, env) {
  if (!env.INGEST_TOKEN || bearer(request, 'X-Dex-Token') !== env.INGEST_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  const kv = kvBinding(env);
  if (!kv) return json({ ok: false, error: '队列存储没绑定' }, 503);
  let body = null;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'body 不是合法 JSON' }, 400);
  }
  const v = validateReport(body, RULES, todayCst(Date.now()));
  if (!v.ok) {
    return json({ ok: false, error: v.error }, 400);
  }
  // ★ 队列列表读失败 ⇒ 503（spec §9 的错误表：KV 出问题一律 503）。
  //   不 catch 就是未捕获异常 ⇒ 兜底 500（虽然也带 CORS 头，但客户端分不清
  //   「队列满了（要重试）」和「服务端炸了（也要重试）」之外的原因）。
  let listed;
  try {
    listed = await kv.list({ prefix: 'q:', limit: MAX_QUEUE });
  } catch (e) {
    return json({ ok: false, error: '队列列表读取失败' }, 503);
  }
  // ★ 上限检查放在**真正写入之前**（满了就一条都不进）。
  //   注意这是软上限（见 MAX_QUEUE 的注释）：并发下可能短暂超一点。
  if (listed.keys.length >= MAX_QUEUE) {
    return json({ ok: false, error: '队列满了（' + MAX_QUEUE + ' 条）' }, 429);
  }
  const id = 'q:' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  const record = {
    id: id,
    body: { v: 1, source: v.source, days: v.days },
    receivedAt: new Date().toISOString(),
  };
  // ★ KV 写失败要回 503（spec §9 的错误表）—— 不 catch 就是 500，
  //   客户端分不清「队列满了（要重试）」和「服务端炸了（也要重试）」。
  try {
    await kv.put(id, JSON.stringify(record), { expirationTtl: TTL_SECONDS });
  } catch (e) {
    return json({ ok: false, error: '队列写入失败' }, 503);
  }
  return json({ ok: true, id: id, receivedAt: record.receivedAt });
}

async function handlePull(request, env) {
  if (!env.ADMIN_TOKEN || bearer(request, 'X-Dex-Admin') !== env.ADMIN_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  const kv = kvBinding(env);
  if (!kv) return json({ ok: false, error: '队列存储没绑定' }, 503);
  let listed;
  try {
    listed = await kv.list({ prefix: 'q:', limit: MAX_QUEUE });
  } catch (e) {
    return json({ ok: false, error: '队列列表读取失败' }, 503);
  }
  const items = [];
  for (const key of listed.keys) {
    let raw;
    try {
      raw = await kv.get(key.name, 'text');
    } catch (e) {
      return json({ ok: false, error: '队列条目读取失败' }, 503);
    }
    if (!raw) continue;
    try {
      items.push(JSON.parse(raw));
    } catch (e) {
      // 坏条目跳过（别让一条垃圾把整批拉取搞挂）
    }
  }
  return json({ ok: true, items: items });
}

async function handleAck(request, env) {
  if (!env.ADMIN_TOKEN || bearer(request, 'X-Dex-Admin') !== env.ADMIN_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  const kv = kvBinding(env);
  if (!kv) return json({ ok: false, error: '队列存储没绑定' }, 503);
  let body = null;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'body 不是合法 JSON' }, 400);
  }
  // ★ body 必须是 JSON 对象（跟 /ingest 同一个口径）：
  //   body 是 `null` / 数组 / 数字时直接 400，别让它一路走到 `body.ids` 上
  //   炸成 TypeError（那会变成兜底 500，前端看不出是「你发错了」）。
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return json({ ok: false, error: 'body 不是 JSON 对象' }, 400);
  }
  const ids = (body && Array.isArray(body.ids)) ? body.ids : [];
  let removed = 0;
  for (const id of ids) {
    if (typeof id !== 'string' || !id.startsWith('q:')) continue;
    // ★ removed 要数**真删掉的**条数 ⇒ 先 get 确认它在，再 delete。
    //   （以前是「请求删了几条」—— 键本来就不在队列里也算 +1 ⇒ 数字虚高。）
    //   每轮最多 200 条、3 小时一次，多几次读完全够用。
    let existed;
    try {
      existed = await kv.get(id, 'text');
    } catch (e) {
      return json({ ok: false, error: '队列条目读取失败' }, 503);
    }
    if (existed === null || existed === undefined) continue;   // 本来就不在队列里
    try {
      await kv.delete(id);
    } catch (e) {
      return json({ ok: false, error: '队列条目删除失败' }, 503);
    }
    removed++;
  }
  return json({ ok: true, removed: removed });
}

async function handleHealth(env) {
  const kv = kvBinding(env);
  if (!kv) return json({ ok: false, error: '队列存储没绑定' }, 503);
  let listed;
  try {
    listed = await kv.list({ prefix: 'q:', limit: MAX_QUEUE });
  } catch (e) {
    return json({ ok: false, error: '队列列表读取失败' }, 503);
  }
  return json({ ok: true, pending: listed.keys.length, source: 'pokemon-dex-ingest' });
}

/**
 * 路由。★ `OPTIONS` 要在所有路径之前处理（预检不带路径语义）。
 *
 * ★ 整个函数体包在 try/catch 里 ⇒ **兜底 500（带 CORS 头）**：
 *   未捕获的异常一旦抛给 Cloudflare，它回的是**不带 CORS 头**的默认 500
 *   ⇒ 浏览器把响应整个吞掉 ⇒ 前端只看到「网络错误」，连「服务端出错」都读不到。
 */
export async function handleRequest(request, env) {
  try {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (path === '/ingest' && request.method === 'POST') return await handleIngest(request, env);
    if (path === '/pull' && request.method === 'GET') return await handlePull(request, env);
    if (path === '/ack' && request.method === 'POST') return await handleAck(request, env);
    if (path === '/health' && request.method === 'GET') return await handleHealth(env);
    return json({ ok: false, error: '没有这个接口' }, 404);
  } catch (e) {
    return json({ ok: false, error: '服务端错误' }, 500);
  }
}

export default { fetch: handleRequest };
