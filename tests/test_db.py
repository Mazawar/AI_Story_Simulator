"""数据层测试：迁移、DAO 往返、参数绑定形态。"""

import tempfile
import unittest
from pathlib import Path

from app.db import Database, migrate
from app.db.dao import packs as packs_dao
from app.db.dao import plays as plays_dao
from app.db.dao import settings as settings_dao
from app.pack.models import Pack, PackSection


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        migrate(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_migrate_idempotent(self):
        self.assertEqual(migrate(self.db), self.db.schema_version())
        self.assertEqual(self.db.schema_version(), 2)

    def test_settings_roundtrip(self):
        settings_dao.set_setting(self.db, "api_base_url", "https://api.example.com/v1")
        self.assertEqual(
            settings_dao.get_setting(self.db, "api_base_url"),
            "https://api.example.com/v1",
        )
        settings_dao.set_setting(self.db, "api_base_url", "https://api2.example.com/v1")
        self.assertEqual(
            settings_dao.get_setting(self.db, "api_base_url"),
            "https://api2.example.com/v1",
        )
        self.assertIsNone(settings_dao.get_setting(self.db, "nope"))

    def test_pack_upsert_roundtrip(self):
        pack = Pack(
            title="测试包", file_path="x.txt", raw_text="一、世界观\n正文",
            sections=[PackSection(num="一", key="world", title="世界观", body="正文", order_idx=1)],
        )
        pack_id = packs_dao.upsert_pack(self.db, pack)
        pack.sections[0].body = "正文改"
        pack_id2 = packs_dao.upsert_pack(self.db, pack)
        self.assertEqual(pack_id, pack_id2, "同标题同路径应更新而非重复插入")

        row = self.db.conn.execute(
            "SELECT title, parse_status FROM packs WHERE id = ?", (pack_id,)
        ).fetchone()
        self.assertEqual(row["title"], "测试包")
        self.assertEqual(row["parse_status"], "loaded")

        n_sections = self.db.conn.execute(
            "SELECT COUNT(*) FROM pack_sections WHERE pack_id = ?", (pack_id,)
        ).fetchone()[0]
        self.assertEqual(n_sections, 1, "重写入库应覆盖旧章节")

        story_id = packs_dao.get_story_for_pack(self.db, pack_id, "测试包")
        story_id2 = packs_dao.get_story_for_pack(self.db, pack_id, "测试包")
        self.assertEqual(story_id, story_id2)

    def test_playthrough_turns_saves(self):
        pack = Pack(
            title="对局包", file_path="p.txt", raw_text="一、世界观\n正文",
            sections=[PackSection(num="一", key="world", title="世界观", body="正文", order_idx=1)],
        )
        story_id = packs_dao.get_story_for_pack(self.db, packs_dao.upsert_pack(self.db, pack), "对局包")
        play_id = plays_dao.create_playthrough(self.db, story_id=story_id, mode="direct")
        plays_dao.add_turn(self.db, play_id, 1, {"narrative": [{"type": "narration", "text": "a"}]}, "输入")
        plays_dao.add_turn(self.db, play_id, 2, {"system_note": "已存档"}, "存档")

        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM turns WHERE playthrough_id = ?", (play_id,)
        ).fetchone()[0]
        self.assertEqual(count, 2)
        turn_count = self.db.conn.execute(
            "SELECT turn_count FROM playthroughs WHERE id = ?", (play_id,)
        ).fetchone()["turn_count"]
        self.assertEqual(turn_count, 2)

        plays_dao.write_save(self.db, play_id, "autosave", "摘要", {"turn_idx": 2})
        plays_dao.write_save(self.db, play_id, "autosave", "摘要2", {"turn_idx": 5})
        snap = plays_dao.load_save(self.db, play_id, "autosave")
        self.assertEqual(snap["turn_idx"], 5, "同槽位存档应覆盖")
        self.assertIsNone(plays_dao.load_save(self.db, play_id, "slot9"))


if __name__ == "__main__":
    unittest.main()
