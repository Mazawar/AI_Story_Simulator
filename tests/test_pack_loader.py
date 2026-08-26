"""剧本包加载器测试：对 script/ 下三个真实剧本包验收。"""

import unittest
from pathlib import Path

from app.pack import load_pack, load_packs, split_sections
from app.pack.loader import read_text
from app.pack.models import normalize_section_key

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "script"


class TestSplitSections(unittest.TestCase):
    def test_plain_and_bracketed_headers(self):
        text = "序言内容\n【剑来 · 开放世界】人生模拟器\n一、世界观\n正文A\n二、角色卡\n【韩立 · 卡】\n正文B\n【九、存档与续玩】\n存档说明"
        sections = split_sections(text)
        keys = [s.key for s in sections]
        self.assertIn("preamble", keys)
        self.assertIn("world", keys)
        self.assertIn("characters", keys)
        self.assertIn("saving", keys)
        by_key = {s.key: s for s in sections}
        self.assertIn("正文A", by_key["world"].body)
        self.assertIn("【韩立 · 卡】", by_key["characters"].body)
        self.assertIn("存档说明", by_key["saving"].body)
        self.assertIn("序言内容", by_key["preamble"].body)

    def test_normalize_keys(self):
        self.assertEqual(normalize_section_key("世界观"), "world")
        self.assertEqual(normalize_section_key("状态播报与面板"), "panels")
        self.assertEqual(normalize_section_key("首轮输出"), "opening")
        self.assertEqual(normalize_section_key("世界活性机制"), "dynamics")
        self.assertEqual(normalize_section_key("随便什么"), "unknown")

    def test_no_sections_still_returns_preamble(self):
        sections = split_sections("只有一段没有章节头的文本")
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].key, "preamble")


class TestRealPacks(unittest.TestCase):
    """对 script/ 三个剧本包的验收：章节切分必须完整。"""

    EXPECTED_KEYS = {"world", "characters", "numeric", "output_format", "constraints", "opening"}

    def test_three_packs_loaded(self):
        packs = load_packs(SCRIPT_DIR)
        self.assertEqual(len(packs), 3, "script/ 下应有三个剧本包")

    def test_sections_complete(self):
        for pack in load_packs(SCRIPT_DIR):
            with self.subTest(pack=pack.title):
                keys = {s.key for s in pack.sections}
                missing = self.EXPECTED_KEYS - keys
                self.assertFalse(missing, f"缺少章节：{missing}")
                for key in self.EXPECTED_KEYS:
                    body = pack.section(key).body
                    self.assertGreater(len(body), 50, f"章节 {key} 内容过短")

    def test_title_extracted(self):
        pack = load_pack(SCRIPT_DIR / "凡人修仙传.txt")
        self.assertIn("凡人修仙传", pack.title)

    def test_raw_text_decodable(self):
        text = read_text(SCRIPT_DIR / "剑来.txt")
        self.assertGreater(len(text), 10000)
        self.assertIn("世界观", text)


if __name__ == "__main__":
    unittest.main()
