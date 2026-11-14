import json
import pathlib
import unittest

from scripts import fetch_pokemon as fp

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pokemon_0025.json"


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.src = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.out = fp.extract(self.src)

    def test_identity_fields(self):
        self.assertEqual(self.out["id"], "0025")
        self.assertEqual(self.out["nameZh"], "皮卡丘")
        self.assertEqual(self.out["nameJa"], "ピカチュウ")
        self.assertEqual(self.out["nameEn"], "Pikachu")

    def test_types_and_category_are_chinese(self):
        self.assertEqual(self.out["types"], ["电"])
        self.assertEqual(self.out["category"], "鼠宝可梦")

    def test_has_chinese_flavor_text(self):
        self.assertTrue(self.out["flavor"], "必须有中文图鉴描述")
        self.assertTrue(any("一" <= ch <= "鿿" for ch in self.out["flavor"]))

    def test_has_abilities(self):
        self.assertTrue(self.out["abilities"])
        self.assertIn("静电", self.out["abilities"])

    def test_has_evolution_chain(self):
        self.assertIn("皮卡丘", self.out["evolution"])
        self.assertIn("皮丘", self.out["evolution"])

    def test_art_and_shiny_are_picked(self):
        self.assertTrue(self.out["art"], "必须选到主立绘")
        self.assertTrue(self.out["artShiny"], "必须选到闪光立绘")
        self.assertTrue(self.out["art"].endswith(".png"))

    def test_art_is_absolute_cdn_url(self):
        """立绘存的是完整直链，浏览器直接拉，不落盘。"""
        self.assertTrue(self.out["art"].startswith("https://raw.githubusercontent.com/"))
        self.assertTrue(self.out["artShiny"].startswith("https://raw.githubusercontent.com/"))

    def test_art_prefers_standard_form_over_variants(self):
        """标准形态优先于极巨化/帽子等变体。"""
        self.assertNotIn("极巨化", self.out["art"])
        self.assertNotIn("帽子", self.out["art"])


class PickArtTest(unittest.TestCase):
    def test_prefers_exact_standard_form(self):
        imgs = [
            {"name": "皮卡丘-雄性", "image": "0025-皮卡丘-雄性.png", "shiny": "0025-皮卡丘-雄性-shiny.png"},
            {"name": "皮卡丘", "image": "0025-皮卡丘.png", "shiny": "0025-皮卡丘-shiny.png"},
        ]
        self.assertEqual(fp.pick_art(imgs, "皮卡丘", False), "0025-皮卡丘.png")
        self.assertEqual(fp.pick_art(imgs, "皮卡丘", True), "0025-皮卡丘-shiny.png")

    def test_falls_back_to_gender_variant(self):
        imgs = [{"name": "皮卡丘-雄性", "image": "0025-皮卡丘-雄性.png",
                 "shiny": "0025-皮卡丘-雄性-shiny.png"}]
        self.assertEqual(fp.pick_art(imgs, "皮卡丘", False), "0025-皮卡丘-雄性.png")

    def test_falls_back_to_any_variant_when_only_one(self):
        imgs = [{"name": "新叶喵-世界帽子", "image": "0906-新叶喵-世界帽子.png",
                 "shiny": "0906-新叶喵-世界帽子-shiny.png"}]
        self.assertEqual(fp.pick_art(imgs, "新叶喵", False), "0906-新叶喵-世界帽子.png")

    def test_returns_none_when_empty(self):
        self.assertIsNone(fp.pick_art([], "不存在", False))
        self.assertIsNone(fp.pick_art([], "不存在", True))

    def test_missing_shiny_falls_back_to_none_not_to_normal(self):
        imgs = [{"name": "某宝可梦", "image": "0900-某宝可梦.png"}]
        self.assertIsNone(fp.pick_art(imgs, "某宝可梦", True))


if __name__ == "__main__":
    unittest.main()
