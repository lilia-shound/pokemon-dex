#!/usr/bin/env node
/*
 * 用 fengari（Lua 5.3 的 JS 实现）在沙箱里跑 KOReader 插件的纯逻辑断言。
 *
 *   node tests/koreader_lua_runner.js                     跑 tests/koreader_lua_spec.lua
 *   node tests/koreader_lua_runner.js --probe             跑完再输出一行探针结果（JSON）
 *   node tests/koreader_lua_runner.js --daily-sql 123     输出设备上真正执行的那条 SQL（原始字节）
 *
 * 沙箱里没有真的 Lua 解释器（Kindle 上才有），fengari 就是替身。
 * 退出码：0 = 全部通过；1 = 有用例失败；2 = runner 自己没跑起来。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require('fengari');

const ROOT = path.resolve(__dirname, '..');
const SPEC = path.join(__dirname, 'koreader_lua_spec.lua');
const REPORT = path.join(ROOT, 'plugin', 'koreader-dex.koplugin', 'report.lua');
const FIXTURE = path.join(__dirname, 'fixtures', 'koreader_days.txt');
const WIRE_CASES = path.join(__dirname, 'fixtures', 'koreader_wire_cases.txt');
const SOURCES_JSON = path.join(ROOT, 'data', 'sources.json');

function die(msg) {
  console.error('runner 出错：' + msg);
  process.exit(2);
}

function readText(path, what) {
  try {
    return fs.readFileSync(path, 'utf8');
  } catch (err) {
    die('读不到' + what + '：' + path + '（' + err.message + '）');
  }
}

/* 跨语言共用的 fixture：天数串 */
const fixture = readText(FIXTURE, 'fixture').trim();

/* 线格式的用例：每行一条（**不 trim 每一行** —— 带空白的行本身就是用例） */
const wireCases = readText(WIRE_CASES, '线格式用例文件');
const wireCasesBody = wireCases.endsWith('\n') ? wireCases.slice(0, -1) : wireCases;

/* 额度折算口径（几分钟 1 个额度）从服务端注册表里读，别在测试里另抄一份 */
let per = 15;
try {
  const registry = JSON.parse(fs.readFileSync(SOURCES_JSON, 'utf8'));
  if (registry && registry.sources && registry.sources.koreader) {
    per = registry.sources.koreader.per;
  }
} catch (err) {
  die('读不到源注册表：' + SOURCES_JSON + '（' + err.message + '）');
}

/*
 * --daily-sql：只输出设备上真正执行的那条 SQL（**原始字节**，不过 JSON）。
 * JSON 转义一旦有 bug，SQL 就会被悄悄改掉，而 Python 侧要的是字节级一致。
 */
const dailySqlIdx = process.argv.indexOf('--daily-sql');
const dailySqlMode = dailySqlIdx !== -1;

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

/** 把一个字符串塞进全局变量（spec 里直接读，不用 io —— fengari 的 io 是残的） */
function setGlobal(name, value) {
  lua.lua_pushstring(L, to_luastring(value));
  lua.lua_setglobal(L, to_luastring(name));
}

setGlobal('REPORT_PATH', REPORT);
setGlobal('DAYS_FIXTURE', fixture);
setGlobal('WIRE_CASES', wireCasesBody);
setGlobal('KOREADER_PER', String(per));
/* --daily-sql 模式下 stdout 只放 SQL ⇒ 断言集别往 stdout 打印明细 */
setGlobal('SILENT', dailySqlMode ? '1' : '0');

const status = lauxlib.luaL_dofile(L, to_luastring(SPEC));
if (status !== lua.LUA_OK) {
  console.error('spec 执行失败：' + to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(1);
}

/* spec 的返回值在栈上（索引 1） */
function readIntField(key) {
  lua.lua_getfield(L, 1, to_luastring(key));
  const value = lua.lua_tointeger(L, -1);
  lua.lua_pop(L, 1);
  return value;
}

/**
 * 调用 spec 导出的一个函数（栈上的返回值）
 * @param {string} name spec 返回表里的字段名
 * @param {string|number} [arg] 一个参数（字符串或数字）
 * @returns {string} 返回的字符串
 */
function callStringFunction(name, arg) {
  lua.lua_getfield(L, 1, to_luastring(name));
  if (arg !== undefined) {
    if (typeof arg === 'string') {
      lua.lua_pushstring(L, to_luastring(arg));
    } else {
      lua.lua_pushnumber(L, arg);
    }
  }
  if (lua.lua_pcall(L, arg === undefined ? 0 : 1, 1, 0) !== lua.LUA_OK) {
    die(name + ' 调用失败：' + to_jsstring(lua.lua_tostring(L, -1)));
  }
  return to_jsstring(lua.lua_tostring(L, -1));
}

const passed = readIntField('passed');
const failed = readIntField('failed');

if (dailySqlMode) {
  if (failed > 0) {
    die('断言集有失败用例，daily_sql 不可信');
  }
  const raw = process.argv[dailySqlIdx + 1];
  const since = Number(raw === undefined ? 0 : raw);
  if (!Number.isFinite(since)) {
    die('--daily-sql 后面要跟一个数字');
  }
  const sql = callStringFunction('daily_sql', Math.trunc(since));
  try {
    /* 直写 fd 1：process.stdout.write 在管道上是异步的，
       紧跟 process.exit() 有截断的风险 */
    fs.writeSync(1, Buffer.from(sql, 'utf8'));
  } catch (err) {
    /* 读的人先关了管道（head 之类）⇒ 不用报错 */
    if (err && err.code !== 'EPIPE') throw err;
  }
  process.exit(0);
}

let probeJson = '';
if (process.argv.includes('--probe')) {
  const fixtureProbe = JSON.parse(callStringFunction('probe', fixture));
  // 每一条线（好坏都有）都过一遍 ⇒ Python 侧能逐例对拍
  const cases = JSON.parse(callStringFunction('probe_cases', undefined));
  probeJson = JSON.stringify(Object.assign({}, fixtureProbe, { cases: cases }));
}

console.log('合计：' + passed + ' 通过 / ' + failed + ' 失败');
if (probeJson) {
  console.log(probeJson);
}
// 一条都没跑也算失败（spec 被清空了 ⇒ 假绿）
process.exit((failed > 0 || passed === 0) ? 1 : 0);
