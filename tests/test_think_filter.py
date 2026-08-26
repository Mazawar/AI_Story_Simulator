"""思考型模型输出剥离测试（strip_think / 流式过滤器 / 消息准备）。"""

import unittest

from app.ai.local import (
    _ThinkStreamFilter,
    prepare_messages,
    strip_think,
)


class TestStripThink(unittest.TestCase):
    def test_balanced_block(self):
        self.assertEqual(strip_think("<think>\n盘算一下\n</think>\n\n正文"), "正文")

    def test_open_only_block(self):
        self.assertEqual(strip_think("<think>\n\n</think>正文"), "正文")

    def test_no_block(self):
        self.assertEqual(strip_think("普通叙事"), "普通叙事")

    def test_block_in_middle(self):
        self.assertEqual(strip_think("前段<think>x</think>后段"), "前段后段")


class TestThinkStreamFilter(unittest.TestCase):
    def _run(self, chunks):
        f = _ThinkStreamFilter()
        return "".join(f.feed(c) for c in chunks) + f.flush()

    def test_split_across_chunks(self):
        # think 标签与内容被任意切分
        chunks = ["<thi", "nk>内心", "独白</th", "ink>正文开", "始"]
        self.assertEqual(self._run(chunks), "正文开始")

    def test_no_think(self):
        self.assertEqual(self._run(["纯叙事", "内容"]), "纯叙事内容")

    def test_unclosed_think_all_dropped(self):
        self.assertEqual(self._run(["<think>还没想完"]), "")

    def test_partial_tag_held_then_resolved(self):
        # "<" 后续是普通文字而非标签，应放出
        self.assertEqual(self._run(["1 < 2，", "成立"]), "1 < 2，成立")


class TestPrepareMessages(unittest.TestCase):
    def test_appends_no_think_to_last_user(self):
        msgs = [{"role": "system", "content": "s"},
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "a"},
                {"role": "user", "content": "第二句"}]
        prepared = prepare_messages(msgs)
        self.assertEqual(prepared[-1]["content"], "第二句 /no_think")
        self.assertEqual(prepared[1]["content"], "第一句")
        self.assertEqual(msgs[-1]["content"], "第二句", "原列表不被修改")

    def test_disable(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(prepare_messages(msgs, no_think=False)[0]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
