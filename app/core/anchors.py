"""锚点引擎：条件求值 + 剧透隔离（DESIGN.md §9）。

条件 DSL（安全 JSON 子集，仅引擎求值，无外部输入拼接）：
- {"type":"turn_gte","v":N} / {"type":"turn_lte","v":N}
- {"type":"flag","k":"..."}
- {"type":"realm_gte","realm":"结丹"}
- {"type":"depends_on","key":"tl-2"}
- {"type":"anchor_effect","title_contains":"..."}   # LLM 裁决请求匹配
- {"type":"all"/"any","conds":[...]}
"""

from __future__ import annotations

from .rules import NumericState

_REALM_ORDER = ["练气", "筑基", "结丹", "元婴", "化神",
                "搬血", "洞天", "化灵", "铭纹", "列阵", "尊者"]


def eval_condition(cond: dict, state: NumericState, turn: int,
                   triggered: set[str], anchor_requests: list[str]) -> bool:
    t = cond.get("type")
    if t == "turn_gte":
        return turn >= int(cond.get("v", 0))
    if t == "turn_lte":
        return turn <= int(cond.get("v", 1 << 30))
    if t == "flag":
        return bool(state.flags.get(str(cond.get("k"))))
    if t == "realm_gte":
        want = _realm_rank(str(cond.get("realm", "")))
        return _realm_rank(state.realm_short) >= want if want else False
    if t == "depends_on":
        return str(cond.get("key")) in triggered
    if t == "anchor_effect":
        frag = str(cond.get("title_contains", ""))
        return any(frag and frag in r for r in anchor_requests)
    if t == "all":
        return all(eval_condition(c, state, turn, triggered, anchor_requests)
                   for c in cond.get("conds", []))
    if t == "any":
        return any(eval_condition(c, state, turn, triggered, anchor_requests)
                   for c in cond.get("conds", []))
    return False


def _realm_rank(name: str) -> int:
    for i, r in enumerate(_REALM_ORDER):
        if r in name:
            return i
    return -1


class AnchorEngine:
    """锚点调度：每回合求值；揭晓点未放行前绝不进入 LLM 上下文（剧透隔离）。"""

    def __init__(self, anchors: list[dict]):
        self.anchors = anchors

    def evaluate(self, state: NumericState, turn: int,
                 anchor_requests: list[str]) -> list[dict]:
        """返回本轮新触发的锚点（更新由调用方持久化到 is_triggered）。"""
        triggered_keys = {a["key"] for a in self.anchors if a.get("is_triggered")}
        fired = []
        for a in self.anchors:
            if a.get("is_triggered"):
                continue
            if eval_condition(a.get("trigger", {}), state, turn, triggered_keys,
                              anchor_requests):
                a["is_triggered"] = True
                fired.append(a)
        return fired

    def context_block(self, turn: int, limit: int = 4) -> str:
        """邻近锚点元信息（供 LLM 铺垫剧情走向）。

        只含已触发锚点的结果叙述 + 未触发时间表锚点的标题——
        揭晓点（spoiler_level=reveal）的真相文本绝不出现。
        """
        lines = []
        for a in self.anchors:
            if a.get("is_triggered") and a["kind"] == "timeline":
                lines.append(f"- 已发生：{a['title']}")
            if len(lines) >= limit:
                break
        upcoming = [a for a in self.anchors
                    if not a.get("is_triggered") and a["kind"] == "timeline"]
        for a in upcoming[:limit]:
            lines.append(f"- 世界暗流（可铺垫，禁止剧透细节）：{a['title']}")
        return "\n".join(lines)

    def released_reveals(self) -> list[dict]:
        """已放行的揭晓点（可进入剧情/上下文）。"""
        return [a for a in self.anchors
                if a.get("is_triggered") and a["kind"] == "reveal"]
