"""锚点解析与求值测试（含剧透隔离断言）。"""

import unittest
from pathlib import Path

from app.core.anchors import AnchorEngine, eval_condition
from app.pack import load_packs
from app.pack.anchors import parse_anchors, parse_identity_lines, parse_random_events
from app.pack.numeric import parse_numeric_schema
from app.core.rules import NumericState

SCRIPT = Path(__file__).resolve().parent.parent / "script"
PACKS = load_packs(SCRIPT)
FANREN = next(p for p in PACKS if "凡人" in p.title)
JIANLAI = next(p for p in PACKS if "剑来" in p.title)


class TestParseAnchors(unittest.TestCase):
    def test_fanren_timeline_with_turn_window(self):
        anchors = parse_anchors(FANREN)
        timeline = [a for a in anchors if a["kind"] == "timeline"]
        self.assertGreaterEqual(len(timeline), 4)
        xutiandian = next(a for a in timeline if a["title"] == "虚天殿")
        conds = xutiandian["trigger"]["conds"]
        self.assertTrue(any(c["type"] == "turn_gte" and c["v"] == 21 for c in conds))
        self.assertTrue(any(c["type"] == "realm_gte" and c["realm"] == "结丹" for c in conds))

    def test_jianlai_numbered_anchors(self):
        anchors = parse_anchors(JIANLAI)
        timeline = [a for a in anchors if a["kind"] == "timeline"]
        self.assertGreaterEqual(len(timeline), 9, "剑来应有 ①-⑨ 主线锚点")

    def test_reveal_points_isolated(self):
        for pack in (FANREN, JIANLAI):
            reveals = [a for a in parse_anchors(pack) if a["kind"] == "reveal"]
            self.assertGreaterEqual(len(reveals), 3, f"{pack.title} 应有揭晓点")
            for r in reveals:
                self.assertEqual(r["spoiler_level"], "reveal")

    def test_identity_lines(self):
        lines = parse_identity_lines(FANREN)
        identities = {l["identity"] for l in lines}
        self.assertIn("凡人", identities)
        fanren = next(l for l in lines if l["identity"] == "凡人")
        self.assertGreaterEqual(len(fanren["nodes"]), 4)

    def test_random_events_pool(self):
        events = parse_random_events(FANREN)
        self.assertGreaterEqual(len(events), 8, "凡人包应有约 10 条随机事件")
        titles = [e["title"] for e in events]
        self.assertIn("坊市淘宝", titles)
        self.assertIn("拍卖会", titles)
        self.assertTrue(all(e["desc"] for e in events))
        # 无事件池的包返回空
        self.assertEqual(parse_random_events(JIANLAI), [])


class TestConditionEval(unittest.TestCase):
    def setUp(self):
        self.schema = parse_numeric_schema(FANREN)
        self.state = NumericState.new_game(self.schema, location="七玄门")

    def test_turn_window(self):
        cond = {"type": "all", "conds": [{"type": "turn_gte", "v": 21},
                                         {"type": "turn_lte", "v": 36}]}
        self.assertFalse(eval_condition(cond, self.state, 20, set(), []))
        self.assertTrue(eval_condition(cond, self.state, 25, set(), []))

    def test_realm_gte(self):
        cond = {"type": "realm_gte", "realm": "结丹"}
        self.assertFalse(eval_condition(cond, self.state, 30, set(), []))
        self.state.realm_index = 2
        self.assertTrue(eval_condition(cond, self.state, 30, set(), []))

    def test_flag_and_anchor_effect(self):
        self.assertFalse(eval_condition({"type": "flag", "k": "reveal:X"}, self.state, 5, set(), []))
        self.state.flags["reveal:X"] = True
        self.assertTrue(eval_condition({"type": "flag", "k": "reveal:X"}, self.state, 5, set(), []))
        cond = {"type": "anchor_effect", "title_contains": "血色禁地"}
        self.assertTrue(eval_condition(cond, self.state, 5, set(), ["玩家请求触发血色禁地"]))

    def test_depends_on(self):
        self.assertFalse(eval_condition({"type": "depends_on", "key": "tl-1"}, self.state, 99, set(), []))
        self.assertTrue(eval_condition({"type": "depends_on", "key": "tl-1"}, self.state, 99, {"tl-1"}, []))


class TestSpoilerIsolation(unittest.TestCase):
    def test_reveal_never_in_context_before_trigger(self):
        anchors = parse_anchors(FANREN)
        engine = AnchorEngine(anchors)
        state = NumericState.new_game(parse_numeric_schema(FANREN))
        # 连续推进 30 轮，不给任何 anchor 请求
        for turn in range(1, 31):
            engine.evaluate(state, turn, [])
        ctx = engine.context_block(turn=30)
        for a in anchors:
            if a["kind"] == "reveal":
                self.assertNotIn(a["desc"][:12], ctx, "揭晓点真相不得进入上下文")
        # 但时间表锚点可以被触发并出现在上下文
        self.assertIn("已发生", ctx)

    def test_reveal_released_on_request(self):
        anchors = parse_anchors(FANREN)
        engine = AnchorEngine(anchors)
        state = NumericState.new_game(parse_numeric_schema(FANREN))
        reveal = next(a for a in anchors if a["kind"] == "reveal")
        fired = engine.evaluate(state, 5, [f"玩家追问{reveal['title'][:8]}的真相"])
        keys = {a["key"] for a in fired}
        self.assertIn(reveal["key"], keys)
        self.assertTrue(engine.released_reveals())


if __name__ == "__main__":
    unittest.main()
