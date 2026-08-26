"""叙事解析器测试：LLM 文本 → 渲染块（剧本包「输出格式」章节的真实格式）。"""

import unittest

from app.render.narrative_parser import parse_narrative


class TestParseNarrative(unittest.TestCase):
    def test_dialogue_bold(self):
        blocks = parse_narrative("药园雾气弥漫。\n> **韩立：** 这株灵芝有些奇异。\n> **墨大夫：** 拿来我看看。")
        self.assertEqual(blocks[0]["type"], "narration")
        self.assertEqual(blocks[1], {"type": "dialogue", "speaker": "韩立", "text": "这株灵芝有些奇异。"})
        self.assertEqual(blocks[2]["speaker"], "墨大夫")

    def test_dialogue_plain(self):
        blocks = parse_narrative("> 韩立：不敢欺瞒师叔。")
        self.assertEqual(blocks[0]["type"], "dialogue")
        self.assertEqual(blocks[0]["speaker"], "韩立")

    def test_broadcast(self):
        blocks = parse_narrative("【境界 练气4层/13｜寿元 32岁｜灵石 12块｜七玄门药园】")
        self.assertEqual(blocks[0]["type"], "broadcast")
        fields = {f["label"]: f["value"] for f in blocks[0]["fields"]}
        self.assertEqual(fields["境界"], "练气4层/13")
        self.assertEqual(fields["灵石"], "12块")

    def test_choices_bracketed(self):
        text = "你决定：\n【A】收下灵石，转身离开\n【B】问清楚灵石来历\n【C】偷偷留下一块\n【D】全部退回"
        blocks = parse_narrative(text)
        self.assertEqual(blocks[-1]["type"], "choices")
        self.assertEqual(len(blocks[-1]["options"]), 4)
        self.assertEqual(blocks[-1]["options"][0]["id"], "A")
        self.assertIn("收下灵石", blocks[-1]["options"][0]["text"])

    def test_choices_lettered(self):
        blocks = parse_narrative("A. 继续赶路\nB. 在原地休息")
        self.assertEqual(blocks[-1]["type"], "choices")
        self.assertEqual(len(blocks[-1]["options"]), 2)

    def test_single_letter_line_is_narration(self):
        blocks = parse_narrative("A bird flew over the field.")
        self.assertEqual(blocks[0]["type"], "narration")
        self.assertIn("A bird", blocks[0]["text"])

    def test_bracket_line_without_separator_is_not_broadcast(self):
        blocks = parse_narrative("【韩立 · 七玄门杂役出身，四灵根修士】")
        self.assertEqual(blocks[0]["type"], "narration")

    def test_mixed_stream(self):
        text = (
            "雾气漫过药园，你蹲下身。\n"
            "> **墨大夫：** 今日的药引采齐了？\n"
            "你点头，把竹篓递过去。\n"
            "【境界 练气3层/13｜寿元 31岁｜灵石 8块｜七玄门药园】\n"
            "【A】如实回答\n【B】隐瞒一株三七\n【C】反问缘由\n【D】沉默"
        )
        blocks = parse_narrative(text)
        types = [b["type"] for b in blocks]
        self.assertIn("narration", types)
        self.assertIn("dialogue", types)
        self.assertIn("broadcast", types)
        self.assertEqual(types[-1], "choices")
        self.assertEqual(blocks[-1]["options"][1]["text"], "隐瞒一株三七")

    def test_empty(self):
        self.assertEqual(parse_narrative(""), [])


class TestSmallModelDrift(unittest.TestCase):
    """本地 1.7B 实测漂移形态的回归用例（来自真机截图）。"""

    def test_latex_lines_dropped(self):
        text = "> $$\n> \\fcolorbox{gray}{10}{\\textcolor{white}{灵石}}\n正常旁白一行。"
        blocks = parse_narrative(text)
        types = [b["type"] for b in blocks]
        self.assertEqual(types, ["narration"])
        self.assertIn("正常旁白", blocks[0]["text"])

    def test_quoted_prefix_stripped(self):
        blocks = parse_narrative("> 是的。\n> 你听得清，是墨大夫。")
        self.assertEqual(blocks[0]["type"], "narration")
        self.assertNotIn(">", blocks[0]["text"])
        self.assertIn("墨大夫", blocks[0]["text"])

    def test_bold_quoted_dialogue(self):
        blocks = parse_narrative('> ** "韩立！" **')
        # 非对话格式的引用行 → 剥净后归旁白
        self.assertEqual(blocks[0]["type"], "narration")
        self.assertIn("韩立", blocks[0]["text"])

    def test_broadcast_variant_fields(self):
        text = "> **【境界】练气3层 | 【寿元】31岁 | 【灵石】8块 | 【地点】七玄门**"
        blocks = parse_narrative(text)
        self.assertEqual(blocks[0]["type"], "broadcast")
        fields = {f["label"]: f["value"] for f in blocks[0]["fields"]}
        self.assertEqual(fields.get("境界"), "练气3层")
        self.assertEqual(fields.get("灵石"), "8块")

    def test_panel_label_lines_dropped(self):
        text = "**【播报条】**\n【境界 练气3层｜灵石 8块】"
        blocks = parse_narrative(text)
        types = [b["type"] for b in blocks]
        self.assertEqual(types, ["broadcast"])

    def test_separator_dropped(self):
        blocks = parse_narrative("上一段。\n---\n下一段。")
        self.assertEqual(len(blocks), 1)
        self.assertIn("上一段", blocks[0]["text"])
        self.assertIn("下一段", blocks[0]["text"])

    def test_quoted_choices(self):
        blocks = parse_narrative("> 【A】继续赶路\n> 【B】原地休息")
        self.assertEqual(blocks[-1]["type"], "choices")
        self.assertEqual(len(blocks[-1]["options"]), 2)


if __name__ == "__main__":
    unittest.main()
