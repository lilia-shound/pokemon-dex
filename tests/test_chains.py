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


class ChainKeyTest(unittest.TestCase):
    def test_joins_names_with_arrow(self):
        self.assertEqual(dc.chain_key(BULBASAUR), "妙蛙种子→妙蛙草→妙蛙花")

    def test_dedups_repeated_names(self):
        self.assertEqual(dc.chain_key(PIKACHU), "皮丘→皮卡丘→雷丘")

    def test_different_chains_differ(self):
        self.assertNotEqual(dc.chain_key(BULBASAUR), dc.chain_key(PIKACHU))

    def test_missing_evolution_is_empty(self):
        self.assertEqual(dc.chain_key({"id": "x", "nameZh": "独角兽"}), "")


class ChainStageTest(unittest.TestCase):
    def test_stage_is_index_in_chain(self):
        self.assertEqual(dc.chain_stage(BULBASAUR), 0)
        self.assertEqual(dc.chain_stage(IVYSAUR), 1)
        self.assertEqual(dc.chain_stage(VENUSAUR), 2)

    def test_final_stage(self):
        self.assertEqual(dc.chain_final_stage(BULBASAUR), 2)
        self.assertEqual(dc.chain_final_stage(PIKACHU), 2)

    def test_name_not_in_chain_is_stage_zero(self):
        self.assertEqual(dc.chain_stage({"id": "9", "nameZh": "外星种"}), 0)


class ChainPokemonByStageTest(unittest.TestCase):
    POKEDEX = [BULBASAUR, IVYSAUR, VENUSAUR, PIKACHU, PIKACHU_DUPE]

    def test_maps_stage_to_pokemon(self):
        got = dc.chain_pokemon_by_stage(BULBASAUR, self.POKEDEX)
        self.assertEqual(got[0]["id"], "0001")
        self.assertEqual(got[1]["id"], "0002")
        self.assertEqual(got[2]["id"], "0003")

    def test_missing_stage_absent(self):
        # 皮丘不在这个测试用的 pokedex 里，所以那一级不出现
        got = dc.chain_pokemon_by_stage(PIKACHU, self.POKEDEX)
        self.assertNotIn(0, got)
        self.assertEqual(got[1]["id"], "0025")


class AdvanceChainTest(unittest.TestCase):
    def test_first_care_creates_entry(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子→妙蛙草→妙蛙花"]
        self.assertEqual(st["care"], 1)
        self.assertEqual(st["stage"], 0)
        self.assertEqual(st["pendingShiny"], 0)
        self.assertEqual(st["nextShinyAt"], 30)

    def test_care_accumulates(self):
        chains = {}
        for _ in range(5):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["care"], 5)

    def test_stage_advances_every_three_care(self):
        chains = {}
        for _ in range(3):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["stage"], 1)

    def test_stage_advances_again_at_six(self):
        chains = {}
        for _ in range(6):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["stage"], 2)

    def test_stage_stops_at_final(self):
        chains = {}
        for _ in range(12):
            dc.advance_chain(chains, BULBASAUR)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["stage"], 2)

    def test_care_thirty_grants_one_guarantee(self):
        chains = {}
        for _ in range(30):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子→妙蛙草→妙蛙花"]
        self.assertEqual(st["pendingShiny"], 1)
        self.assertEqual(st["nextShinyAt"], 60)

    def test_next_guarantee_at_sixty(self):
        chains = {}
        for _ in range(60):
            dc.advance_chain(chains, BULBASAUR)
        st = chains["妙蛙种子→妙蛙草→妙蛙花"]
        self.assertEqual(st["pendingShiny"], 2)
        self.assertEqual(st["nextShinyAt"], 90)

    def test_other_chain_is_independent(self):
        chains = {}
        dc.advance_chain(chains, BULBASAUR)
        dc.advance_chain(chains, PIKACHU)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["care"], 1)
        self.assertEqual(chains["皮丘→皮卡丘→雷丘"]["care"], 1)

    def test_returns_same_dict_in_place(self):
        chains = {}
        got = dc.advance_chain(chains, BULBASAUR)
        self.assertIs(got, chains)
        self.assertEqual(chains["妙蛙种子→妙蛙草→妙蛙花"]["care"], 1)

    def test_no_evolution_adds_no_entry(self):
        chains = {}
        dc.advance_chain(chains, {"id": "9", "nameZh": "外星种"})
        self.assertEqual(chains, {})


class ApplyFormsTest(unittest.TestCase):
    POKEDEX = [BULBASAUR, IVYSAUR, VENUSAUR, PIKACHU, PIKACHU_DUPE]

    def _collected(self, pid, form):
        return [{"id": pid, "nameZh": "x", "shiny": False, "date": "2026-11-16",
                 "sources": ["weread"], "form": form}]

    def test_stage_zero_keeps_own_form(self):
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 1, "stage": 0}}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "0001")

    def test_stage_one_sets_next_form(self):
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 3, "stage": 1}}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "0002")

    def test_stage_two_sets_final_form(self):
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 6, "stage": 2}}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "0003")

    def test_chain_without_state_untouched(self):
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "0001")

    def test_unknown_id_untouched(self):
        col = self._collected("9999", "9999")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 3, "stage": 1}}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "9999")

    def test_stage_beyond_chain_leaves_form(self):
        # 链只有 3 级，stage 4 没有对应形态 —— 原样不动
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 99, "stage": 4}}, self.POKEDEX)
        self.assertEqual(col[0]["form"], "0001")

    def test_collected_id_unchanged(self):
        # form 只影响展示，id 不变 —— 「已收服 N/1028」不会倒退
        col = self._collected("0001", "0001")
        dc.apply_forms(col, {"妙蛙种子→妙蛙草→妙蛙花": {"care": 6, "stage": 2}}, self.POKEDEX)
        self.assertEqual(col[0]["id"], "0001")


if __name__ == "__main__":
    unittest.main()
