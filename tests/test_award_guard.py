#!/usr/bin/env python3
"""发放器的护栏路径：取数失败、配置坏、推送。

这几条路径之前全都没有测试覆盖，而它们恰好是「静默出错」的重灾区：

1. **取数失败 ≠ 今天没读够** —— monthly 与 weekly 都取数失败时，
   `fetch_buckets` 必须抛错，`main()` 必须 exit 1 + 友好中文。
   退化成「未达标，今天没有宝可梦。」+ rc=0 的话，两者在 cron 里完全同形：
   Key 失效 / skill 版本过期 / 网关抖动都会被当成「用户今天没读书」，
   全站安静地停在「不更新」状态而没人知道。
2. **单模式失败仍可降级** —— 一个周期挂掉时，另一个周期的数据照样用。
3. **两个模式都成功但没数据 ≠ 取数失败** —— 那就是「今天确实没读」。
4. **配置坏了** → exit 1 + 友好中文，不甩裸 traceback，也不写坏状态。
5. **推送路径** —— 旧测试全都带 `--no-push`，
   「今日额度 x/2」这行文案一直没人验证过。
"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import award
from scripts import weread_client as wc

KEY_20261114 = 1794585600
DAY = "2026-11-14"

CONFIG = {
    "version": 1, "perAward": 2, "maxPerDay": 1, "maxBackfill": 3,
    "ledgerDays": 30,
    "sources": {
        "weread": {"name": "微信读书", "icon": "📖", "unit": "秒", "per": 900,
                   "mode": "pull", "enabled": True, "maxPerDay": 86400},
        "pom": {"name": "番茄钟", "icon": "🍅", "unit": "分钟", "per": 25,
                "mode": "push", "enabled": True, "maxPerDay": 1440},
    },
}


class FetchBucketsTest(unittest.TestCase):
    """取数这一层的失败语义。"""

    def test_both_modes_fail_raises(self):
        """两个周期都失败 → 抛错（不能安静地返回空桶）。"""
        with mock.patch("scripts.weread_client.read_times",
                        side_effect=wc.WeReadError("网关 HTTP 401")):
            with self.assertRaises(RuntimeError) as ctx:
                award.fetch_buckets()
        msg = str(ctx.exception)
        self.assertIn("取数全部失败", msg)
        # 两个周期的失败原因都要在报错里 —— 只报一个会让人以为是偶发
        self.assertIn("monthly", msg)
        self.assertIn("weekly", msg)

    def test_one_mode_failure_keeps_other_mode_data(self):
        """单模式失败仍可降级：另一个周期的数据照样用。"""

        def fake(mode):
            if mode == "monthly":
                raise wc.WeReadError("网关 HTTP 500")
            return {KEY_20261114: 1800}

        with mock.patch("scripts.weread_client.read_times", side_effect=fake):
            got = award.fetch_buckets()
        self.assertEqual(got, {KEY_20261114: 1800})

    def test_empty_result_is_not_a_failure(self):
        """两个模式都成功但没有数据 —— 那是「今天确实没读」，不是取数失败。"""
        with mock.patch("scripts.weread_client.read_times", return_value={}):
            self.assertEqual(award.fetch_buckets(), {})


class AwardFetchFailureTest(unittest.TestCase):
    """取数失败时：exit 1 + 友好中文 + 不写状态。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        # award.DEX_PATH 是模块全局量 —— 用完必须还原
        orig = award.DEX_PATH
        self.addCleanup(setattr, award, "DEX_PATH", orig)
        award.DEX_PATH = self.dex_path

    def _run(self, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("scripts.weread_client.read_times",
                        side_effect=wc.WeReadError("环境变量 WEREAD_API_KEY 未设置")), \
             mock.patch.object(award, "load_config", return_value=CONFIG):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = award.main(["--date", DAY, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_exits_1_with_friendly_chinese(self):
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("[失败]", err)
        self.assertIn("取数失败", err)
        self.assertNotIn("Traceback", err)

    def test_not_reported_as_below_threshold(self):
        """不能退化成「未达标，今天没有宝可梦。」—— 那是取数失败被当成没读书。"""
        rc, out, err = self._run()
        self.assertNotIn("未达标", out)
        self.assertNotIn("未达标", err)

    def test_does_not_write_state(self):
        """失败就不该写状态：旧状态留着，别把「没读到」写成「今天读了多少」。"""
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertFalse(self.dex_path.exists())


class AwardConfigFailureTest(unittest.TestCase):
    """配置坏了：exit 1 + 友好中文，不甩裸 traceback，也不写坏状态。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.dex_path = self.dir / "dex.json"
        self.sources_path = self.dir / "sources.json"
        # award.DEX_PATH / award.SOURCES_PATH 都是模块全局量 —— 用完必须还原
        orig_dex = award.DEX_PATH
        orig_src = award.SOURCES_PATH
        self.addCleanup(setattr, award, "DEX_PATH", orig_dex)
        self.addCleanup(setattr, award, "SOURCES_PATH", orig_src)
        award.DEX_PATH = self.dex_path
        award.SOURCES_PATH = self.sources_path

    def _run(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = award.main(["--date", DAY])
        return rc, out.getvalue(), err.getvalue()

    def test_broken_registry_exits_1_with_friendly_message(self):
        self.sources_path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("源注册表", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.dex_path.exists())

    def test_registry_without_weread_source_exits_1(self):
        """缺微信读书源 → exit 1。

        以前 award.py 没有这道检查：注册表里去掉 weread 源就会算出
        `threshold = 0` 写进对外的 dex.json（图鉴页渲染成「30/0 分」、进度条满格），
        而且**没有任何报错**。现在这道要求上移到 sources.validate_config，
        两个入口自动一致。
        """
        cfg = dict(CONFIG)
        cfg["sources"] = {k: v for k, v in CONFIG["sources"].items() if k != "weread"}
        self.sources_path.write_text(json.dumps(cfg, ensure_ascii=False),
                                      encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("微信读书", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.dex_path.exists())


class AwardPushTest(unittest.TestCase):
    """推送路径：旧测试全都带 --no-push，「今日额度 x/2」这行文案没人验证过。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dex_path = pathlib.Path(tmp.name) / "dex.json"
        orig = award.DEX_PATH
        self.addCleanup(setattr, award, "DEX_PATH", orig)
        award.DEX_PATH = self.dex_path

    def _run(self, argv=()):
        out = io.StringIO()
        with mock.patch.object(award, "fetch_buckets",
                               return_value={KEY_20261114: 1800}), \
             mock.patch.object(award, "load_config", return_value=CONFIG), \
             mock.patch.object(award.push_notify, "send") as send:
            with contextlib.redirect_stdout(out):
                rc = award.main(["--date", DAY, *argv])
        return rc, out.getvalue(), send

    def test_push_body_shows_today_credit(self):
        rc, out, send = self._run()
        self.assertEqual(rc, 0)
        send.assert_called_once()
        title, body = send.call_args[0]
        self.assertIn("捕获", title)
        self.assertIn("今日额度 2/2", body)
        self.assertIn("连读 1 天", body)

    def test_no_push_flag_skips_push(self):
        rc, out, send = self._run(argv=["--no-push"])
        self.assertEqual(rc, 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
