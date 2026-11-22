"""S6 端到端：形态改写真的走通发放流程。

前面 `test_chain_form.py` 测的是纯规则；这里测的是
**`award.apply_chain_forms` + `dex_core.advance_chain_form` 在真实数据上的协作**。
"""

import json
import pathlib
import unittest

from scripts import award
from scripts import dex_core as dc

ROOT = pathlib.Path(__file__).resolve().parent.parent
POKEDEX = ROOT / "data" / "pokedex.json"


def _by_name(pool: list[dict]) -> dict:
    return {p["nameZh"]: p for p in pool}


def _entry(pool_by_name: dict, name: str) -> dict:
    p = pool_by_name[name]
    return {
        "id": p["id"], "nameZh": p["nameZh"],
        "chain": dc.chain_key(p), "evolutionStage": dc.chain_stage(p),
        "form": p["id"], "shiny": False, "date": "2026-11-20",
    }


class ApplyChainFormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = json.loads(POKEDEX.read_text(encoding="utf-8"))["pokemon"]
        cls.by_name = _by_name(cls.pool)

    def test_backfills_chain_and_stage(self):
        """老条目没有 chain / evolutionStage ⇒ 补上。"""
        state = {"collected": [{"id": "0373", "nameZh": "暴飞龙", "form": "0373"}]}
        award.apply_chain_forms(state, self.pool)
        c = state["collected"][0]
        self.assertEqual(c["chain"], "宝贝龙")
        self.assertEqual(c["evolutionStage"], 2)

    def test_missing_form_becomes_id(self):
        """规格 §5.3：form 缺失 ⇒ 等于 id。"""
        state = {"collected": [{"id": "0373", "nameZh": "暴飞龙"}]}
        award.apply_chain_forms(state, self.pool)
        self.assertEqual(state["collected"][0]["form"], "0373")

    def test_uncollected_higher_stage_does_not_push(self):
        """没收服暴飞龙 ⇒ 宝贝龙不会被推进（推进的触发是「真的收服了」）。"""
        state = {"collected": [_entry(self.by_name, "宝贝龙")]}
        award.apply_chain_forms(state, self.pool)
        self.assertEqual(state["collected"][0]["form"], "0371")

    def test_collecting_higher_stage_pushes_lower_entry(self):
        """收服了暴飞龙（2 阶）⇒ 宝贝龙（0 阶）的 form 变成暴飞龙。"""
        state = {"collected": [_entry(self.by_name, "宝贝龙"),
                               _entry(self.by_name, "暴飞龙")]}
        award.apply_chain_forms(state, self.pool)
        by_id = {c["id"]: c for c in state["collected"]}
        self.assertEqual(by_id["0371"]["form"], "0373")
        self.assertEqual(by_id["0373"]["form"], "0373")

    def test_higher_stage_entry_is_not_pulled_back(self):
        """先收服暴飞龙（2 阶），再收服宝贝龙（0 阶）⇒ 暴飞龙不动。"""
        state = {"collected": [_entry(self.by_name, "暴飞龙"),
                               _entry(self.by_name, "宝贝龙")]}
        award.apply_chain_forms(state, self.pool)
        by_id = {c["id"]: c for c in state["collected"]}
        self.assertEqual(by_id["0373"]["form"], "0373")
        self.assertEqual(by_id["0371"]["form"], "0373")   # 宝贝龙被推到暴飞龙

    def test_middle_stage_pushes_only_lower(self):
        """甲壳龙（1 阶）只推进 0 阶的，不动 2 阶的。"""
        state = {"collected": [_entry(self.by_name, "宝贝龙"),
                               _entry(self.by_name, "甲壳龙"),
                               _entry(self.by_name, "暴飞龙")]}
        award.apply_chain_forms(state, self.pool)
        by_id = {c["id"]: c for c in state["collected"]}
        self.assertEqual(by_id["0371"]["form"], "0373")   # 宝贝龙被推到暴飞龙
        self.assertEqual(by_id["0372"]["form"], "0373")   # 甲壳龙也被推到暴飞龙
        self.assertEqual(by_id["0373"]["form"], "0373")   # 暴飞龙自己不动

    def test_sibling_forms_do_not_fight(self):
        """伊布链的 8 个分支都是 1 阶 ⇒ 谁先来谁生效，不互相覆盖。"""
        state = {"collected": [_entry(self.by_name, "冰伊布"),
                               _entry(self.by_name, "火伊布")]}
        award.apply_chain_forms(state, self.pool)
        by_id = {c["id"]: c for c in state["collected"]}
        self.assertEqual(by_id["0471"]["form"], "0471")   # 冰伊布
        self.assertEqual(by_id["0136"]["form"], "0136")   # 火伊布

    def test_other_chains_untouched(self):
        """别的链不受这条链的形态推进影响。"""
        state = {"collected": [_entry(self.by_name, "宝贝龙"),
                               _entry(self.by_name, "皮卡丘")]}
        award.apply_chain_forms(state, self.pool)
        by_id = {c["id"]: c for c in state["collected"]}
        self.assertEqual(by_id["0025"]["form"], "0025")   # 皮卡丘不受影响

    def test_is_idempotent(self):
        """同样输入反复跑，结果必须一样。"""
        state = {"collected": [_entry(self.by_name, "宝贝龙")]}
        award.apply_chain_forms(state, self.pool)
        once = json.dumps(state, ensure_ascii=False, sort_keys=True)
        award.apply_chain_forms(state, self.pool)
        self.assertEqual(json.dumps(state, ensure_ascii=False, sort_keys=True), once)

    def test_empty_collected_is_safe(self):
        state = {"collected": []}
        award.apply_chain_forms(state, self.pool)
        self.assertEqual(state["collected"], [])

    def test_unknown_id_is_skipped_not_crashed(self):
        """状态文件里有一条图鉴里没有的记录 ⇒ 不能崩。"""
        state = {"collected": [{"id": "9999", "nameZh": "不存在的宝可梦"}]}
        award.apply_chain_forms(state, self.pool)   # 不抛异常即可
        self.assertEqual(len(state["collected"]), 1)


