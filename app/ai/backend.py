"""LLMBackend 抽象：本地 / 在线 / 演练后端实现同一接口，上层无感知。

接口（DESIGN.md §10）：
- generate(messages) -> str           单次生成
- stream(messages) -> Iterator[str]   流式生成
- generate_json(messages) -> dict     结构化生成（含 JSON 修复）
"""

from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator

Message = dict  # {"role": "system"|"user"|"assistant", "content": str}


class LLMBackend(ABC):
    name = "base"

    def __init__(self):
        # 生成级互斥：llama.cpp 的 llama_context 非线程安全——同一实例被
        # 两个线程并发 generate 会硬崩（进程消失、无 Python 异常）。
        # 回合推理与后台任务（如剧本配置生成）共用实例时必须串行。
        self._gen_lock = threading.RLock()

    @abstractmethod
    def generate(self, messages: list[Message], *, max_tokens: int = 1024,
                 temperature: float = 0.8, stop: list[str] | None = None) -> str:
        raise NotImplementedError

    def stream(self, messages: list[Message], *, max_tokens: int = 1024,
               temperature: float = 0.8, stop: list[str] | None = None) -> Iterator[str]:
        # 默认实现：不支持流式的后端退化为一次性输出
        yield self.generate(messages, max_tokens=max_tokens, temperature=temperature, stop=stop)

    def generate_json(self, messages: list[Message], *, max_tokens: int = 1024,
                      temperature: float = 0.3) -> dict:
        text = self.generate(messages, max_tokens=max_tokens, temperature=temperature)
        return repair_json(text)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def repair_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象：剥代码围栏、截取首个配平的 {...}，
    并容错小模型常见毛病（尾随逗号、中文引号）。"""
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    text = text.replace("“", '"').replace("”", '"')
    start = text.find("{")
    if start < 0:
        raise ValueError(f"模型输出中未找到 JSON 对象：{text[:120]!r}")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = _TRAILING_COMMA_RE.sub(r"\1", text[start : i + 1])
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    text = candidate
                    raise
    raise ValueError("模型输出的 JSON 对象未闭合")


class CannedBackend(LLMBackend):
    """演练后端：无模型/无网络时验证管线闭环（demo --dry-run、单元测试）。"""

    name = "canned"

    def generate(self, messages: list[Message], *, max_tokens: int = 1024,
                 temperature: float = 0.8, stop: list[str] | None = None) -> str:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        head = user.strip()[:24] or "(沉默)"
        return (
            f"(演练旁白) 你说了：「{head}」。\n"
            "> **神秘声音：** 世界听见了你的声音，但此刻无人应答。\n"
            "(演练模式未接入真实模型——这是 Canned 后端的固定回复，用于验证管线。)"
        )

    def generate_json(self, messages: list[Message], *, max_tokens: int = 1024,
                      temperature: float = 0.3) -> dict:
        """引擎模式演练：返回固定裁决 JSON（含一次数值变化，供管线验证）。"""
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        head = user.strip()[:20] or "(沉默)"
        return {
            "narrative": f"(演练旁白) 你行动了：「{head}」。山道尽头传来陌生的脚步声，"
                         "一名背药篓的老者朝你走来，目光在你腰间的竹牌上停了一瞬。\n"
                         "> **神秘声音：** 因果已记下这一笔。",
            "effects": [{"ref": "灵石", "op": "+", "v": 1, "reason": "演练奖励"}],
            "choices": ["迎上老者，主动搭话", "退到路旁，暗中观察", "原路返回", "屏息藏进林中"],
        }
