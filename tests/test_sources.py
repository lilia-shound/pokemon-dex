import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from scripts import sources as src


def cfg(**over):
    base = {
        "version": 1,
        "perAward": 2,
        "maxPerDay": 1,
        "maxBackfill": 3,
        "ledgerDays": 30,
        "sources": {
            "weread": {"name": "微信读书", "unit": "秒", "per": 900,
                       "mode": "pull", "enabled": True, "maxPerDay": 86400},
            "pom": {"name": "番茄钟", "unit": "分钟", "per": 25,
                    "mode": "push", "enabled": True, "maxPerDay": 1440},
            "words": {"name": "背单词", "unit": "个", "per": 20,
                      "mode": "push", "enabled": False, "maxPerDay": 500},
        },
    }
    base.update(over)
    return base


def is_positive_int(v) -> bool:
    """>=1 的整数（bool 不算，尽管 True == 1）。"""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


class ValidateConfigTest(unittest.TestCase):
    def test_accepts_good_config(self):
        src.validate_config(cfg())  # 不抛异常即通过

    def test_rejects_missing_per_award(self):
        c = cfg()
        del c["perAward"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_zero_per_award(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(perAward=0))

    def test_rejects_non_int_per_award(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(perAward="2"))

    def test_rejects_source_without_per(self):
        c = cfg()
        del c["sources"]["pom"]["per"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_source_with_bad_mode(self):
        c = cfg()
        c["sources"]["pom"]["mode"] = "pushs"
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_empty_sources(self):
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(cfg(sources={}))

    def test_rejects_source_per_below_one(self):
        c = cfg()
        c["sources"]["weread"]["per"] = 0
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_source_without_enabled(self):
        # enabled 是必填：拼错键名 / 漏写不能让源静默退出结算
        c = cfg()
        del c["sources"]["pom"]["enabled"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_non_bool_enabled(self):
        # "false" 字符串是真值，放过去会让源照常参与结算
        c = cfg()
        c["sources"]["pom"]["enabled"] = "false"
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_int_enabled(self):
        # 1 不是 True：类型错了就该报错
        c = cfg()
        c["sources"]["weread"]["enabled"] = 1
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)

    def test_rejects_config_without_weread_source(self):
        """缺微信读书源 → 注册表非法。

        少了它，发放器会算出 `threshold = 0` 并写进对外的 dex.json
        （图鉴页渲染成「30/0 分」、进度条满格），而且没有任何报错。
        这条要求放在 validate_config 里，award.py 与 check_today.py 两个入口自动一致。
        """
        c = cfg()
        del c["sources"]["weread"]
        with self.assertRaises(src.SourceConfigError):
            src.validate_config(c)


class EnabledSourcesTest(unittest.TestCase):
    def test_only_enabled_returned(self):
        got = src.enabled_sources(cfg())
        self.assertIn("weread", got)
        self.assertIn("pom", got)
        self.assertNotIn("words", got)


class CreditForDayTest(unittest.TestCase):
    def test_floors_amount(self):
        got = src.credit_for_day(cfg(), {"weread": 1799})
        self.assertEqual(got["weread"], 1)

    def test_exact_multiple(self):
        got = src.credit_for_day(cfg(), {"weread": 1800})
        self.assertEqual(got["weread"], 2)

    def test_below_threshold_is_zero(self):
        got = src.credit_for_day(cfg(), {"weread": 899})
        self.assertEqual(got["weread"], 0)

    def test_disabled_source_never_credits(self):
        got = src.credit_for_day(cfg(), {"words": 1000})
        self.assertNotIn("words", got)
        self.assertEqual(got.get("words", 0), 0)

    def test_missing_source_counts_zero(self):
        got = src.credit_for_day(cfg(), {"weread": 900})
        self.assertEqual(got["pom"], 0)

    def test_day_credit_sums_sources(self):
        got = src.day_credit(cfg(), {"weread": 900, "pom": 50})
        self.assertEqual(got, 3)

    def test_day_credit_empty(self):
        self.assertEqual(src.day_credit(cfg(), {}), 0)

    def test_credits_for_days_maps_each_day(self):
        got = src.credits_for_days(cfg(), {"2026-11-16": {"weread": 900},
                                           "2026-11-17": {"weread": 1800}})
        self.assertEqual(got, {"2026-11-16": 1, "2026-11-17": 2})


REGISTRY_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "sources.json"


def registry_raw() -> dict:
    """注册表原始 JSON（用来和 load_config 的结果对比，避免把可调数值钉死在测试里）。"""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


class LoadConfigTest(unittest.TestCase):
    """load_config 是本模块唯一的 IO 入口，顺带盯住 DEX_THRESHOLD 这个旧旋钮。"""

    def test_loads_shipped_registry(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        # 只断言不变量：注册表里的数值是手调的旋钮（perAward 想调回 1 也不该让测试变红），
        # 所以这里不能钉死 perAward == 2 / weread.per == 900 这种具体值。
        self.assertTrue(is_positive_int(got["perAward"]), f"perAward 必须是 >=1 的整数，实际 {got['perAward']!r}")
        self.assertIn("weread", got["sources"])
        weread = got["sources"]["weread"]
        self.assertTrue(is_positive_int(weread["per"]), f"weread.per 必须是 >=1 的整数，实际 {weread['per']!r}")
        self.assertIn(weread["mode"], ("pull", "push"))
        # 每个源的 enabled 都得是布尔值（validate_config 已经把关，这里再钉一道）
        for sid, s in got["sources"].items():
            self.assertIsInstance(s["enabled"], bool, f"源 {sid} 的 enabled 必须是布尔值")
        self.assertTrue(any(s["enabled"] for s in got["sources"].values()), "至少要有一个启用的源")

    def test_dex_threshold_overrides_weread_per(self):
        raw = registry_raw()
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "600"}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        self.assertEqual(got["sources"]["weread"]["per"], 600)
        # 别的源不受影响（跟文件里的值比，不钉死具体数字）
        self.assertEqual(got["sources"]["pom"]["per"], raw["sources"]["pom"]["per"])

    def test_dex_threshold_absent_keeps_file_value(self):
        raw = registry_raw()
        with mock.patch.dict(os.environ, {}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        self.assertEqual(got["sources"]["weread"]["per"], raw["sources"]["weread"]["per"])

    def test_dex_threshold_not_integer_raises(self):
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "abc"}, clear=True):
            with self.assertRaises(src.SourceConfigError):
                src.load_config(REGISTRY_PATH)

    def test_dex_threshold_zero_raises(self):
        # 覆盖值也是配置：per=0 会在折算时 ZeroDivisionError，必须在读配置时就拒掉
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "0"}, clear=True):
            with self.assertRaises(src.SourceConfigError):
                src.load_config(REGISTRY_PATH)

    def test_dex_threshold_negative_raises(self):
        # per=-5 会把 900 秒记成 -180 额度，同样要在读配置时就拒掉
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "-5"}, clear=True):
            with self.assertRaises(src.SourceConfigError):
                src.load_config(REGISTRY_PATH)

    def test_missing_file_raises(self):
        with self.assertRaises(src.SourceConfigError):
            src.load_config(REGISTRY_PATH.parent / "no-such-registry.json")

    def test_directory_path_raises(self):
        # 传目录路径：不能漏出裸的 IsADirectoryError
        with self.assertRaises(src.SourceConfigError):
            src.load_config(REGISTRY_PATH.parent)

    def test_invalid_utf8_raises(self):
        # 非法 UTF-8 字节：不能漏出裸的 UnicodeDecodeError
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "sources.json"
            p.write_bytes(b"\xff\xfe\x00bad")
            with self.assertRaises(src.SourceConfigError):
                src.load_config(p)

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "sources.json"
            p.write_text("{不是 JSON", encoding="utf-8")
            with self.assertRaises(src.SourceConfigError):
                src.load_config(p)

    def test_invalid_shape_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "sources.json"
            p.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(src.SourceConfigError):
                src.load_config(p)


