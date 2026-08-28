"""渲染契约与 AI 抽象测试。"""

import unittest

from app.ai.backend import CannedBackend, repair_json
from app.render.contract import (
    Choice, Delta, Entity, TurnPayload,
    narration_payload, note_payload,
)


class TestContract(unittest.TestCase):
    def test_payload_json_roundtrip(self):
        payload = TurnPayload(
            turn_idx=3,
            narrative=[
                {"type": "narration", "text": "雾气漫过药园。"},
                {"type": "dialogue", "speaker": "韩立", "speaker_ref": "character:7", "text": "……"},
            ],
            entities=[Entity(ref="character:7", surface="韩立")],
            deltas=[Delta(ref="attr:灵石", op="+", v=2, reason="卖出草药")],
            choices=[Choice(id=1, text="收下灵石", tags=["顺应"])],
            fx={"level": "minor"},
        )
        restored = TurnPayload.from_json(payload.to_json())
        self.assertEqual(restored.turn_idx, 3)
        self.assertEqual(restored.entities[0].ref, "character:7")
        self.assertEqual(restored.deltas[0].v, 2)
        self.assertEqual(restored.choices[0].tags, ["顺应"])
        self.assertEqual(restored.narrative[1]["speaker"], "韩立")

    def test_helper_payloads(self):
        p1 = narration_payload(1, "文本")
        self.assertEqual(p1.narrative[0]["type"], "narration")
        p2 = note_payload(2, "已存档", panel="save")
        self.assertEqual(p2.panel, "save")


class TestRepairJson(unittest.TestCase):
    def test_fenced_json(self):
        self.assertEqual(repair_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_prose(self):
        text = '好的，以下是结果：{"a": {"b": 2}} 请查收'
        self.assertEqual(repair_json(text), {"a": {"b": 2}})

    def test_trailing_comma_tolerated(self):
        self.assertEqual(repair_json('{"a": 1, "b": [1, 2,],}'), {"a": 1, "b": [1, 2]})

    def test_chinese_quotes_tolerated(self):
        self.assertEqual(repair_json('{"narrative": “带中文引号的文本。”, "effects": []}'),
                         {"narrative": "带中文引号的文本。", "effects": []})

    def test_missing_json_raises(self):
        with self.assertRaises(ValueError):
            repair_json("没有任何结构化内容")


class TestCannedBackend(unittest.TestCase):
    def test_generate_and_stream(self):
        backend = CannedBackend()
        reply = backend.generate([{"role": "user", "content": "你好"}])
        self.assertIn("演练", reply)
        pieces = list(backend.stream([{"role": "user", "content": "你好"}]))
        self.assertTrue(pieces)


if __name__ == "__main__":
    unittest.main()
