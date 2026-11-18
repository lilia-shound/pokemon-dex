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
# 真实数据里同链成员的 evolution 数组并不一致 —— 这正是旧 key 算法的坑
WALKING_GRASS = {"id": "0004", "nameZh": "走路草",
                 "evolution": ["走路草", "臭臭花", "霸王花", "走路草", "臭臭花", "美丽花"]}
BEAUTIFUL_FLOWER = {"id": "0005", "nameZh": "霸王花",
                     "evolution": ["走路草", "臭臭花", "霸王花"]}

DAY = "2026-11-14"


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
    def test_creates_entry_with_first_date(self):
        chains = {}
        st = dc.advance_chain(chains, BULBASAUR, DAY)
        self.assertEqual(st["firstDate"], DAY)
        self.assertIsNone(st["lastShinyDate"])
        self.assertEqual(chains["妙蛙种子"], st)

    def test_mutates_in_place_and_returns_same_object(self):
        chains = {}
        got = dc.advance_chain(chains, BULBASAUR, DAY)
        self.assertIs(got, chains["妙蛙种子"])
        self.assertIs(got, dc.advance_chain(chains, BULBASAUR, "2026-11-20"))

    def test_existing_first_date_is_kept(self):
        """再次抽到同一条链不重置计时起点。"""
        chains = {}
        dc.advance_chain(chains, BULBASAUR, DAY)
        st = dc.advance_chain(chains, IVYSAUR, "2026-12-01")
        self.assertEqual(st["firstDate"], DAY)

    def test_no_evolution_creates_no_entry(self):
        chains = {}
        self.assertEqual(dc.advance_chain(chains, {"id": "x", "nameZh": "独角兽"}, DAY), {})
        self.assertEqual(chains, {})

    def test_legacy_entry_without_first_date_gets_one(self):
        """★ 旧结构的链条目没有 firstDate ⇒ 补上，否则这条链永远养不起来。

        旧结构是 {"care":…, "pendingShiny":…, "nextShinyAt":…}，
        计时起点缺失 ⇒ chain_care 恒为 0 ⇒ 保底永远兑现不了。
        """
        chains = {"妙蛙种子": {"care": 3, "pendingShiny": 0, "nextShinyAt": 30}}
        st = dc.advance_chain(chains, BULBASAUR, DAY)
        self.assertEqual(st["firstDate"], DAY)
        self.assertIsNone(st["lastShinyDate"])
        self.assertEqual(dc.chain_care(st, "2026-12-14"), 30)

    def test_legacy_entry_keeps_existing_first_date(self):
        """补 firstDate 只在缺的时候补 —— 已有起点不动。"""
        chains = {"妙蛙种子": {"firstDate": "2026-10-01"}}
        st = dc.advance_chain(chains, BULBASAUR, DAY)
        self.assertEqual(st["firstDate"], "2026-10-01")


class ChainCareTest(unittest.TestCase):
    def test_care_is_days_since_first_collection(self):
        chain = {"firstDate": "2026-11-14", "lastShinyDate": None}
        self.assertEqual(dc.chain_care(chain, "2026-11-14"), 0)
        self.assertEqual(dc.chain_care(chain, "2026-11-15"), 1)
        self.assertEqual(dc.chain_care(chain, "2026-12-14"), 30)

    def test_care_zero_without_first_date(self):
        self.assertEqual(dc.chain_care({}, "2026-11-14"), 0)

    def test_care_zero_on_bad_date(self):
        chain = {"firstDate": "不是日期", "lastShinyDate": None}
        self.assertEqual(dc.chain_care(chain, "2026-11-14"), 0)


class ChainShinyReadyTest(unittest.TestCase):
    def test_not_ready_before_thirty_days(self):
        chain = {"firstDate": "2026-11-14", "lastShinyDate": None}
        self.assertFalse(dc.chain_shiny_ready(chain, "2026-12-13"))   # 29 天

    def test_ready_at_thirty_days(self):
        chain = {"firstDate": "2026-11-14", "lastShinyDate": None}
        self.assertTrue(dc.chain_shiny_ready(chain, "2026-12-14"))    # 30 天

    def test_not_ready_again_right_after_a_shiny(self):
        """兑现过一次后，要再养满 30 天才能再兑现。"""
        chain = {"firstDate": "2026-11-14", "lastShinyDate": "2026-12-14"}
        self.assertFalse(dc.chain_shiny_ready(chain, "2026-12-20"))

    def test_ready_again_thirty_days_after_the_last_shiny(self):
        chain = {"firstDate": "2026-11-14", "lastShinyDate": "2026-12-14"}
        self.assertTrue(dc.chain_shiny_ready(chain, "2027-01-13"))   # 距上次 30 天

    def test_care_grows_with_calendar_not_with_chain_size(self):
        """★ 这条是「旧口径的 bug」的护栏。

        旧实现按抽卡次数累计 care，而 pick 只从未收服池抽 ⇒ 一条链最多被抽「链大小」次
        （最大链只有 9 只）⇒ care 上限 9，永远够不到 30 ⇒ 保底闪光数学上不可能触发。
        改成按日历天后，链的大小不再影响可达性。
        """
        chains = {}
        dc.advance_chain(chains, VENUSAUR, DAY)          # 只有 1 只成员的链
        self.assertEqual(dc.chain_care(chains["妙蛙种子"], "2026-12-14"), 30)
        self.assertTrue(dc.chain_shiny_ready(chains["妙蛙种子"], "2026-12-14"))


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
        """543 = 真实生物链数。

        推导：按「evolution 数组共现」建连通分量（另一种自然的链定义），
        得到的连通分量数 = 543，且与基础形态名分组逐组一致。
        pokedex.json 重生成后这个数会变 —— 那时该重新核对，而不是直接改数字。
        """
        groups = {}
        for x in self.pokedex:
            groups.setdefault(dc.chain_key(x), []).append(x["nameZh"])
        self.assertEqual(len(groups), 543)

    def test_no_chain_is_longer_than_nine_members(self):
        """★ 「按抽卡次数累计 care」为什么不可能触发 —— 链的大小上限。

        pick 只从未收服池抽 ⇒ 同一条链最多被抽「链大小」次。
        最大链是伊布族（9 只）⇒ 旧口径的 care 上限是 9 < 30。
        """
        groups = {}
        for x in self.pokedex:
            groups.setdefault(dc.chain_key(x), []).append(x["nameZh"])
        self.assertLessEqual(max(len(v) for v in groups.values()), 9)


if __name__ == "__main__":
    unittest.main()
