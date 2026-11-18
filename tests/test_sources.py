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


class LoadConfigTest(unittest.TestCase):
    """load_config 是本模块唯一的 IO 入口，顺带盯住 DEX_THRESHOLD 这个旧旋钮。"""

    def test_loads_shipped_registry(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        self.assertEqual(got["perAward"], 2)
        self.assertEqual(got["sources"]["weread"]["per"], 900)
        self.assertEqual(got["sources"]["words"]["enabled"], False)

    def test_dex_threshold_overrides_weread_per(self):
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "600"}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        self.assertEqual(got["sources"]["weread"]["per"], 600)
        # 别的源不受影响
        self.assertEqual(got["sources"]["pom"]["per"], 25)

    def test_dex_threshold_absent_keeps_file_value(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            got = src.load_config(REGISTRY_PATH)
        self.assertEqual(got["sources"]["weread"]["per"], 900)

    def test_dex_threshold_not_integer_raises(self):
        with mock.patch.dict(os.environ, {"DEX_THRESHOLD": "abc"}, clear=True):
            with self.assertRaises(src.SourceConfigError):
                src.load_config(REGISTRY_PATH)

    def test_missing_file_raises(self):
        with self.assertRaises(src.SourceConfigError):
            src.load_config(REGISTRY_PATH.parent / "no-such-registry.json")

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


if __name__ == "__main__":
    unittest.main()
