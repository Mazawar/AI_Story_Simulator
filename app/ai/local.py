"""本地推理后端：llama-cpp-python + GGUF（延迟导入，缺失时给出可操作提示）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .backend import LLMBackend, Message

# 择优顺序：主力 → 快速档 → 增强档（见 DESIGN.md §4 与 models/README.md）
_PREFERRED_PATTERNS = (
    "qwen3-1.7b", "qwen3-1_7b", "qwen2.5-1.5b",
    "qwen3-0.6b", "qwen3-0_6b", "qwen2.5-0.5b",
    "qwen3-4b",
)


def find_model_file(models_dir: Path) -> Path | None:
    """扫描 models/ 目录，按偏好顺序选择 GGUF 文件。"""
    if not models_dir.is_dir():
        return None
    ggufs = [p for p in models_dir.iterdir() if p.suffix.lower() == ".gguf"]
    if not ggufs:
        return None
    for pattern in _PREFERRED_PATTERNS:
        for p in sorted(ggufs):
            if pattern in p.name.lower():
                return p
    return sorted(ggufs)[0]


class LocalBackend(LLMBackend):
    name = "local"

    def __init__(self, model_path: Path | str, *, n_ctx: int = 8192, n_threads: int | None = None):
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise RuntimeError(
                "未安装 llama-cpp-python。请执行：pip install llama-cpp-python"
            ) from e
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise RuntimeError(f"找不到模型文件：{self.model_path}（见 models/README.md）")
        kwargs = {"n_ctx": n_ctx, "verbose": False}
        if n_threads:
            kwargs["n_threads"] = n_threads
        self.llm = Llama(str(self.model_path), **kwargs)

    def generate(self, messages: list[Message], *, max_tokens: int = 1024,
                 temperature: float = 0.8, stop: list[str] | None = None) -> str:
        out = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop or None,
        )
        return out["choices"][0]["message"]["content"] or ""

    def stream(self, messages: list[Message], *, max_tokens: int = 1024,
               temperature: float = 0.8, stop: list[str] | None = None) -> Iterator[str]:
        stream = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop or None,
            stream=True,
        )
        for chunk in stream:
            delta = chunk["choices"][0].get("delta", {})
            piece = delta.get("content")
            if piece:
                yield piece
