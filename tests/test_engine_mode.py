"""引擎模式端到端测试（Canned 后端，不依赖模型）。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.ai.backend import CannedBackend
from app.ai.context_assembler import assemble_messages, estimate_tokens
from app.core.engine_mode import EngineSession
from app.db import Database, migrate
from app.db.dao import packs as packs_dao
from app.db.dao import plays as plays_dao
from app.pack import load_packs

SCRIPT = Path(__file__).resolve().parent.parent / "script"


def make_session(tmpdir: str, pack_title_frag: str = "凡人") -> tuple[Database, EngineSession]:
    db = Database(Path(tmpdir) / "enginetest.db")
    migrate(db)
    pack = next(p for p in load_packs(SCRIPT) if pack_title_frag in p.title)
    pack_id = packs_dao.upsert_pack(db, pack)
    story_id = packs_dao.get_story_for_pack(db, pack_id, pack.title)
    pid = plays_dao.create_playthrough(db, story_id, mode="engine")
    engine = EngineSession(db, CannedBackend(), pack, pid)
    return db, engine


class TestEngineSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db, self.engine = make_session(self.tmp.name)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _run(self, text):
        payload = None
        for kind, data in self.engine.stream_handle(text):
            if kind in ("turn", "note"):
                payload = data
        return payload

    def test_adjudication_turn_real_state(self):
        payload = self._run("我去坊市卖药")
        self.assertEqual(payload.turn_idx, 1)
        types = [b["type"] for b in payload.narrative]
        self.assertIn("broadcast", types, "每回合末尾应有引擎播报条")
        # Canned 裁决固定 +1 灵石
        self.assertEqual(self.engine.state.stones, 1)
        self.assertTrue(payload.deltas, "应用后的增量应出现在 payload.deltas")
        self.assertTrue(payload.choices, "引擎应生成四向选项")
        # 状态持久化
        with self.db.locked() as conn:
            stored = conn.execute(
                "SELECT player_json FROM playthroughs WHERE id = ?",
                (self.engine.playthrough_id,),
            ).fetchone()["player_json"]
        self.assertEqual(json.loads(stored)["stones"], 1)

    def test_cultivator_panel_trigger_word(self):
        self._run("卖药")
        payload = self._run("修士")
        panel = next(b for b in payload.narrative if b["type"] == "panel")
        self.assertEqual(panel["title"], "修士面板")
        fields = {f["label"]: f["value"] for f in panel["fields"]}
        self.assertEqual(fields["灵石"], "1块")
        self.assertIn("境界", fields)

    def test_settlement_panel(self):
        for _ in range(3):
            self._run("干活")
        payload = self._run("本章结束")
        panel = next(b for b in payload.narrative if b["type"] == "panel")
        self.assertEqual(panel["title"], "本章结算")
        self.assertIn("3 回合", "，".join(f["value"] for f in panel["fields"]))

    def test_save_resume_roundtrip(self):
        self._run("卖药")
        self._run("存档")
        snap = plays_dao.load_save(self.db, self.engine.playthrough_id, "autosave")
        self.assertEqual(snap["state"]["stones"], 1)
        # 重建会话（模拟服务重启后续玩）
        from app.core.rules import NumericState
        from app.pack.numeric import parse_numeric_schema

        state = NumericState(parse_numeric_schema(self.engine.pack), snap["state"])
        db2 = self.db
        engine2 = EngineSession(db2, CannedBackend(), self.engine.pack,
                                self.engine.playthrough_id, state=state,
                                rolling_summary=snap.get("rolling_summary", ""))
        self.assertEqual(engine2.state.stones, 1)
        self.assertEqual(engine2.turn_idx, 2)
        p2 = None
        for kind, data in engine2.stream_handle("继续干活"):
            if kind == "turn":
                p2 = data
        self.assertEqual(p2.turn_idx, 3, "续玩后回合序号连续")

    def test_no_spoiler_in_context(self):
        """揭晓点真相不得进入组装上下文（结构隔离）。"""
        from app.ai.context_assembler import assemble_messages

        for _ in range(5):
            self._run("闲逛")
        msgs = assemble_messages(
            self.engine.pack, self.engine.characters, self.engine.state,
            self.engine.recent, self.engine.rolling_summary,
            self.engine.anchor_engine.context_block(6), "试探", 6,
        )
        ctx = msgs[0]["content"] + msgs[1]["content"]
        for a in self.engine.anchor_engine.anchors:
            if a["kind"] == "reveal":
                self.assertNotIn(a["desc"][:10], ctx)

    def test_tasks_panel_identity_line(self):
        # 向导文本解析身份 → 「任务」面板渲染节点链；模型 flag 达成后翻转状态
        p = self._run("【人物已定】1.A（七玄门时期）；2.A（凡人——从最底层爬起）；3.A（四灵根）。以此身入局。")
        self.assertEqual(self.engine.state.extra.get("identity"), "凡人")

        tasks = None
        for kind, data in self.engine.stream_handle("任务"):
            if kind == "note":
                tasks = data
        panel = next(b for b in tasks.narrative if b["type"] == "panel")
        labels = [f["label"] for f in panel["fields"]]
        self.assertIn("灵根觉醒", labels)               # 凡人线首节点
        values = {f["label"]: f["value"] for f in panel["fields"]}
        self.assertEqual(values["灵根觉醒"], "· 未竟")

        # 模型以自然名标记达成 → 自动登记为 线:<节点>
        self.engine.state.flags["线·灵根觉醒"] = True
        for kind, data in self.engine.stream_handle("任务"):
            if kind == "note":
                tasks = data
        panel = next(b for b in tasks.narrative if b["type"] == "panel")
        fields = panel["fields"]
        values = {f["label"]: f["value"] for f in fields}
        self.assertEqual(values["灵根觉醒"], "✓ 已成")
        # 当前所指 = 首个未竟节点
        self.assertEqual(fields[-1]["label"], "当前所指")
        self.assertEqual(fields[-1]["value"], "求师")

    def test_context_budget(self):
        from app.ai.context_assembler import estimate_tokens

        msgs = assemble_messages(
            self.engine.pack, self.engine.characters, self.engine.state,
            [{"input": "x" * 50, "text": "y" * 400}] * 6,
            "摘" * 300, "锚" * 100, "玩家行动" * 10, 20,
        )
        self.assertLess(estimate_tokens(msgs), 9000, "组装上下文必须控制在预算内")


class TestEngineModeAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile as _tf

        cls.tmp = _tf.TemporaryDirectory()
        from app.server import create_app
        from fastapi.testclient import TestClient

        app = create_app(token="t2", dry_run=True,
                         db_path=Path(cls.tmp.name) / "api.db",
                         web_dist=Path(cls.tmp.name) / "nodist")
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_engine_mode_default(self):
        r = self.client.post("/api/play?token=t2", json={"pack_title": "凡人"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["mode"], "engine")
        self.assertEqual(body["n_ctx"], 8192)

        import time
        pid = body["playthrough_id"]
        self.client.post(f"/api/play/{pid}/input?token=t2", json={"text": "卖药"})
        deadline = time.time() + 10
        turns = []
        while time.time() < deadline:
            turns = self.client.get(f"/api/play/{pid}/history?token=t2").json()["turns"]
            if turns:
                break
            time.sleep(0.1)
        payload = turns[0]["payload"]
        types = [b["type"] for b in payload["narrative"]]
        self.assertIn("broadcast", types)
        self.assertTrue(payload.get("choices"))

    def test_direct_mode_optin(self):
        r = self.client.post("/api/play?token=t2&mode=direct", json={"pack_title": "凡人"})
        self.assertEqual(r.json()["mode"], "direct")

    def test_engine_resume(self):
        import time

        pid = self.client.post("/api/play?token=t2", json={"pack_title": "剑来"}).json()["playthrough_id"]
        self.client.post(f"/api/play/{pid}/input?token=t2", json={"text": "走走"})
        deadline = time.time() + 10
        while time.time() < deadline:
            turns = self.client.get(f"/api/play/{pid}/history?token=t2").json()["turns"]
            if turns:
                break
            time.sleep(0.1)
        r = self.client.post(f"/api/play/{pid}/resume?token=t2")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["mode"], "engine")
        self.assertGreaterEqual(body["turn_count"], 1)


if __name__ == "__main__":
    unittest.main()
