import json
import pathlib
import unittest

from scripts import dex_core as dc

BULBASAUR = {"id": "0001", "nameZh": "妙蛙种子",
             "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
IVYSAUR = {"id": "0002", "nameZh": "妙蛙草",
           "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
VENUSAUR = {"id": "0003", "nameZh": "妙蛙花",
            "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"]}
PIKACHU = {"id": "0025", "nameZh": "皮卡丘",
           "evolution": ["皮丘", "皮卡丘", "雷丘", "皮丘", "皮卡丘", "雷丘"]}
PIKACHU_DUPE = {"id": "0025b", "nameZh": "皮卡丘",
                "evolution": ["皮丘", "皮卡丘", "雷丘"]}
# 真实数据里同链成员的 evolution 数组并不一致 —— 这正是旧 key 算法的坑
WALKING_GRASS = {"id": "0004", "nameZh": "走路草",
                 "evolution": ["走路草", "臭臭花", "霸王花", "走路草", "臭臭花", "美丽花"]}
BEAUTIFUL_FLOWER = {"id": "0005", "nameZh": "霸王花",
                     "evolution": ["走路草", "臭臭花", "霸王花"]}


class ChainKeyTest(unittest.TestCase):
    def test_uses_base_form_name(self):
        self.assertEqual(dc.chain_key(BULBASAUR), "妙蛙种子")

    def test_dedups_repeated_names(self):
        self.assertEqual(dc.chain_key(PIKACHU), "皮丘")

    def test_missing_evolution_is_empty(self):
        self.assertEqual(dc.chain_key({"id": "x", "nameZh": "独角兽"}), "")

    def test_members_with_different_arrays_share_one_key(self):
        """真实数据的坑：同链成员的 evolution 数组不一致，key 必须仍然一致。"""
        self.assertEqual(dc.chain_key(WALKING_GRASS), dc.chain_key(BEAUTIFUL_FLOWER))

    def test_different_chains_differ(self):
        self.assertNotEqual(dc.chain_key(BULBASAUR), dc.chain_key(PIKACHU))


class AdvanceChainTest(unittest.TestCase):
    def test_first_care_creates_entry(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["care"], 1)
        self.assertEqual(st["pendingShiny"], 0)
        self.assertEqual(st["nextShinyAt"], 30)

    def test_mutates_in_place_and_returns_same_object(self):
        chains = {}
        got = dc.advance_chain(chains, BULBASAUR)
        self.assertIs(got, chains)

    def test_care_accumulates(self):
        chains = {}
        for _ in range(5):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子"]["care"], 5)

    def test_thirty_care_grants_one_guarantee(self):
        chains = {}
        for _ in range(30):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 1)
        self.assertEqual(st["nextShinyAt"], 60)

    def test_sixty_care_grants_second_guarantee(self):
        chains = {}
        for _ in range(60):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 2)
        self.assertEqual(st["nextShinyAt"], 90)

    def test_guarantee_rhythm_is_fixed_not_sliding(self):
        """care 跳变时保底节奏不该整体后移：nextShinyAt 每次 +30，不是 care+30。"""
        chains = {"妙蛙种子": {"care": 35, "pendingShiny": 0, "nextShinyAt": 30}}
        dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子"]
        self.assertEqual(st["pendingShiny"], 1)
        self.assertEqual(st["nextShinyAt"], 60)

    def test_other_chain_is_independent(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        dc.advance_chain(chains, PIKACHU)
        self.assertEqual(chains["妙蛙种子"]["care"], 1)
        self.assertEqual(chains["皮丘"]["care"], 1)

    def test_no_evolution_creates_no_entry(self):
        chains = {}
        dc.advance_chain(chains, {"id": "x", "nameZh": "独角兽"})
        self.assertEqual(chains, {})


class RealPokedexTest(unittest.TestCase):
    """真实数据护栏 —— 合成数据会掩盖「同链成员 evolution 数组不一致」这个坑。"""

    @classmethod
    def setUpClass(cls):
        p = pathlib.Path(__file__).resolve().parent.parent / "data" / "pokedex.json"
        cls.pokedex = json.loads(p.read_text(encoding="utf-8"))["pokemon"]
        cls.by_name = {x["nameZh"]: x for x in cls.pokedex}

    def test_walking_grass_line_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n])
                for n in ("走路草", "臭臭花", "霸王花", "美丽花")}
        self.assertEqual(len(keys), 1)

    def test_eevee_family_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n])
                for n in ("伊布", "水伊布", "雷伊布", "火伊布", "太阳伊布",
                          "月亮伊布", "叶伊布", "冰伊布", "仙子伊布")}
        self.assertEqual(len(keys), 1)

    def test_pikachu_line_shares_one_key(self):
        keys = {dc.chain_key(self.by_name[n]) for n in ("皮丘", "皮卡丘", "雷丘")}
        self.assertEqual(len(keys), 1)

    def test_chain_key_is_base_form_name(self):
        self.assertEqual(dc.chain_key(self.by_name["皮卡丘"]), "皮丘")

    def test_grouping_matches_biological_chain_count(self):
        """用基础形态名分组 → 543 组，正好等于生物链数。"""
        groups = {}
        for x in self.pokedex:
            groups.setdefault(dc.chain_key(x), []).append(x["nameZh"])
        self.assertEqual(len(groups), 543)
