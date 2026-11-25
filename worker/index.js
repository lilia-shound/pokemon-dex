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
 * ★ CORS 头**每条响应都要带**（包括 401/404）——
 *   少了浏览器会把响应整个吞掉，前端只看到「网络错误」，看不出哪里不对。
 */

import validate from './lib/validate.cjs';
import rules from './lib/rules.cjs';

const { validateReport, todayCst } = validate;
const RULES = rules.RULES;

/** 队列最多留多少条（防刷：别人拿公开的 ingest token 灌数据）。 */
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

async function handleIngest(request, env) {
  if (!env.INGEST_TOKEN || bearer(request, 'X-Dex-Token') !== env.INGEST_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
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
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
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
    await env.INGEST_KV.put(id, JSON.stringify(record), { expirationTtl: TTL_SECONDS });
  } catch (e) {
    return json({ ok: false, error: '队列写入失败' }, 503);
  }
  return json({ ok: true, id: id, receivedAt: record.receivedAt });
}

async function handlePull(request, env) {
  if (!env.ADMIN_TOKEN || bearer(request, 'X-Dex-Admin') !== env.ADMIN_TOKEN) {
    return json({ ok: false, error: '凭证不对' }, 401);
  }
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
  const items = [];
  for (const key of listed.keys) {
    const raw = await env.INGEST_KV.get(key.name, 'text');
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
  let body = null;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'body 不是合法 JSON' }, 400);
  }
  const ids = Array.isArray(body.ids) ? body.ids : [];
  let removed = 0;
  for (const id of ids) {
    if (typeof id !== 'string' || !id.startsWith('q:')) continue;
    await env.INGEST_KV.delete(id);
    removed++;
  }
  return json({ ok: true, removed: removed });
}

async function handleHealth(env) {
  const listed = await env.INGEST_KV.list({ prefix: 'q:', limit: MAX_QUEUE });
  return json({ ok: true, pending: listed.keys.length, source: 'pokemon-dex-ingest' });
}

/** 路由。★ `OPTIONS` 要在所有路径之前处理（预检不带路径语义）。 */
export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (path === '/ingest' && request.method === 'POST') return handleIngest(request, env);
  if (path === '/pull' && request.method === 'GET') return handlePull(request, env);
  if (path === '/ack' && request.method === 'POST') return handleAck(request, env);
  if (path === '/health' && request.method === 'GET') return handleHealth(env);
  return json({ ok: false, error: '没有这个接口' }, 404);
}

export default { fetch: handleRequest };