@unittest.skipUnless(POKEDEX.exists(), "没有 data/pokedex.json")
class RealPokedexConsistencyTest(unittest.TestCase):
    """真实 pokedex.json 的一致性断言。"""

    @classmethod
    def setUpClass(cls):
        cls.pokedex = json.loads(POKEDEX.read_text(encoding="utf-8"))
        cls.pool = cls.pokedex["pokemon"]

    def test_chain_count_is_543(self):
        """543 条生物链。"""
        chains = {dc.chain_key(p) for p in self.pool}
        self.assertEqual(len(chains), 543)

    def test_every_pokemon_has_a_stage(self):
        for p in self.pool:
            with self.subTest(name=p["nameZh"]):
                self.assertIn("evolutionStage", p)
                self.assertGreaterEqual(int(p["evolutionStage"]), 0)

    def test_chain_members_share_identical_evolution(self):
        """同链成员的 evolution 数组必须逐字相同。"""
        by_chain = {}
        for p in self.pool:
            by_chain.setdefault(dc.chain_key(p), []).append(p)
        for chain, members in by_chain.items():
            arrays = {json.dumps(m.get("evolution"), ensure_ascii=False)
                      for m in members}
            with self.subTest(chain=chain):
                self.assertEqual(len(arrays), 1, f"{chain} 的成员数组不一致")

    def test_evolution_first_name_is_the_chain_root(self):
        """evolution 的第一个名字必须就是链根（chain_key 的依据）。"""
        for p in self.pool:
            with self.subTest(name=p["nameZh"]):
                evo = p.get("evolution") or []
                self.assertTrue(evo)
                self.assertEqual(evo[0], dc.chain_key(p))

    def test_no_duplicate_names_in_a_chain(self):
        """链的成员数组里不能有重复名字（老数据拍平后的重复）。"""
        for p in self.pool:
            evo = p.get("evolution") or []
            with self.subTest(name=p["nameZh"]):
                self.assertEqual(len(evo), len(set(evo)))

    def test_stages_within_a_chain_are_contiguous(self):
        """每条链的阶数必须是从 0 开始的连续整数。"""
        by_chain = {}
        for p in self.pool:
            by_chain.setdefault(dc.chain_key(p), []).append(p)
        for chain, members in by_chain.items():
            stages = sorted({int(m["evolutionStage"]) for m in members})
            with self.subTest(chain=chain):
                self.assertEqual(stages, list(range(len(stages))))


if __name__ == "__main__":
    unittest.main()
