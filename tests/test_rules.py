"""裁决执行器测试：白名单 / 边界 / 防刷子 / 境界时间流 / 守恒。"""

import unittest

from app.core.rules import NumericState
from app.pack.numeric import DEFAULT_SCHEMA, parse_numeric_schema
from pathlib import Path
from app.pack import load_packs

FANREN = parse_numeric_schema(
    next(p for p in load_packs(Path(__file__).resolve().parent.parent / "script")
         if "凡人" in p.title))


def new_state() -> NumericState:
    return NumericState.new_game(FANREN, spirit="四灵根", location="七玄门",
                                 name="测试", starting_stones=10)


class TestApplyEffects(unittest.TestCase):
    def test_stone_delta(self):
        st = new_state()
        applied, rejected = st.apply_effects([
            {"ref": "灵石", "op": "+", "v": 3, "reason": "卖出灵芝"},
            {"ref": "灵石", "op": "-", "v": 1, "reason": "购买地图"},
        ])
        self.assertEqual(st.stones, 12)
        self.assertEqual(len(applied), 2)
        self.assertEqual(rejected, [])

    def test_lifespan_forbidden(self):
        st = new_state()
        _, rejected = st.apply_effects([{"ref": "寿元", "op": "+", "v": 50, "reason": "奇怪的丹药"}])
        self.assertEqual(len(rejected), 1)
        self.assertIn("寿元", rejected[0]["why"])

    def test_negative_stones_rejected(self):
        st = new_state()
        st.apply_effects([{"ref": "灵石", "op": "-", "v": 99, "reason": "买法宝"}])
        self.assertEqual(st.stones, 10, "灵石不足应整条拒收，状态不变")

    def test_unknown_ref_rejected(self):
        st = new_state()
        _, rejected = st.apply_effects([{"ref": "战斗力", "op": "+", "v": 5, "reason": "x"}])
        self.assertEqual(len(rejected), 1)

    def test_stone_gain_clamped(self):
        st = new_state()
        applied, _ = st.apply_effects([{"ref": "灵石", "op": "+", "v": 100, "reason": "神手谷机缘"}])
        self.assertEqual(applied[0]["v"], 30.0)
        self.assertEqual(st.stones, 40)
        self.assertIn("压缩", applied[0]["reason"])

    def test_item_count_clamped(self):
        st = new_state()
        st.apply_effects([{"item": "筑基丹", "action": "add", "note": "数量 +100"}])
        entry = next(i for i in st.inventory if i["name"] == "筑基丹")
        self.assertEqual(entry["count"], 9)

    def test_item_add_remove(self):
        st = new_state()
        st.apply_effects([{"item": "灵芝", "action": "add", "note": "数量 +2"}])
        st.apply_effects([{"item": "灵芝", "action": "add"}])
        entry = next(i for i in st.inventory if i["name"] == "灵芝")
        self.assertEqual(entry["count"], 3)
        st.apply_effects([{"item": "灵芝", "action": "remove", "note": "数量 -3"}])
        self.assertFalse(any(i["name"] == "灵芝" for i in st.inventory))
        _, rejected = st.apply_effects([{"item": "灵芝", "action": "remove"}])
        self.assertIn("未持有", rejected[0]["why"])

    def test_flag_and_anchor_passthrough(self):
        st = new_state()
        applied, _ = st.apply_effects([
            {"flag": "墨大夫真面目", "value": "true"},
            {"anchor": "血色禁地"},
        ])
        self.assertTrue(st.flags["墨大夫真面目"])
        self.assertEqual(len(applied), 2)

    def test_illegal_structure(self):
        st = new_state()
        _, rejected = st.apply_effects(["nonsense", 42, {"foo": "bar"}])
        self.assertEqual(len(rejected), 3)


class TestAntiGrind(unittest.TestCase):
    def test_repeated_gain_decays(self):
        st = new_state()
        gains = []
        for _ in range(6):
            applied, _ = st.apply_effects([{"ref": "灵石", "op": "+", "v": 2, "reason": "采药卖出"}])
            gains.append(applied[0]["v"])
        # 前三次原值，之后 1, 0.5, 0.25 递减
        self.assertEqual(gains[:3], [2, 2, 2])
        self.assertEqual(gains[3], 1)
        self.assertEqual(gains[4], 0.5)
        self.assertEqual(gains[5], 0.25)
        self.assertEqual(st.stones, 10 + 2 + 2 + 2 + 1 + 0.5 + 0.25)

    def test_different_reason_not_decayed(self):
        st = new_state()
        for reason in ("采药", "跑腿", "捡漏"):
            applied, _ = st.apply_effects([{"ref": "灵石", "op": "+", "v": 2, "reason": reason}])
            self.assertEqual(applied[0]["v"], 2)


class TestRealmProgression(unittest.TestCase):
    def test_progress_fills_and_advances_layer(self):
        st = new_state()
        age_before = st.age
        for _ in range(2):
            st.apply_effects([{"ref": "修为", "op": "+", "v": 60, "reason": "闭关修炼"}])
        self.assertEqual(st.realm_name, "练气2层")
        self.assertEqual(st.age, age_before + 1, "升一层扣一年")
        self.assertEqual(st.progress, 20.0)

    def test_realm_breakthrough_costs_years(self):
        st = NumericState.new_game(FANREN, location="x")
        st.realm_index, st.stage_index = 0, 12          # 练气13层圆满
        st.age = 30
        self.assertTrue(st.realm_breakthrough())
        self.assertEqual(st.realm_name, "筑基初期")
        self.assertEqual(st.age, 40, "练气→筑基扣10年")
        self.assertGreater(st.lifespan_cap, 100, "寿元上限跃升")


class TestRoundTrip(unittest.TestCase):
    def test_serialization(self):
        st = new_state()
        st.apply_effects([{"ref": "灵石", "op": "+", "v": 5, "reason": "r"},
                          {"item": "丹药", "action": "add"}])
        st2 = NumericState(FANREN, st.to_dict())
        self.assertEqual(st2.stones, st.stones)
        self.assertEqual(st2.inventory, st.inventory)
        self.assertEqual(st2.realm_name, st.realm_name)

    def test_broadcast_real_data(self):
        st = new_state()
        fields = {f["label"]: f["value"] for f in st.broadcast()}
        self.assertEqual(fields["灵石"], "10块")
        self.assertIn("练气", fields["境界"])
        self.assertIn("七玄门", fields["地点"])


class TestConservation(unittest.TestCase):
    def test_20_turn_ledger(self):
        """20 回合守恒：最终灵石 = 初始 + Σ(被接受的增量)。"""
        st = new_state()
        ledger = 10
        effects_seq = [
            [{"ref": "灵石", "op": "+", "v": 3, "reason": f"任务奖励{i}"}] for i in range(10)
        ] + [
            [{"ref": "灵石", "op": "-", "v": 1, "reason": f"杂费{i}"}] for i in range(10)
        ]
        for effects in effects_seq:
            applied, rejected = st.apply_effects(effects)
            for d in applied:
                if d["ref"] == "灵石":
                    ledger += d["v"] if d["op"] == "+" else -d["v"]
            self.assertTrue(all(r["effect"] not in applied for r in rejected))
        self.assertEqual(st.stones, ledger, "状态必须与台账一致")


if __name__ == "__main__":
    unittest.main()
