"""引擎模式数值状态机与裁决执行（DESIGN.md §9）。

NumericState：玩家状态的唯一权威，序列化存 playthroughs.player_json。
apply_effects：LLM 裁决的白名单执行器——只认 delta/item/flag/anchor 四类指令，
边界校验（寿元禁改、灵石非负）、防刷子衰减（同来源收益递减）、
境界时间流（修为满→升层扣年，大境界突破扣年+寿元上限跃升）。
"""

from __future__ import annotations

import re

# 防刷子：同一 (ref + 理由关键词) 连续命中的衰减
_DECAY_START = 3          # 第 3 次起开始衰减
_DECAY_FACTOR = 0.5

_REASON_STRIP_RE = re.compile(r"[\d０-９\s.,，。;；]+")

_CURRENCY_REFS = ("灵石", "灵石(下品)", "下品灵石")


def _reason_key(reason: str) -> str:
    """理由归一化（去数字/空白）作为防刷子的来源指纹。"""
    return _REASON_STRIP_RE.sub("", reason)[:24]


class NumericState:
    """玩家数值状态。state_dict 可整体 round-trip（存档/续玩）。"""

    def __init__(self, schema: dict, state: dict | None = None):
        self.schema = schema
        self.realms = schema["realms"]
        s = state or {}
        self.realm_index: int = s.get("realm_index", 0)
        self.stage_index: int = s.get("stage_index", 0)        # 层或初中后期下标
        self.age: float = s.get("age", 17.0)                   # 已活岁数（寿元流逝）
        self.stones: int = s.get("stones", 0)                  # 下品灵石
        self.progress: float = s.get("progress", 0.0)          # 修为进度（0-100）
        self.spirit: str = s.get("spirit", "")                 # 灵根
        self.location: str = s.get("location", "")
        self.name: str = s.get("name", "")
        self.inventory: list[dict] = s.get("inventory", [])    # [{name,count,note}]
        self.flags: dict[str, bool] = s.get("flags", {})
        self.decay_counter: dict[str, int] = s.get("decay_counter", {})
        self.extra: dict = s.get("extra", {})

    # ---- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "realm_index": self.realm_index, "stage_index": self.stage_index,
            "age": self.age, "stones": self.stones, "progress": self.progress,
            "spirit": self.spirit, "location": self.location, "name": self.name,
            "inventory": self.inventory, "flags": self.flags,
            "decay_counter": self.decay_counter, "extra": self.extra,
        }

    @classmethod
    def new_game(cls, schema: dict, *, spirit: str = "", location: str = "",
                 name: str = "", age: float | None = None,
                 starting_stones: int = 0) -> "NumericState":
        st = cls(schema)
        st.spirit = spirit
        st.location = location
        st.name = name
        st.age = age if age is not None else 17.0
        st.stones = starting_stones
        return st

    # ---- 展示 -----------------------------------------------------------------

    @property
    def realm_name(self) -> str:
        r = self.realms[self.realm_index]
        if isinstance(r["stages"], list):
            return f"{r['name']}{r['stages'][self.stage_index]}期"
        return f"{r['name']}{self.stage_index + 1}层"

    @property
    def realm_short(self) -> str:
        return self.realms[self.realm_index]["name"]

    @property
    def lifespan_cap(self) -> int:
        caps = self.schema.get("lifespan_caps", {})
        return caps.get(self.realm_short, 100)

    @property
    def lifespan_left(self) -> float:
        return max(0.0, self.lifespan_cap - self.age)

    def broadcast(self, changes: list[dict] | None = None) -> list[dict]:
        """播报条字段（引擎真数据；变化项显示 旧→新）。"""
        changed_refs = {c["ref"] for c in (changes or [])}
        fields = [
            {"label": "境界",
             "value": self.realm_name + (f"（{self._fmt_old(changes, '境界')}→）" if "境界" in changed_refs else "")},
            {"label": "寿元", "value": f"{self.lifespan_left:.0f}年"},
            {"label": "灵石", "value": f"{self.stones:g}块"},
            {"label": "地点", "value": self.location or "—"},
        ]
        return fields

    @staticmethod
    def _fmt_old(changes, ref) -> str:
        for c in changes:
            if c["ref"] == ref:
                return str(c.get("old", ""))
        return ""

    def panel_cultivator(self) -> dict:
        """修士面板数据（触发词「修士」由引擎直接产出）。"""
        return {
            "名字": self.name or "（未定）", "境界": self.realm_name,
            "灵根": self.spirit or "—", "寿元": f"{self.lifespan_left:.0f}年",
            "灵石": f"{self.stones:g}块", "地点": self.location or "—",
            "修为": f"{self.progress:.0f}/100",
            "物品": [f"{i['name']}×{i.get('count', 1)}" for i in self.inventory][:5] or ["无"],
        }

    # ---- 境界与时间流 ------------------------------------------------------------

    def _advance_stage(self) -> bool:
        """修为满 → 升一层（练气系）或推进初中后期；大境界突破由锚点/事件驱动。"""
        r = self.realms[self.realm_index]
        stages = r["stages"]
        limit = stages if isinstance(stages, int) else len(stages)
        if self.stage_index + 1 < limit:
            self.stage_index += 1
            self.age += self.schema.get("layer_cost_years", 1)
            self.progress = 0.0
            return True
        return False

    def realm_breakthrough(self) -> bool:
        """大境界突破：扣年 + 寿元上限跃升 + 修为清零（须由锚点/事件触发）。"""
        if self.realm_index + 1 >= len(self.realms):
            return False
        self.realm_index += 1
        self.stage_index = 0
        self.progress = 0.0
        costs = self.schema.get("realm_breakthrough_cost_years", {})
        self.age += costs.get(self.realm_short, 10)
        return True

    # ---- 裁决执行 ----------------------------------------------------------------

    def apply_effects(self, effects: list[dict]) -> tuple[list[dict], list[dict]]:
        """执行白名单裁决。返回 (已应用的 deltas, 被拒指令及原因)。"""
        applied: list[dict] = []
        rejected: list[dict] = []
        for eff in effects or []:
            if not isinstance(eff, dict):
                rejected.append({"effect": eff, "why": "非法结构"})
                continue
            if "ref" in eff and "op" in eff and "v" in eff:
                self._apply_delta(eff, applied, rejected)
            elif "item" in eff:
                self._apply_item(eff, applied, rejected)
            elif "flag" in eff:
                self.flags[str(eff["flag"])] = str(eff.get("value", "true")).lower() != "false"
                applied.append({"ref": f"flag:{eff['flag']}", "op": "=", "v": 1,
                                "reason": eff.get("note", "")})
            elif "anchor" in eff:
                # 锚点触发请求：记录待 M3 锚点系统求值，这里透传
                applied.append({"ref": f"anchor:{eff['anchor']}", "op": "=", "v": 1,
                                "reason": "锚点触发请求"})
            else:
                rejected.append({"effect": eff, "why": "未知指令类型"})
        return applied, rejected

    def _apply_delta(self, eff: dict, applied: list, rejected: list) -> None:
        ref, op, v = str(eff["ref"]), str(eff["op"]), eff["v"]
        try:
            v = float(v)
        except (TypeError, ValueError):
            rejected.append({"effect": eff, "why": "数值非法"})
            return
        reason = str(eff.get("reason", ""))

        if "寿元" in ref or "寿命" in ref:
            rejected.append({"effect": eff, "why": "寿元由境界与时间事件驱动，禁止直接修改"})
            return

        if "修为" in ref:
            delta = v if op == "+" else -v
            new_progress = self.progress + delta
            if new_progress >= 100.0 and self._advance_stage():
                self.progress = max(0.0, new_progress - 100.0)   # 溢出余量保留
                applied.append({"ref": "境界", "op": "+", "v": 1, "reason": "修为圆满，推进一层"})
            else:
                self.progress = max(0.0, min(100.0, new_progress))
            applied.append({"ref": "修为", "op": op, "v": v, "reason": reason})
            return

        if any(c in ref for c in _CURRENCY_REFS):
            # 防刷子衰减
            key = _reason_key(reason)
            if key and v > 0 and op == "+":
                n = self.decay_counter.get(key, 0)
                self.decay_counter[key] = n + 1
                if n + 1 > _DECAY_START:
                    factor = _DECAY_FACTOR ** (n + 1 - _DECAY_START)
                    v = round(v * factor, 2)
                    reason += "（重复收益递减）"
            else:
                # 支出/非常规来源也计数，防止"买卖倒腾"刷差价
                k2 = _reason_key("支出" + reason)
                self.decay_counter[k2] = self.decay_counter.get(k2, 0) + 1
            delta = v if op == "+" else -v
            if self.stones + delta < 0:
                rejected.append({"effect": eff, "why": f"灵石不足（当前 {self.stones:g}）"})
                return
            self.stones = self.stones + delta                # 保持浮点，展示层取整
            applied.append({"ref": "灵石", "op": op, "v": v, "reason": reason})
            return

        rejected.append({"effect": eff, "why": f"未知数值项：{ref}"})

    def _apply_item(self, eff: dict, applied: list, rejected: list) -> None:
        name, action = str(eff["item"]), str(eff.get("action", "add"))
        note = str(eff.get("note", ""))
        # note 里的数量提示（"数量 +2"）作为数量依据，缺省 ±1
        m = re.search(r"([+-－—]|±)\s*([0-9０-９]+)", note)
        count = abs(int(m.group(2).translate(str.maketrans("０１２３４５６７８９", "0123456789")))) if m else 1
        entry = next((i for i in self.inventory if i["name"] == name), None)
        if action == "add":
            if entry:
                entry["count"] = entry.get("count", 1) + count
            else:
                self.inventory.append({"name": name, "count": count, "note": note})
            applied.append({"ref": f"item:{name}", "op": "+", "v": count, "reason": note})
        elif action == "remove":
            if not entry:
                rejected.append({"effect": eff, "why": f"未持有：{name}"})
                return
            entry["count"] = entry.get("count", 1) - count
            if entry["count"] <= 0:
                self.inventory.remove(entry)
            applied.append({"ref": f"item:{name}", "op": "-", "v": count, "reason": note})
        else:
            rejected.append({"effect": eff, "why": f"未知物品动作：{action}"})
