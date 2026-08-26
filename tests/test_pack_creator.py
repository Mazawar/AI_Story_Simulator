"""剧本包结构化解析测试（角色卡 / 首轮创建步骤）。"""

import unittest
from pathlib import Path

from app.pack import load_packs, parse_character_cards, parse_creation_steps

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "script"


class TestRealPacks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs = load_packs(SCRIPT_DIR)
        cls.by_title = {p.title: p for p in cls.packs}

    def test_character_cards(self):
        for pack in self.packs:
            with self.subTest(pack=pack.title):
                cards = parse_character_cards(pack)
                self.assertGreaterEqual(len(cards), 3, "至少应解析出主要角色卡")
                for c in cards:
                    self.assertTrue(c["name"])
                    self.assertTrue(c["desc"])
        fanren = parse_character_cards(self.by_title["凡人修仙传：人界篇"])
        names = [c["name"] for c in fanren]
        self.assertIn("韩立", names)
        self.assertIn("墨大夫", names)
        hanli = next(c for c in fanren if c["name"] == "韩立")
        self.assertIn("七玄门", hanli["desc"])

    def test_creation_steps(self):
        fanren = parse_creation_steps(self.by_title["凡人修仙传：人界篇"])
        self.assertGreaterEqual(len(fanren), 2, "凡人包应有分步创建")
        for step in fanren:
            self.assertTrue(step["question"])
            self.assertGreaterEqual(len(step["options"]), 2)
            for opt in step["options"]:
                self.assertIn(opt["id"], "ABCD")
                self.assertTrue(opt["text"])
        # 第一步是时期选择
        self.assertIn("时候", fanren[0]["question"])
        texts = " ".join(o["text"] for o in fanren[0]["options"])
        self.assertIn("七玄门", texts)

    def test_direct_wrapper_applied(self):
        from app.core.engine import _wrapped_system_prompt

        prompt = _wrapped_system_prompt(self.by_title["凡人修仙传：人界篇"])
        self.assertIn("输出合同", prompt)
        self.assertIn("禁止再输出任何开局问卷", prompt)
        self.assertIn("凡人修仙传", prompt)


class TestSynthetic(unittest.TestCase):
    def test_minimal(self):
        from app.pack import split_sections
        from app.pack.models import Pack

        text = (
            "【测试包】\n一、世界观\n正文\n二、角色卡\n"
            "【韩立 · 四灵根修士】\n- 行为锚：话不多\n"
            "【词条角色 · 一句话卡】\n【张三 · 路人甲】\n"
            "七、首轮输出\n"
            "【第一步】你的身份？\n【A】凡人——从最底层爬起\n【B】散修——自由但一无所有\n"
            "【第二步】你的资质？\n【A】四灵根\n【B】天灵根\n"
        )
        pack = Pack(title="测试包", file_path="t.txt", raw_text=text,
                    sections=split_sections(text))
        cards = parse_character_cards(pack)
        names = [c["name"] for c in cards]
        self.assertIn("韩立", names)
        self.assertIn("张三", names)
        self.assertNotIn("词条角色", names)

        steps = parse_creation_steps(pack)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["question"], "你的身份？")
        self.assertEqual(len(steps[0]["options"]), 2)

    def test_no_steps_returns_empty(self):
        from app.pack import split_sections
        from app.pack.models import Pack

        text = "【测试包】\n七、首轮输出\n直接开始剧情，没有分步。"
        pack = Pack(title="t", file_path="t.txt", raw_text=text,
                    sections=split_sections(text))
        self.assertEqual(parse_creation_steps(pack), [])


if __name__ == "__main__":
    unittest.main()
