"""S6：进化链形态改写。

规则（详见 `scripts/dex_core.advance_chain_form` 的 docstring）：

    链推进 = 这条链收服了阶更高的成员。
    收服 X（阶 s）时，同链里阶 < s 的已收服条目，form 改成 X 的 id；
    阶 ≥ s 的不动（高阶不会被低阶拉回去）。
"""

import unittest

from scripts import dex_core as dc

# 宝贝龙链：宝贝龙(0) → 甲壳龙(1) → 暴飞龙(2)
CHARMANDER = {"id": "0371", "nameZh": "宝贝龙",
               "evolution": ["宝贝龙", "甲壳龙", "暴飞龙"], "evolutionStage": 0}
CHARMELEON = {"id": "0372", "nameZh": "甲壳龙",
              "evolution": ["宝贝龙", "甲壳龙", "暴飞龙"], "evolutionStage": 1}
CHARIZARD = {"id": "0373", "nameZh": "暴飞龙",
             "evolution": ["宝贝龙", "甲壳龙", "暴飞龙"], "evolutionStage": 2}

# 伊布链：伊布(0) → 8 个 1 阶分支
EEVEE = {"id": "0470", "nameZh": "伊布",
         "evolution": ["伊布", "仙子伊布", "冰伊布", "叶伊布", "太阳伊布",
                       "月亮伊布", "水伊布", "火伊布", "雷伊布"], "evolutionStage": 0}
GLACEON = {"id": "0471", "nameZh": "冰伊布",
           "evolution": ["伊布", "仙子伊布", "冰伊布", "叶伊布", "太阳伊布",
                         "月亮伊布", "水伊布", "火伊布", "雷伊布"], "evolutionStage": 1}

# 走路草链：走路草(0) → 臭臭花(1) → 霸王花(2) / 美丽花(2)（兄弟同阶）
BULBASAUR = {"id": "0001", "nameZh": "妙蛙种子",
             "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"], "evolutionStage": 0}
IVYSAUR = {"id": "0002", "nameZh": "妙蛙草",
           "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"], "evolutionStage": 1}
VENUSAUR = {"id": "0003", "nameZh": "妙蛙花",
            "evolution": ["妙蛙种子", "妙蛙草", "妙蛙花"], "evolutionStage": 2}


def entry(pokemon: dict, form: str | None = None) -> dict:
    """造一条 collected 条目（chain / evolutionStage 都带上）。"""
    return {
        "id": pokemon["id"],
        "nameZh": pokemon["nameZh"],
        "chain": dc.chain_key(pokemon),
        "evolutionStage": pokemon["evolutionStage"],
        "form": form or pokemon["id"],
        "shiny": False,
    }


def stage_lookup(*pokemons: dict):
    table = {str(p["id"]): int(p["evolutionStage"]) for p in pokemons}
    return lambda pid: int(table.get(str(pid), 0) or 0)


class ChainStageTest(unittest.TestCase):
    def test_reads_evolution_stage(self):
        self.assertEqual(dc.chain_stage(CHARIZARD), 2)
        self.assertEqual(dc.chain_stage(CHARMANDER), 0)

    def test_missing_field_is_zero(self):
        self.assertEqual(dc.chain_stage({"id": "x", "nameZh": "独角兽"}), 0)

    def test_bad_value_is_zero(self):
        self.assertEqual(dc.chain_stage({"evolutionStage": "abc"}), 0)


class AdvanceChainFormTest(unittest.TestCase):
    def test_higher_stage_pushes_lower_entry(self):
        """收服暴飞龙（2 阶）⇒ 宝贝龙（0 阶）的 form 改成暴飞龙。"""
        col = [entry(CHARMANDER)]
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=stage_lookup(CHARIZARD))
        self.assertEqual(col[0]["form"], "0373")

    def test_root_stage_never_pushes(self):
        """链根（0 阶）不会把同链的形态往回拉。"""
        col = [entry(CHARIZARD)]
        dc.advance_chain_form(col, CHARMANDER, stage_lookup=stage_lookup(CHARMANDER))
        self.assertEqual(col[0]["form"], "0373")

    def test_lower_stage_does_not_overwrite_higher(self):
        """先收服暴飞龙再收服宝贝龙 ⇒ 暴飞龙的 form 不能被改回去。"""
        col = [entry(CHARIZARD), entry(CHARMANDER)]
        dc.advance_chain_form(col, CHARMANDER, stage_lookup=stage_lookup(CHARMANDER))
        self.assertEqual(col[0]["form"], "0373")   # 暴飞龙自己不动
        self.assertEqual(col[1]["form"], "0371")   # 宝贝龙还是自己

    def test_same_stage_sibling_does_not_overwrite(self):
        """兄弟形态同阶（伊布的 8 个分支都是 1 阶）⇒ 谁先来谁生效。"""
        col = [entry(GLACEON)]
        dc.advance_chain_form(col, EEVEE, stage_lookup=stage_lookup(EEVEE))
        self.assertEqual(col[0]["form"], "0471")   # 冰伊布没被 0 阶的伊布改写

    def test_different_chain_untouched(self):
        """别的链不受影响。"""
        col = [entry(BULBASAUR)]
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=stage_lookup(CHARIZARD))
        self.assertEqual(col[0]["form"], "0001")

    def test_is_idempotent(self):
        """同样输入反复跑，结果必须一样。"""
        col = [entry(CHARMANDER)]
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=stage_lookup(CHARIZARD))
        once = dict(col[0])
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=stage_lookup(CHARIZARD))
        self.assertEqual(col[0], once)

    def test_empty_inputs_are_safe(self):
        self.assertEqual(dc.advance_chain_form([], CHARIZARD), [])
        self.assertEqual(dc.advance_chain_form([entry(CHARMANDER)], {}), [entry(CHARMANDER)])

    def test_missing_form_treated_as_id(self):
        """老数据没有 form ⇒ 视为等于 id，仍可被推进。"""
        col = [{"id": "0371", "nameZh": "宝贝龙", "chain": "宝贝龙",
                "evolutionStage": 0}]
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=stage_lookup(CHARIZARD))
        self.assertEqual(col[0]["form"], "0373")

    def test_form_may_point_to_uncollected_pokemon(self):
        """规格 §5.4：form 指向的宝可梦本身不必被单独收服。"""
        col = [entry(CHARMANDER)]
        lookup = stage_lookup(CHARIZARD)          # 暴飞龙不在 collected 里
        dc.advance_chain_form(col, CHARIZARD, stage_lookup=lookup)
        self.assertEqual(col[0]["form"], "0373")


class ChainKeyFromCollectedTest(unittest.TestCase):
    def test_collected_entry_uses_stored_chain(self):
        """collected 条目没有 evolution 字段 ⇒ 用存的 chain 字段。"""
        self.assertEqual(dc.chain_key({"id": "0371", "chain": "宝贝龙"}), "宝贝龙")

    def test_prefers_evolution_when_present(self):
        self.assertEqual(dc.chain_key(CHARIZARD), "宝贝龙")

    def test_no_evolution_and_no_chain_is_empty(self):
        self.assertEqual(dc.chain_key({"id": "x", "nameZh": "独角兽"}), "")


if __name__ == "__main__":
    unittest.main()
