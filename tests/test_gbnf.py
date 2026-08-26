"""GBNF 裁决语法与截断抢救测试。"""

import unittest

from app.ai.gbnf import ADJUDICATION_GRAMMAR, salvage_adjudication


class TestSalvage(unittest.TestCase):
    def test_truncated_mid_string(self):
        text = '{"narrative": "雾漫过山道，远处传来钟声，你循声望去——'
        data = salvage_adjudication(text)
        self.assertIsNotNone(data)
        self.assertIn("雾漫过山道", data["narrative"])
        # 截断的字符串会被补引号闭合
        self.assertTrue(data["narrative"].rstrip('"').endswith("望去——"))
        self.assertEqual(data["effects"], [])

    def test_complete_string_with_trailing_garbage(self):
        text = '{"narrative": "完整的一句。", "eff'
        data = salvage_adjudication(text)
        self.assertIsNotNone(data)
        self.assertEqual(data["narrative"], "完整的一句。")

    def test_no_narrative_key(self):
        self.assertIsNone(salvage_adjudication('{"foo": 1'))

    def test_empty_input(self):
        self.assertIsNone(salvage_adjudication(""))

    def test_salvage_keeps_complete_effects(self):
        # narrative 已闭合、effects 中途截断：已配平的对象应被保留
        text = ('{"narrative": "他推门而入。", "effects": ['
                '{"ref": "灵石", "op": "+", "v": 5, "reason": "卖药"}, '
                '{"ref": "灵石", "op": "+", "v": 3')
        data = salvage_adjudication(text)
        self.assertIsNotNone(data)
        self.assertEqual(data["narrative"], "他推门而入。")
        self.assertEqual(len(data["effects"]), 1)
        self.assertEqual(data["effects"][0]["v"], 5)

    def test_extract_objects_skips_strings_with_braces(self):
        text = '{"narrative": "带括号 { 的文本"} tail {"flag":"x"}'
        objs = __import__("app.ai.gbnf", fromlist=["_extract_complete_objects"]).__dict__[
            "_extract_complete_objects"](text)
        flag_objs = [o for o in objs if o.get("flag")]
        self.assertEqual(flag_objs, [{"flag": "x"}])


class TestGrammarCompiles(unittest.TestCase):
    def test_from_string(self):
        from llama_cpp import LlamaGrammar

        g = LlamaGrammar.from_string(ADJUDICATION_GRAMMAR)
        self.assertIsNotNone(g)


if __name__ == "__main__":
    unittest.main()
