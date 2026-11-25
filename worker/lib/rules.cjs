'use strict';
/**
 * **单日上限表** —— 源 id → 该源一天最多能报多少原始单位。
 *
 * ★ 这张表**同时就是源白名单**：表里没有的源，报告一律拒收。
 * ★ 它必须与 `data/sources.json` 的 `sources.<id>.maxPerDay` **逐字一致**
 *   —— `tests/test_worker_rules.py` 盯着这件事，漂了就红。
 *   （跟 KOReader 插件里 `Report.MAX_MINUTES_PER_DAY` 一个套路：
 *   常量抄一份，但用测试守住它不漂。）
 *
 * 为什么不用 CommonJS 的 .json：Worker 入口是 ESM，node 里 import JSON
 * 要 import attributes，各家 bundler 支持得不齐 —— 一律用 .cjs 最省事。
 */

const RULES = {
  pom: 1440,       // 分钟（一天 24 小时）
  koreader: 1440,  // 分钟
  words: 500,      // 个
};

module.exports = { RULES };
