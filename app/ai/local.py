"""本地推理后端：llama-cpp-python + GGUF（延迟导入，缺失时给出可操作提示）。

Qwen3 等思考型模型的处理：
- 默认在最后一条用户消息追加 "/no_think" 软开关（关闭内心独白）；
- 输出端仍剥离 <think>…</think> 块（含流式状态机），确保叙事干净。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from .backend import LLMBackend, Message

# 择优顺序：主力 → 快速档 → 增强档（见 DESIGN.md §4 与 models/README.md）
_PREFERRED_PATTERNS = (
    "qwen3-1.7b", "qwen3-1_7b", "qwen2.5-1.5b",
    "qwen3-0.6b", "qwen3-0_6b", "qwen2.5-0.5b",
    "qwen3-4b",
)

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN_ONLY_RE = re.compile(r"^\s*<think>.*?(?:</think>\s*)?", re.DOTALL)

# ---- 上下文窗口分档（Qwen3 原生 32k，YaRN 可扩至 128k） ----------------------
NATIVE_CTX = 32768
CTX_TIERS = (32768, 65536, 98304, 131072)
CHARS_PER_TOKEN = 1.4        # 中文经验值


def pick_context_window(prompt_chars: int, history_chars: int = 0,
                        gen_reserve_tokens: int = 1024) -> int:
    """按剧本包体量选上下文档位：10% 余量，最大 128k。"""
    need = int(prompt_chars / CHARS_PER_TOKEN) + int(history_chars / CHARS_PER_TOKEN) \
        + gen_reserve_tokens
    for tier in CTX_TIERS:
        if need <= int(tier * 0.9):
            return tier
    return CTX_TIERS[-1]


def strip_think(text: str) -> str:
    """剥离思考块：配平的 <think>…</think>，以及只有开标签的残块。"""
    text = _THINK_RE.sub("", text)
    text = _THINK_OPEN_ONLY_RE.sub("", text)
    return text.lstrip()


class _ThinkStreamFilter:
    """流式剥离 <think>…</think>：跨 chunk 维护状态。"""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._skip = False
        self._buf = ""

    def _partial_suffix_len(self, tag: str) -> int:
        for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
            if self._buf.endswith(tag[:k]):
                return k
        return 0

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            if self._skip:
                end = self._buf.find(self.CLOSE)
                if end >= 0:
                    self._buf = self._buf[end + len(self.CLOSE):]
                    self._skip = False
                    continue
                keep = self._partial_suffix_len(self.CLOSE)
                self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                break
            start = self._buf.find(self.OPEN)
            if start >= 0:
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(self.OPEN):]
                self._skip = True
                continue
            keep = self._partial_suffix_len(self.OPEN)
            cut = len(self._buf) - keep
            if cut > 0:
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return "" if self._skip else rest


def prepare_messages(messages: list[Message], *, no_think: bool = True) -> list[Message]:
    """追加 Qwen3 软开关（不修改原列表）。"""
    if not no_think:
        return messages
    prepared = [dict(m) for m in messages]
    for m in reversed(prepared):
        if m["role"] == "user":
            m["content"] = str(m["content"]).rstrip() + " /no_think"
            break
    return prepared


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

    # 剧本包系统提示词约 1.2 万 token（1.5 万字），必须给足窗口：
    # Qwen3 系列原生 32k，小内存机器可在构造时调低
    def __init__(self, model_path: Path | str, *, n_ctx: int = 32768,
                 n_threads: int | None = None, no_think: bool = True,
                 kv_q8: bool = False):
        super().__init__()
        try:
            from llama_cpp import GGML_TYPE_Q8_0, Llama
        except ImportError as e:
            raise RuntimeError(
                "未安装 llama-cpp-python。请执行：uv sync --extra local"
            ) from e
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise RuntimeError(f"找不到模型文件：{self.model_path}（见 models/README.md）")
        self.no_think = no_think
        kwargs = {"n_ctx": n_ctx, "verbose": False}
        if n_threads:
            kwargs["n_threads"] = n_threads
        if kv_q8:
            # 量化 KV：省约一半内存，但本构建实测提示词处理慢 ~3 倍，默认关闭；
            # 低内存机器可开（需同时开 flash attention）
            kwargs.update(flash_attn=True,
                          type_k=GGML_TYPE_Q8_0, type_v=GGML_TYPE_Q8_0)
        if n_ctx > NATIVE_CTX:
            # YaRN 上下文扩展（Qwen3 官方支持 32k→131k），按档位缩放 RoPE
            from llama_cpp import LLAMA_ROPE_SCALING_TYPE_YARN

            scale = n_ctx / NATIVE_CTX
            kwargs.update(rope_scaling_type=LLAMA_ROPE_SCALING_TYPE_YARN,
                          rope_freq_scale=1.0 / scale,
                          yarn_orig_ctx=NATIVE_CTX)
        self.n_ctx = n_ctx
        self.llm = Llama(str(self.model_path), **kwargs)

    def generate(self, messages: list[Message], *, max_tokens: int = 1024,
                 temperature: float = 0.8, stop: list[str] | None = None) -> str:
        with self._gen_lock:
            out = self.llm.create_chat_completion(
                messages=prepare_messages(messages, no_think=self.no_think),
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or None,
                repeat_penalty=1.15,       # 抗复读：小模型倾向逐字复述上一轮
            )
        return strip_think(out["choices"][0]["message"]["content"] or "")

    def generate_json(self, messages: list[Message], *, max_tokens: int = 1024,
                      temperature: float = 0.3) -> dict:
        """裁决生成：GBNF 语法强制输出合法裁决 JSON。

        生成可能撞上 token 上限截断（narrative 过长）——先尝试截断抢救，
        失败则不带语法重生成一次（上限翻倍）。
        """
        from .gbnf import grammar_object, salvage_adjudication

        with self._gen_lock:
            out = self.llm.create_chat_completion(
                messages=prepare_messages(messages, no_think=self.no_think),
                max_tokens=max_tokens,
                temperature=temperature,
                grammar=grammar_object(),
                repeat_penalty=1.15,
            )
        content = strip_think(out["choices"][0]["message"]["content"] or "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            salvaged = salvage_adjudication(content)
            if salvaged is not None:
                return salvaged
            with self._gen_lock:
                out = self.llm.create_chat_completion(
                    messages=prepare_messages(
                        messages[:-1]
                        + [{"role": "user",
                            "content": str(messages[-1]["content"]) +
                            "\n\n（上一轮你的 JSON 因过长被截断。本轮 narrative 压缩到 40 字以内。）"}],
                        no_think=self.no_think),
                    max_tokens=max_tokens * 2,
                    temperature=max(0.2, temperature - 0.3),
                )
            content = strip_think(out["choices"][0]["message"]["content"] or "{}")
            salvaged = salvage_adjudication(content)
            if salvaged is not None:
                return salvaged
            # 最终兜底：无语法自由生成 + JSON 修复（GBNF 下模型偶尔卡死，
            # 自由 JSON 反而成功率更高；仍失败才交给上层优雅降级）
            from .backend import repair_json

            with self._gen_lock:
                out = self.llm.create_chat_completion(
                    messages=prepare_messages(messages, no_think=self.no_think),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    repeat_penalty=1.15,
                )
            return repair_json(strip_think(out["choices"][0]["message"]["content"] or "{}"))

    def stream(self, messages: list[Message], *, max_tokens: int = 1024,
               temperature: float = 0.8, stop: list[str] | None = None) -> Iterator[str]:
        # 锁全程持有：流式期间同样不允许其它线程触碰 llama_context
        with self._gen_lock:
            stream = self.llm.create_chat_completion(
                messages=prepare_messages(messages, no_think=self.no_think),
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or None,
                stream=True,
            )
            think_filter = _ThinkStreamFilter()
            for chunk in stream:
                delta = chunk["choices"][0].get("delta", {})
                piece = delta.get("content")
                if piece:
                    visible = think_filter.feed(piece)
                    if visible:
                        yield visible
            tail = think_filter.flush()
            if tail:
                yield tail
