"""渲染契约：TurnPayload（DESIGN.md §5.1）。

引擎与 UI 之间只传结构化 JSON；UI 不解析自由文本。
阶段 1 的 WebEngine 视图按此契约渲染交互面板与实体链接。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Entity:
    """叙事中出现的实体引用（实体链接的锚点）。"""

    ref: str            # "character:7" / "item:31" / "attr:灵石"
    surface: str        # 文本中的写法


@dataclass
class Delta:
    """一次数值变化（渲染为浮动 +N 动画）。"""

    ref: str
    op: str             # "+" / "-"
    v: float
    reason: str = ""


@dataclass
class Choice:
    """一个行动选项（四向选项卡的卡片）。"""

    id: int
    text: str
    tags: list[str] = field(default_factory=list)
    hint: str = ""


@dataclass
class TurnPayload:
    """一个回合的完整渲染数据。"""

    turn_idx: int
    narrative: list[dict] = field(default_factory=list)   # {type, text, ...}
    entities: list[Entity] = field(default_factory=list)
    deltas: list[Delta] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)
    panel: str | None = None        # 命中触发词时：要渲染的面板 key
    system_note: str | None = None  # 引擎提示（存档成功/面板未实现等）
    fx: dict | None = None
    proto_ver: int = 1

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "TurnPayload":
        d = json.loads(s)
        d["entities"] = [Entity(**e) for e in d.get("entities", [])]
        d["deltas"] = [Delta(**x) for x in d.get("deltas", [])]
        d["choices"] = [Choice(**c) for c in d.get("choices", [])]
        return cls(**d)


def narration_payload(turn_idx: int, text: str) -> TurnPayload:
    """便捷构造：整段叙事作为单个旁白块（直通模式用）。"""
    return TurnPayload(turn_idx=turn_idx, narrative=[{"type": "narration", "text": text}])


def note_payload(turn_idx: int, note: str, panel: str | None = None) -> TurnPayload:
    """便捷构造：引擎提示（触发词/存档等，不经过 LLM）。"""
    return TurnPayload(turn_idx=turn_idx, system_note=note, panel=panel)
