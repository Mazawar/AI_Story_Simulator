"""上下文窗口分档测试。"""

import unittest

from app.ai.local import CTX_TIERS, NATIVE_CTX, pick_context_window


class TestPickContextWindow(unittest.TestCase):
    def test_small_pack_native(self):
        # 现有三个包（1.6万-2.1万字）都应落在原生 32k 档
        self.assertEqual(pick_context_window(16039), NATIVE_CTX)
        self.assertEqual(pick_context_window(21028), NATIVE_CTX)

    def test_45k_chars_pack_yarn_64k(self):
        # 用户上限：四五万字剧本包 → 64k 档（YaRN 2x）
        self.assertEqual(pick_context_window(45000), 65536)
        self.assertEqual(pick_context_window(50000), 65536)

    def test_larger_tiers(self):
        # 预算=字数/1.4+预留，90% 余量：7万字→64k档；12万字→96k档
        self.assertEqual(pick_context_window(70000), 65536)
        self.assertEqual(pick_context_window(120000), 98304)

    def test_over_limit_clamps_to_max(self):
        self.assertEqual(pick_context_window(500000), CTX_TIERS[-1])

    def test_history_counts(self):
        # 历史也会占预算：小包 + 超长历史 → 升档
        self.assertEqual(pick_context_window(16039, history_chars=0), NATIVE_CTX)
        self.assertGreater(pick_context_window(16039, history_chars=60000), NATIVE_CTX)


if __name__ == "__main__":
    unittest.main()
