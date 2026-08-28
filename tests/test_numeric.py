"""数值体系解析测试。"""

import unittest
from pathlib import Path

from app.pack import load_packs
from app.pack.numeric import GENERIC_SCHEMA, cn_num, parse_numeric_schema

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "script"


class TestCnNum(unittest.TestCase):
    def test_common(self):
        self.assertEqual(cn_num("十三"), 13)
        self.assertEqual(cn_num("十"), 10)
        self.assertEqual(cn_num("百"), 100)
        self.assertEqual(cn_num("二百"), 200)
        self.assertEqual(cn_num("五百"), 500)
        self.assertEqual(cn_num("千"), 1000)
        self.assertEqual(cn_num("两千"), 2000)
        self.assertEqual(cn_num("9"), 9)

    def test_invalid(self):
        self.assertIsNone(cn_num("abc"))


class TestParseFanren(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        packs = load_packs(SCRIPT_DIR)
        cls.schema = parse_numeric_schema(next(p for p in packs if "凡人" in p.title))

    def test_realms_ladder(self):
        self.assertEqual(self.schema["source"], "parsed")
        names = [r["name"] for r in self.schema["realms"]]
        self.assertEqual(names[:3], ["练气", "筑基", "结丹"])
        self.assertEqual(self.schema["realms"][0]["stages"], 13)          # 练气十三层
        self.assertEqual(self.schema["realms"][1]["stages"], ["初", "中", "后"])

    def test_lifespan_caps(self):
        caps = self.schema["lifespan_caps"]
        self.assertEqual(caps.get("练气"), 100)
        self.assertEqual(caps.get("筑基"), 200)
        self.assertEqual(caps.get("结丹"), 500)
        self.assertEqual(caps.get("元婴"), 1000)
        self.assertEqual(caps.get("化神"), 2000)

    def test_breakthrough_costs(self):
        costs = self.schema["realm_breakthrough_cost_years"]
        self.assertEqual(costs.get("筑基"), 10)
        self.assertEqual(costs.get("结丹"), 30)
        self.assertEqual(costs.get("元婴"), 80)
        self.assertEqual(costs.get("化神"), 200)

    def test_currency(self):
        self.assertEqual(self.schema["currency"]["rate"], 100)
        self.assertIn("下品", self.schema["currency"]["denoms"])

    def test_spirits(self):
        self.assertIn("四灵根", self.schema["spirits"])
        self.assertIn("天灵根", self.schema["spirits"])


class TestFallback(unittest.TestCase):
    def test_jianlai_falls_back_to_generic(self):
        packs = load_packs(SCRIPT_DIR)
        jianlai = next(p for p in packs if "剑来" in p.title)
        schema = parse_numeric_schema(jianlai)
        self.assertEqual(schema["source"], "generic")
        self.assertEqual(schema["realms"], [], "武侠包不应有境界轴")
        self.assertIn("生命", [r["ref"] for r in schema["resources"]])

    def test_new_form_pack_generic(self):
        """末日系统型包：无 numeric 章节 → 通用资源，题材无关。"""
        packs = load_packs(SCRIPT_DIR)
        moemo = next((p for p in packs if "末日" in p.title), None)
        if moemo is None:
            self.skipTest("末日包不存在")
        schema = parse_numeric_schema(moemo)
        self.assertEqual(schema["source"], "generic")
        self.assertEqual(schema["realms"], [])

    def test_perfect_world_parsed(self):
        packs = load_packs(SCRIPT_DIR)
        wanmei = next(p for p in packs if "完美" in p.title)
        schema = parse_numeric_schema(wanmei)
        self.assertEqual(schema["source"], "parsed")
        self.assertTrue(schema["realms"])


if __name__ == "__main__":
    unittest.main()
