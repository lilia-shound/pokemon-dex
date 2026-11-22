"""S6：`scripts/evo_tree.py` —— 从全库重建进化树。

**测试用真实上游数据当夹具**（42arch 的 data/pokemon），
因为这个模块的价值全在「面对真实脏数据能不能算对」上 —— 手写的理想夹具测不出那些坑。
"""

import json
import pathlib
import unittest

from scripts import evo_tree as et

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "pokemon-dataset-zh" / "data" / "pokemon"
POKEDEX = ROOT / "data" / "pokedex.json"


def _species(paths: list[list[dict]]) -> list[dict]:
    """把「几条 path」包装成上游 JSON 的形状。"""
    return [{"name_zh": p[0]["name"] if p else "?", "evolution_chains": paths}]


def _node(name: str, stage: str = "未进化", from_: str | None = None) -> dict:
    return {"name": name, "stage": stage, "from": from_}


class StageValueTest(unittest.TestCase):
    def test_known_labels(self):
        self.assertEqual(et.stage_value("未进化"), 0)
        self.assertEqual(et.stage_value("幼年"), 0)
        self.assertEqual(et.stage_value("1阶进化"), 1)
        self.assertEqual(et.stage_value("2阶进化"), 2)

    def test_unknown_label_is_zero(self):
        """未知标签宁可当起点，也别当进阶。"""
        self.assertEqual(et.stage_value("???阶进化"), 0)
        self.assertEqual(et.stage_value(None), 0)


class BuildTreeSmallTest(unittest.TestCase):
    """小夹具：一条直线链 + 一条分叉链。"""

    def setUp(self):
        self.species = [
            {"name_zh": "皮丘", "evolution_chains": [[
                _node("皮丘", "幼年"), _node("皮卡丘", "1阶进化", "皮丘"),
                _node("雷丘", "2阶进化", "皮卡丘")]]},
            {"name_zh": "伊布", "evolution_chains": [
                [_node("伊布", "未进化"), _node("水伊布", "1阶进化", "伊布")],
                [_node("伊布", "未进化"), _node("火伊布", "1阶进化", "伊布")],
            ]},
        ]
        self.tree = et.build_tree(self.species)

    def test_linear_chain_is_one_group(self):
        self.assertEqual(self.tree["root_of"]["雷丘"], "皮丘")
        self.assertEqual(self.tree["members"]["皮丘"], ["皮丘", "皮卡丘", "雷丘"])

    def test_depth_counts_levels(self):
        self.assertEqual(et.stage_of(self.tree, "皮丘"), 0)
        self.assertEqual(et.stage_of(self.tree, "皮卡丘"), 1)
        self.assertEqual(et.stage_of(self.tree, "雷丘"), 2)

    def test_branch_stays_one_group(self):
        """伊布的 8 个分支（这里是 2 个）必须同组。"""
        self.assertEqual(self.tree["root_of"]["水伊布"], "伊布")
        self.assertEqual(self.tree["root_of"]["火伊布"], "伊布")
        self.assertEqual(sorted(self.tree["members"]["伊布"]),
                         ["伊布", "水伊布", "火伊布"])

    def test_siblings_share_a_stage(self):
        """兄弟形态同阶（分支都是 1 阶）。"""
        self.assertEqual(et.stage_of(self.tree, "水伊布"),
                         et.stage_of(self.tree, "火伊布"))

    def test_evolution_array_is_identical_for_chain_members(self):
        a = et.evolution_array(self.tree, "皮丘")
        b = et.evolution_array(self.tree, "雷丘")
        self.assertEqual(a, b)

    def test_stage_for_chain(self):
        self.assertEqual(et.stage_for_chain(self.tree, "皮丘"), 2)
        self.assertEqual(et.stage_for_chain(self.tree, "伊布"), 1)


class BuildTreeDirtyDataTest(unittest.TestCase):
    """脏数据：自报的 from 写错、path 里父节点写串。"""

    def test_majority_vote_survives_wrong_from(self):
        """少数文件把自己的 from 写成 None ⇒ 链不能散。"""
        species = [
            {"name_zh": "甲", "evolution_chains": [[
                _node("甲", "未进化"), _node("乙", "1阶进化", "甲"),
                _node("丙", "2阶进化", "乙")]]},
            # 丙自己的文件里 from 写错了（写成 None）
            {"name_zh": "丙", "evolution_chains": [[
                _node("甲", "未进化"), _node("乙", "1阶进化", "甲"),
                _node("丙", "2阶进化")]]},
        ]
        tree = et.build_tree(species)
        self.assertEqual(tree["root_of"]["丙"], "甲")
        self.assertEqual(tree["members"]["甲"], ["甲", "乙", "丙"])

    def test_path_adjacency_is_hard_fact(self):
        """链的中间节点只在链根的文件里出现 ⇒ 没有自报 from 也要能连上。"""
        species = [
            {"name_zh": "甲", "evolution_chains": [[
                _node("甲", "未进化"), _node("乙", "1阶进化", "甲"),
                _node("丙", "2阶进化", "乙")]]},
        ]
        tree = et.build_tree(species)
        self.assertEqual(tree["root_of"]["丙"], "甲")
        self.assertEqual(et.stage_of(tree, "丙"), 2)

    def test_cycle_does_not_hang(self):
        """父节点成环时不能死循环（会内存爆掉被 OOM 杀，rc=137）。"""
        species = [
            {"name_zh": "甲", "evolution_chains": [[
                _node("甲", "未进化"), _node("乙", "1阶进化", "甲")]]},
            {"name_zh": "乙", "evolution_chains": [[
                _node("甲", "未进化"), _node("乙", "1阶进化", "甲")]]},
        ]
        # 人为造环：甲的父是乙、乙的父是甲
        tree = et.build_tree(species)
        for name in tree["root_of"]:
            self.assertIsInstance(tree["root_of"][name], str)

    def test_extra_names_are_independent_chains(self):
        """预告条目（上游没有）各自独立成链，不编造进化关系。"""
        species = [
            {"name_zh": "甲", "evolution_chains": [[_node("甲", "未进化")]]},
        ]
        tree = et.build_tree(species, ["预告甲"])
        self.assertEqual(tree["root_of"]["预告甲"], "预告甲")
        self.assertEqual(tree["members"]["预告甲"], ["预告甲"])