class RegistryDexConsistencyTest(unittest.TestCase):
    """对外的 dex.json 的 today.threshold 必须等于注册表算出来的值。

    两份文件是各自写各自更新的：改了注册表**不会**自动改已发布的 dex.json
    （dex.json 是发放器跑出来的，注册表是人手改的）—— 于是图鉴页会显示一个
    和实际口径不一致的进度条（甚至「30/0 分」）。这条测试把这个缝钉住：
    改了注册表就要重跑发放器并把新的 dex.json 一起提交。
    """

    @staticmethod
    def _expected_threshold(registry: dict) -> int:
        """掉 1 只实际需要的微信读书秒数 = perAward × weread.per。"""
        return int(registry["perAward"]) * int(registry["sources"]["weread"]["per"])

    def _dex_threshold(self, path: pathlib.Path) -> int:
        dex = json.loads(path.read_text(encoding="utf-8"))
        return int(dex["today"]["threshold"])

    def test_state_dex_threshold_matches_registry(self):
        registry = registry_raw()
        dex_path = REGISTRY_PATH.parent / "dex.json"
        self.assertEqual(self._dex_threshold(dex_path),
                         self._expected_threshold(registry))

    def test_published_site_dex_threshold_matches_registry(self):
        """发布到 gh-pages 的那份也要一致（deploy.sh 是 cp 出来的，但两边各自提交）。"""
        registry = registry_raw()
        site_dex = REGISTRY_PATH.parent.parent / "site" / "dex.json"
        self.assertEqual(self._dex_threshold(site_dex),
                         self._expected_threshold(registry))


if __name__ == "__main__":
    unittest.main()
