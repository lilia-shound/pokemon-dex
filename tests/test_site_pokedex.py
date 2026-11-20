"""线上那份 pokedex.json 是瘦身版 —— 钉住「字段不能少」和「体积别涨回去」。"""

import json
import pathlib
import unittest

from scripts import build_site_pokedex as bsp

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pokedex.json"
OUT = ROOT / "site" / "pokedex.json"

# 读方真正会读的字段 —— 少一个就是线上少显示一样东西
# （artShiny 不是每只都有，由 test_art_shiny_kept_when_source_has_it 单独盯）
READ_BY_CLIENT = ("id", "nameZh", "types", "art", "flavor",
                  "abilities", "evolution", "category", "height", "weight")


class SitePokedexTest(unittest.TestCase):
    def setUp(self):
        self.src = json.loads(SRC.read_text(encoding="utf-8"))
        self.out = json.loads(OUT.read_text(encoding="utf-8"))

    def test_same_number_of_pokemon(self):
        self.assertEqual(self.out["count"], self.src["count"])
        self.assertEqual(len(self.out["pokemon"]), len(self.src["pokemon"]))

    def test_ids_match_in_order(self):
        self.assertEqual([p["id"] for p in self.out["pokemon"]],
                         [p["id"] for p in self.src["pokemon"]])

    def test_every_read_field_present(self):
        """读方要的字段一个都不能少（少一个线上就少显示一样东西）。"""
        for p in self.out["pokemon"]:
            for k in READ_BY_CLIENT:
                self.assertIn(k, p, f"{p.get('id')} 缺字段 {k}")
                self.assertTrue(p[k], f"{p.get('id')} 的 {k} 是空的")

    def test_art_shiny_kept_when_source_has_it(self):
        """artShiny 不是每只都有（实测 1028 只里 1012 只有），有就必须留着。"""
        src_shiny = {p["id"] for p in self.src["pokemon"] if p.get("artShiny")}
        out_shiny = {p["id"] for p in self.out["pokemon"] if p.get("artShiny")}
        self.assertEqual(out_shiny, src_shiny)
        self.assertGreater(len(src_shiny), 1000)

    def test_preview_entries_keep_their_marks(self):
        prev = [p for p in self.out["pokemon"] if p.get("preview")]
        self.assertEqual(len(prev), 3)
        for p in prev:
            self.assertTrue(p.get("provisionalNo"))

    def test_size_is_much_smaller_than_source(self):
        """瘦身是这刀的意义 —— 涨回去就等于白做。"""
        self.assertLess(OUT.stat().st_size, SRC.stat().st_size * 0.4)

    def test_build_keeps_only_known_fields(self):
        row = bsp.build({"pokemon": [{
            "id": "0001", "nameZh": "妙蛙种子", "profile": "很长的文本",
            "summary": "也是", "nameEn": "Bulbasaur", "artSource": "home",
            "flavorSource": "dex", "art": "0001.webp",
        }]})["pokemon"][0]
        self.assertNotIn("profile", row)
        self.assertNotIn("summary", row)
        self.assertNotIn("nameEn", row)
        self.assertNotIn("artSource", row)
        self.assertNotIn("flavorSource", row)
        self.assertEqual(row["id"], "0001")

    def test_build_drops_empty_values(self):
        row = bsp.build({"pokemon": [{
            "id": "0002", "nameZh": "妙蛙草", "flavor": "", "evolution": [],
            "art": "0002.webp",
        }]})["pokemon"][0]
        self.assertNotIn("flavor", row)
        self.assertNotIn("evolution", row)


if __name__ == "__main__":
    unittest.main()
