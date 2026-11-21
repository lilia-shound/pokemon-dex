#!/usr/bin/env node
/*
 * 用 fengari（Lua 5.3 的 JS 实现）在沙箱里跑 KOReader 插件的纯逻辑断言。
 *
 *   node tests/koreader_lua_runner.js          跑 tests/koreader_lua_spec.lua
 *   node tests/koreader_lua_runner.js --probe  跑完再输出一行探针结果（JSON）
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
const SOURCES_JSON = path.join(ROOT, 'data', 'sources.json');

function die(msg) {
  console.error('runner 出错：' + msg);
  process.exit(2);
}

let fixture = '';
try {
  fixture = fs.readFileSync(FIXTURE, 'utf8').trim();
} catch (err) {
  die('读不到 fixture：' + FIXTURE + '（' + err.message + '）');
}

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

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

/** 把一个字符串塞进全局变量（spec 里直接读，不用 io —— fengari 的 io 是残的） */
function setGlobal(name, value) {
  lua.lua_pushstring(L, to_luastring(value));
  lua.lua_setglobal(L, to_luastring(name));
}

setGlobal('REPORT_PATH', REPORT);
setGlobal('DAYS_FIXTURE', fixture);
setGlobal('KOREADER_PER', String(per));

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

const passed = readIntField('passed');
const failed = readIntField('failed');

let probeJson = '';
if (process.argv.includes('--probe')) {
  lua.lua_getfield(L, 1, to_luastring('probe'));
  lua.lua_pushstring(L, to_luastring(fixture));
  if (lua.lua_pcall(L, 1, 1, 0) !== lua.LUA_OK) {
    console.error('probe 执行失败：' + to_jsstring(lua.lua_tostring(L, -1)));
    process.exit(1);
  }
  probeJson = to_jsstring(lua.lua_tostring(L, -1));
}

console.log('合计：' + passed + ' 通过 / ' + failed + ' 失败');
if (probeJson) {
  console.log(probeJson);
}
// 一条都没跑也算失败（spec 被清空了 ⇒ 假绿）
process.exit((failed > 0 || passed === 0) ? 1 : 0);
