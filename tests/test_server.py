"""本地服务 API 测试（FastAPI TestClient，演练后端）。"""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.server import create_app


class TestServerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app = create_app(token="t-test", dry_run=True,
                         db_path=Path(cls.tmp.name) / "test.db",
                         web_dist=Path(cls.tmp.name) / "no-dist")
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()   # 上下文管理：退出时触发 shutdown 关闭 DB

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_token_required(self):
        self.assertEqual(self.client.get("/api/health").status_code, 403)
        self.assertEqual(self.client.get("/api/health?token=wrong").status_code, 403)

    def test_health_and_packs(self):
        r = self.client.get("/api/health", headers={"X-Auth-Token": "t-test"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        r = self.client.get("/api/packs?token=t-test")
        self.assertEqual(r.status_code, 200)
        titles = [p["title"] for p in r.json()["packs"]]
        self.assertGreaterEqual(len(titles), 4, "script/ 下应有至少四个剧本包")

    def test_play_flow_with_history(self):
        r = self.client.post("/api/play?token=t-test", json={"pack_title": "凡人"})
        self.assertEqual(r.status_code, 200)
        pid = r.json()["playthrough_id"]

        r = self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "我是谁？"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        # 演练后端在后台线程执行，轮询 history 直到回合落库
        deadline = time.time() + 10
        turns = []
        while time.time() < deadline:
            history = self.client.get(f"/api/play/{pid}/history?token=t-test").json()
            turns = history["turns"]
            if len(turns) >= 1:
                break
            time.sleep(0.1)
        self.assertGreaterEqual(len(turns), 1)
        self.assertEqual(turns[0]["player_input"], "我是谁？")
        self.assertTrue(turns[0]["payload"]["narrative"])

        # 触发词：存档
        self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "存档"})
        deadline = time.time() + 10
        saves = []
        while time.time() < deadline:
            saves = self.client.get(f"/api/play/{pid}/saves?token=t-test").json()["saves"]
            if saves:
                break
            time.sleep(0.1)
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0]["slot"], "autosave")

    def test_unknown_playthrough_404(self):
        r = self.client.post("/api/play/9999/input?token=t-test", json={"text": "hi"})
        self.assertEqual(r.status_code, 404)

    def test_empty_input_400(self):
        r = self.client.post("/api/play?token=t-test", json={"pack_title": "凡人"})
        pid = r.json()["playthrough_id"]
        r = self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "  "})
        self.assertEqual(r.status_code, 400)

    def _wait_turns(self, pid, minimum=1, timeout=10):
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            turns = self.client.get(f"/api/play/{pid}/history?token=t-test").json()["turns"]
            if len(turns) >= minimum:
                return turns
            _t.sleep(0.1)
        return []

    def test_resume_flow(self):
        # 开局 → 一回合 → 存档 → 续玩端点重建会话 → /api/plays 列表可见
        pid = self.client.post("/api/play?token=t-test", json={"pack_title": "凡人"}).json()["playthrough_id"]
        self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "我是谁？"})
        self.assertTrue(self._wait_turns(pid))

        self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "存档"})
        import time as _t
        deadline = _t.time() + 10
        saves = []
        while _t.time() < deadline:
            saves = self.client.get(f"/api/play/{pid}/saves?token=t-test").json()["saves"]
            if saves:
                break
            _t.sleep(0.1)
        self.assertEqual(len(saves), 1)

        # 续玩：应重建会话并保留回合计数
        r = self.client.post(f"/api/play/{pid}/resume?token=t-test")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["resumed"])
        self.assertGreaterEqual(body["turn_count"], 2)
        self.assertIn(body["pack_title"], "凡人修仙传：人界篇")

        # 续玩后可以继续交互
        self.client.post(f"/api/play/{pid}/input?token=t-test", json={"text": "继续"})
        self.assertTrue(self._wait_turns(pid, minimum=3))

        # 列表接口
        plays = self.client.get("/api/plays?token=t-test").json()["plays"]
        self.assertGreaterEqual(len(plays), 1)
        entry = next(p for p in plays if p["id"] == pid)
        self.assertEqual(entry["story_title"], "凡人修仙传：人界篇")
        self.assertTrue(entry["save_summary"])

    def test_packs_meta_enriched(self):
        packs = self.client.get("/api/packs?token=t-test").json()["packs"]
        fanren = next(p for p in packs if "凡人" in p["title"])
        self.assertGreaterEqual(len(fanren["characters"]), 3)
        self.assertGreaterEqual(len(fanren["creation_steps"]), 2)
        names = [c["name"] for c in fanren["characters"]]
        self.assertIn("韩立", names)


if __name__ == "__main__":
    unittest.main()
