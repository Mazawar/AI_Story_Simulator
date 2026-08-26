"""LLMBackend 抽象：本地 / 在线 / 演练后端实现同一接口，上层无感知。

接口（DESIGN.md §10）：
- generate(messages) -> str           单次生成
- stream(messages) -> Iterator[str]   流式生成
- generate_json(messages) -> dict     结构化生成（含 JSON 修复）
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator

Message = dict  # {"role": "system"|"user"|"assistant", "content": str}


class LLMBackend(ABC):
    name = "base"

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


def repair_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象：剥代码围栏、截取首个配平的 {...}。"""
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
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
                return json.loads(text[start : i + 1])
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
        head = user.strip()[:24] or "(沉默)"
        return {
            "narrative": f"(演练旁白) 你行动了：「{head}」。世界以微妙的方式回应了你。\n"
                         "> **神秘声音：** 因果已记下这一笔。",
            "effects": [{"ref": "灵石", "op": "+", "v": 1, "reason": "演练奖励"}],
        }
