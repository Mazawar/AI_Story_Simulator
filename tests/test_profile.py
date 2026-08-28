"""AI 剧本配置（PackProfile）测试：归一化校验与面板渲染（伪造 LLM 输出）。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.ai.backend import CannedBackend
from app.core.engine_mode import EngineSession
from app.db import Database, migrate
from app.db.dao import packs as packs_dao
from app.db.dao import plays as plays_dao
from app.pack import load_packs
from app.pack.profile import build_pack_profile, normalize_profile

SCRIPT = Path(__file__).resolve().parent.parent / "script"


class TestNormalizeProfile(unittest.TestCase):
    def test_full_valid(self):
        raw = {
            "genre": "末日生存",
            "resources": [
                {"ref": "生命", "init": 100, "max": 100, "kind": "vital"},
                {"ref": "物资", "init": 5, "kind": "currency"},
                {"ref": "弹药", "init": 12, "max": 60, "kind": "vital"},
            ],
            "realm_axis": None,
            "panels": [
                {"key": "cultivator", "title": "幸存者面板", "fields": [
                    {"label": "生命", "source": "res:生命"},
                    {"label": "物资", "source": "res:物资"},
                    {"label": "地点", "source": "location"},
                ]},
            ],
            "characters": [{"name": "老猎户", "desc": "沉默寡言的神枪手"}],
            "creation": ["姓名", "职业"],
        }
        profile = normalize_profile(raw)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["source"], "profile")
        self.assertEqual([r["ref"] for r in profile["resources"]],
                         ["生命", "物资", "弹药"])
        self.assertEqual(profile["realms"], [])
        self.assertEqual(len(profile["panels"]), 1)
        self.assertEqual(len(profile["panels"][0]["fields"]), 3)

    def test_invalid_resource_ref_in_panel_dropped(self):
        raw = {"resources": [{"ref": "生命", "init": 100, "max": 100, "kind": "vital"}],
               "panels": [{"key": "x", "title": "面板", "fields": [
                   {"label": "法力", "source": "res:法力"},       # 未声明资源 → 剔除
                   {"label": "地点", "source": "location"},
               ]}]}
        profile = normalize_profile(raw)
        fields = profile["panels"][0]["fields"]
        self.assertEqual([f["label"] for f in fields], ["地点"])

    def test_realm_axis_normalized(self):
        raw = {"resources": [{"ref": "灵石", "init": 0, "kind": "currency"}],
               "realm_axis": {"realms": [{"name": "练气", "stages": 13},
                                         {"name": "筑基", "stages": ["初", "中", "后"]}],
                              "lifespan_caps": {"练气": 100},
                              "realm_breakthrough_cost_years": {"筑基": 10},
                              "layer_cost_years": 1}}
        profile = normalize_profile(raw)
        self.assertEqual(len(profile["realms"]), 2)
        self.assertEqual(profile["lifespan_caps"]["练气"], 100)
        self.assertEqual(profile["realm_breakthrough_cost_years"]["筑基"], 10)

    def test_vital_resource_always_present(self):
        raw = {"resources": [{"ref": "灵石", "init": 0, "kind": "currency"}]}
        profile = normalize_profile(raw)
        kinds = [r["kind"] for r in profile["resources"]]
        self.assertIn("vital", kinds)

    def test_garbage_returns_none(self):
        self.assertIsNone(normalize_profile("nonsense"))
        self.assertIsNone(normalize_profile({"resources": "notalist"}))


class TestProfileGeneration(unittest.TestCase):
    """端到端：Canned 后端（JSON 能力）生成 profile → 引擎面板按配置渲染。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "prof.db")
        migrate(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_engine_uses_generated_profile(self):
        class ProfileBackend(CannedBackend):
            name = "profile-mock"

            def generate(self, messages, *, max_tokens=1024, temperature=0.8, stop=None):
                if "剧本配置生成器" in messages[0]["content"]:
                    return json.dumps({
                        "genre": "末日生存",
                        "resources": [{"ref": "生命", "init": 100, "max": 100, "kind": "vital"},
                                      {"ref": "物资", "init": 3, "kind": "currency"}],
                        "realm_axis": None,
                        "panels": [{"key": "cultivator", "title": "幸存者面板", "fields": [
                            {"label": "生命", "source": "res:生命"},
                            {"label": "物资", "source": "res:物资"},
                            {"label": "地点", "source": "location"}]}],
                        "characters": [],
                        "creation": [],
                    }, ensure_ascii=False)
                return super().generate(messages, max_tokens=max_tokens,
                                        temperature=temperature, stop=stop)

            def generate_json(self, messages, *, max_tokens=1024, temperature=0.3):
                if "剧本配置生成器" in messages[0]["content"]:
                    return json.loads(self.generate(messages))
                return super().generate_json(messages, max_tokens=max_tokens,
                                             temperature=temperature)

        pack = next(p for p in load_packs(SCRIPT) if "末日" in p.title)
        pid = plays_dao.create_playthrough(self.db, packs_dao.get_story_for_pack(
            self.db, packs_dao.upsert_pack(self.db, pack), pack.title), mode="engine")
        engine = EngineSession(self.db, ProfileBackend(), pack, pid)
        profile = build_pack_profile(pack, engine.backend)
        self.assertIsNotNone(profile)
        with self.db.locked() as conn:
            conn.execute("UPDATE storys SET metadata_json = ? WHERE id = ?",
                         (json.dumps(profile, ensure_ascii=False), engine.story_id))
            self.db.conn.commit()

        # 模拟下一局：用持久化 profile 重建
        engine2 = EngineSession(self.db, ProfileBackend(), pack, pid,
                                schema=engine._load_persisted_profile())
        self.assertEqual(engine2.schema["source"], "profile")
        self.assertEqual(engine2.schema["resources"][0]["ref"], "生命")
        # 「修士」触发词 → 按 AI 配置渲染的面板
        payload = None
        for kind, data in engine2.stream_handle("修士"):
            if kind == "note":
                payload = data
        panel = next(b for b in payload.narrative if b["type"] == "panel")
        self.assertEqual(panel["title"], "幸存者面板")
        fields = {f["label"]: f["value"] for f in panel["fields"]}
        self.assertIn("生命", fields)
        self.assertIn("物资", fields)


if __name__ == "__main__":
    unittest.main()
