// 百词斩油猴脚本的**纯逻辑**跑测器（node）。
//
// 用法：
//     echo '{"cases":[...]}' | node tests/bcz_userscript_runner.js
//
// 输入（stdin，JSON）：
//   {
//     "cases": [
//       {"op": "text",    "text": "今日学习 137 词"},
//       {"op": "payload", "payload": {...}, "today": "2026-11-22"},
//       {"op": "url",     "n": 137}
//     ]
//   }
// 输出（stdout，JSON）：
//   {"results": [ {...}, ... ]}
//
// 为什么要有这个跑测器：油猴脚本是**一个文件**（装起来方便），
// 所以纯逻辑不能拆成单独的文件 import ⇒ 这里直接 require 整个脚本，
// 让它在 node 里跑纯逻辑那一段（脚本里有 document 的守卫，node 下不会碰 DOM）。

'use strict';

const path = require('path');

const api = require(path.join(__dirname, '..', 'plugin', 'bcz-dex.user.js'));

const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', () => {
  let input = {};
  try {
    input = JSON.parse(chunks.join('') || '{}');
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: '输入不是合法 JSON：' + e.message }));
    return;
  }
  const cases = Array.isArray(input.cases) ? input.cases : [];
  const results = [];
  for (const c of cases) {
    if (c.op === 'text') {
      results.push(api.parseCountsFromText(c.text));
    } else if (c.op === 'payload') {
      results.push(api.parseCountsFromPayload(c.payload, c.today || '2026-11-22'));
    } else if (c.op === 'url') {
      results.push(api.landingUrl(c.n));
    } else {
      results.push(null);
    }
  }
  process.stdout.write(JSON.stringify({ results: results }));
});