@unittest.skipUnless(CACHE_DIR.is_dir(),
                     "没有素材缓存（.cache/pokemon-dataset-zh）⇒ 跳过真实数据测试")
class RealDataTest(unittest.TestCase):
    """真实上游数据 —— 数字必须钉死。"""

    @classmethod
    def setUpClass(cls):
        cls.species = et.load_species_from_dir(CACHE_DIR)
        cls.pokedex = json.loads(POKEDEX.read_text(encoding="utf-8"))
        extra = [p["nameZh"] for p in cls.pokedex["pokemon"]
                 if p.get("preview") and p.get("nameZh")]
        cls.tree = et.build_tree(cls.species, extra)

    def test_chain_count(self):
        """1025 只上游 + 3 只预告 = 543 条生物链（与规格一致）。"""
        self.assertEqual(len(self.species), 1025)
        self.assertEqual(len(self.tree["members"]), 543)

    def test_known_chain_members(self):
        cases = {
            "伊布": ["伊布", "仙子伊布", "冰伊布", "叶伊布", "太阳伊布",
                     "月亮伊布", "水伊布", "火伊布", "雷伊布"],
            "皮丘": ["皮丘", "皮卡丘", "雷丘"],
            "走路草": ["走路草", "臭臭花", "美丽花", "霸王花"],
            "刺尾虫": ["刺尾虫", "甲壳茧", "盾甲茧", "毒粉蛾", "狩猎凤蝶"],
            "宝贝龙": ["宝贝龙", "甲壳龙", "暴飞龙"],
            "熊宝宝": ["熊宝宝", "圈圈熊", "月月熊"],
            "黏黏宝": ["黏黏宝", "黏美儿", "黏美龙"],
        }
        for root, members in cases.items():
            with self.subTest(root=root):
                self.assertEqual(self.tree["members"][root], members)

    def test_every_species_is_in_exactly_one_group(self):
        total = sum(len(v) for v in self.tree["members"].values())
        self.assertEqual(total, len(self.species) + 3)   # +3 预告

    def test_no_cycles(self):
        """所有节点都能走到一个没有父节点的根。"""
        for name, root in self.tree["root_of"].items():
            with self.subTest(name=name):
                self.assertNotIn(root, self.tree["parent_of"])

    def test_stages_are_contiguous_from_zero(self):
        """每条链的阶数必须是从 0 开始的连续整数（不能有洞）。"""
        for root, members in self.tree["members"].items():
            stages = sorted({et.stage_of(self.tree, m) for m in members})
            with self.subTest(root=root):
                self.assertEqual(stages, list(range(len(stages))))

    def test_siblings_have_same_stage(self):
        """同父的兄弟必须同阶（美丽花/霸王花都是 2 阶）。"""
        children = {}
        for name, parent in self.tree["parent_of"].items():
            children.setdefault(parent, []).append(name)
        for parent, kids in children.items():
            stages = {et.stage_of(self.tree, k) for k in kids}
            with self.subTest(parent=parent):
                self.assertEqual(len(stages), 1, f"{parent} 的孩子不同阶：{stages}")

    def test_pokedex_evolution_arrays_are_consistent(self):
        """data/pokedex.json 里同链成员的 evolution 数组必须逐字相同。"""
        by_root = {}
        for p in self.pokedex["pokemon"]:
            root = self.tree["root_of"].get(p["nameZh"])
            if root:
                by_root.setdefault(root, []).append(p)
        for root, members in by_root.items():
            arrays = {json.dumps(m.get("evolution"), ensure_ascii=False)
                      for m in members}
            with self.subTest(root=root):
                self.assertEqual(len(arrays), 1)

    def test_pokedex_stage_matches_tree(self):
        """pokedex.json 的 evolutionStage 必须与树算出来的一致。"""
        for p in self.pokedex["pokemon"]:
            name = p["nameZh"]
            if name not in self.tree["root_of"]:
                continue
            with self.subTest(name=name):
                self.assertEqual(p.get("evolutionStage"),
                                 et.stage_of(self.tree, name))

    def test_pokedex_chain_key_is_the_root(self):
        """pokedex.json 的 evolution 第一个名字必须就是链根。"""
        for p in self.pokedex["pokemon"]:
            name = p["nameZh"]
            root = self.tree["root_of"].get(name)
            if not root:
                continue
            with self.subTest(name=name):
                self.assertEqual((p.get("evolution") or [None])[0], root)


if __name__ == "__main__":
    unittest.main()
