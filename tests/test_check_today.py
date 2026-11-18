#!/usr/bin/env python3
"""自检脚本的回归测试。

`scripts/check_today.py` 之前**零测试覆盖**，而它是「今天到底掉不掉宝可梦」的第一手证据。
这里守住三件事：

1. **坏配置不该甩 traceback 给读者** —— 注册表读不出来时，脚本要自己说
   「[失败] 源注册表有问题：…」并 exit 1。
2. **注册表里没有微信读书源时也一样** —— 旧代码直接 `cfg["sources"]["weread"]["per"]`，
   抛的是裸 `KeyError`，读者看到的是一坨 traceback，而不是「这个注册表缺微信读书源」。
3. **JSON 里的 `thresholdSeconds` 与 `reached` 必须自洽** ——
   `thresholdSeconds` 是「掉 1 只实际需要的秒数」（`perAward × per`），
   所以 `todaySeconds >= thresholdSeconds` 必须和 `reached` 同真同假。
   （v2 之前这个字段装的是「1 个额度」的秒数，和 `reached` 的判据打架。）
"""

import contextlib
import datetime
import io
import json
import os
import pathlib
import tempfile
import unittest
import zoneinfo
from unittest import mock

from scripts import check_today

SH = zoneinfo.ZoneInfo("Asia/Shanghai")

# 一份合法的注册表（微信读书 900 秒 = 1 额度，perAward = 2 → 掉 1 只要 1800 秒）
GOOD_CONFIG = {
    "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
    "ledgerDays": 30,
    "sources": {
        "weread": {"name": "微信读书", "icon": "📖", "unit": "秒", "per": 900,
                   "mode": "pull", "enabled": True, "maxPerDay": 86400},
        "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                "mode": "push", "enabled": True, "maxPerDay": 1440},
    },
}

# 合法结构，但没有微信读书源
NO_WEREAD_CONFIG = {
    "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
    "ledgerDays": 30,
    "sources": {
        "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                "mode": "push", "enabled": True, "maxPerDay": 1440},
    },
}


def today_bucket_key() -> int:
    """今天（北京时间）的桶 key，和微信读书的月度分桶口径一致。"""
    now = datetime.datetime.now(SH)
    midnight = datetime.datetime.combine(now.date(), datetime.time.min, tzinfo=SH)
    return int(midnight.timestamp())


class CheckTodayConfigErrorTest(unittest.TestCase):
    """配置坏了的时候，脚本要好好说话。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "sources.json"
        # check_today.SOURCES_PATH 是模块全局量 —— 用完必须还原
        orig = check_today.SOURCES_PATH
        self.addCleanup(setattr, check_today, "SOURCES_PATH", orig)
        check_today.SOURCES_PATH = self.path
        # DEX_THRESHOLD 会覆盖注册表里的 per，测试里要排除掉这个变量
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DEX_THRESHOLD", None)

    def _run(self, argv=("--json",)):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", ["check_today.py", *argv]), \
             mock.patch("scripts.weread_client.read_times") as api:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = check_today.main()
        return rc, out.getvalue(), err.getvalue(), api

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_broken_config_exits_1_with_friendly_message(self):
        """坏配置 → exit 1 + stderr 含「源注册表」，而不是 traceback。"""
        self._write("{ 这不是合法 JSON")
        rc, out, err, api = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("源注册表", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        # 配置没过就不该白打一次 API
        api.assert_not_called()

    def test_missing_weread_source_exits_1_with_friendly_message(self):
        """注册表里没有 weread 源 → exit 1 + 友好中文提示（不是 KeyError traceback）。"""
        self._write(json.dumps(NO_WEREAD_CONFIG, ensure_ascii=False))
        rc, out, err, api = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("源注册表", err)
        self.assertIn("微信读书", err)
        self.assertNotIn("KeyError", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        api.assert_not_called()


class CheckTodayJsonSemanticsTest(unittest.TestCase):
    """JSON 里的阈值字段必须和 `reached` 自洽（I-1）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "sources.json"
        self.path.write_text(json.dumps(GOOD_CONFIG, ensure_ascii=False),
                             encoding="utf-8")
        orig = check_today.SOURCES_PATH
        self.addCleanup(setattr, check_today, "SOURCES_PATH", orig)
        check_today.SOURCES_PATH = self.path
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DEX_THRESHOLD", None)

    def _json(self, seconds):
        """跑一次自检（把 API 返回量打桩成 seconds 秒），返回解析后的 JSON。"""
        buckets = {today_bucket_key(): seconds}
        out = io.StringIO()
        with mock.patch("sys.argv", ["check_today.py", "--json"]), \
             mock.patch("scripts.weread_client.read_times",
                        return_value=buckets):
            with contextlib.redirect_stdout(out):
                rc = check_today.main()
        self.assertEqual(rc, 0)
        return json.loads(out.getvalue())

    def test_threshold_is_award_cost_not_one_credit(self):
        """thresholdSeconds = 掉 1 只实际需要的秒数（1800），creditSeconds = 1 个额度（900）。"""
        d = self._json(1776)
        # 与 data/dex.json 的 today.threshold 同名同义
        self.assertEqual(d["thresholdSeconds"], 1800)
        self.assertEqual(d["creditSeconds"], 900)
        self.assertEqual(d["perAward"], 2)

    def test_reached_agrees_with_threshold_comparison(self):
        """todaySeconds >= thresholdSeconds 必须和 reached 同真同假。"""
        for seconds, want_reached in ((1776, False), (1800, True), (2400, True)):
            with self.subTest(seconds=seconds):
                d = self._json(seconds)
                self.assertEqual(d["reached"], want_reached)
                self.assertEqual(
                    d["todaySeconds"] >= d["thresholdSeconds"], d["reached"])
                self.assertEqual(d["credit"] >= d["perAward"], d["reached"])


if __name__ == "__main__":
    unittest.main()
