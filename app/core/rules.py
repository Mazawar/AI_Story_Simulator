"""引擎模式数值状态机与裁决执行（DESIGN.md §9）。

题材无关：数值白名单由剧本包 schema 声明的 resources 驱动——
修仙包（境界轴+灵石）、末日包（生命）、武侠包（生命）共用同一状态机；
LLM 只能对声明过的资源申请增减，引擎负责边界与节奏。

NumericState 是玩家状态的唯一权威，序列化存 playthroughs.player_json。
"""

from __future__ import annotations

import re

# 防刷子：同一 (ref + 理由关键词) 连续命中的衰减
_DECAY_START = 3
_DECAY_FACTOR = 0.5
# 单笔钳制与滚窗限流（仅作用于 kind=currency 的资源，打破"每轮捡钱"循环）
_MAX_CURRENCY_GAIN = 30
_MAX_ITEM_COUNT = 9
_GAIN_WINDOW = 8
_MAX_GAINS_IN_WINDOW = 2
_MAX_PROGRESS_GAINS_IN_WINDOW = 3

_REASON_STRIP_RE = re.compile(r"[\d０-９\s.,，。;；]+")


def _reason_key(reason: str) -> str:
    return _REASON_STRIP_RE.sub("", reason)[:24]


class NumericState:
    """玩家数值状态。state_dict 可整体 round-trip（存档/续玩）。

    schema.realms 非空 → 境界轴（境界/修为/寿元）启用；
    schema.resources → 自由资源（生命/灵石/物资…），delta 白名单来源。
    """

    def __init__(self, schema: dict, state: dict | None = None):
        self.schema = schema
        self.realms = schema.get("realms") or []
        self.resources = schema.get("resources") or [
            {"ref": "生命", "init": 100, "max": 100, "kind": "vital"}]
        s = state or {}

        self.attrs: dict[str, float] = dict(s.get("attrs", {}))
        for res in self.resources:                       # 新资源补默认值
            self.attrs.setdefault(res["ref"], float(res.get("init", 0)))

        self.realm_index: int = s.get("realm_index", 0)
        self.stage_index: int = s.get("stage_index", 0)
        self.age: float = s.get("age", 0.0)              # 已活岁数（有寿元轴才有意义）
        self.progress: float = s.get("progress", 0.0)    # 修为进度（有境界轴才有意义）
        self.spirit: str = s.get("spirit", "")
        self.location: str = s.get("location", "")
        self.name: str = s.get("name", "")
        self.inventory: list[dict] = s.get("inventory", [])
        self.flags: dict[str, bool] = s.get("flags", {})
        self.decay_counter: dict[str, int] = s.get("decay_counter", {})
        self.gain_log: list[str] = s.get("gain_log", [])
        self.extra: dict = s.get("extra", {})

        # 兼容旧字段：灵石（修仙包 currency 资源）
        self._currency_ref = next(
            (r["ref"] for r in self.resources if r.get("kind") == "currency"), None)

    # ---- 兼容属性 ---------------------------------------------------------------

    @property
    def stones(self) -> float:
        return self.attrs.get(self._currency_ref, 0) if self._currency_ref else 0

    # ---- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "attrs": self.attrs,
            "realm_index": self.realm_index, "stage_index": self.stage_index,
            "age": self.age, "progress": self.progress,
            "spirit": self.spirit, "location": self.location, "name": self.name,
            "inventory": self.inventory, "flags": self.flags,
            "decay_counter": self.decay_counter, "gain_log": self.gain_log[-_GAIN_WINDOW:],
            "extra": self.extra,
        }

    @classmethod
    def new_game(cls, schema: dict, *, spirit: str = "", location: str = "",
                 name: str = "", age: float | None = None,
                 starting_stones: int = 0) -> "NumericState":
        st = cls(schema)
        st.spirit = spirit
        st.location = location
        st.name = name
        if st.realms:
            st.age = age if age is not None else 17.0
        if starting_stones and st._currency_ref:
            st.attrs[st._currency_ref] = float(starting_stones)
        return st

    # ---- 展示 -----------------------------------------------------------------

    @property
    def has_realm_axis(self) -> bool:
        return bool(self.realms)

    @property
    def realm_name(self) -> str:
        if not self.realms:
            return ""
        r = self.realms[self.realm_index]
        if isinstance(r["stages"], list):
            return f"{r['name']}{r['stages'][self.stage_index]}期"
        return f"{r['name']}{self.stage_index + 1}层"

    @property
    def realm_short(self) -> str:
        return self.realms[self.realm_index]["name"] if self.realms else ""

    @property
    def lifespan_cap(self) -> int:
        return (self.schema.get("lifespan_caps") or {}).get(self.realm_short, 100)

    @property
    def lifespan_left(self) -> float:
        if not self.realms:
            return 0.0
        return max(0.0, self.lifespan_cap - self.age)

    def _fmt_value(self, ref: str) -> str:
        v = self.attrs.get(ref, 0)
        return f"{int(v)}" if float(v).is_integer() else f"{v:g}"

    def broadcast(self, changes: list[dict] | None = None) -> list[dict]:
        """播报条字段（引擎真数据）：全部声明资源 + 地点（境界变化显示 旧→新）。"""
        changes = changes or []
        changed_refs = {c["ref"] for c in changes}
        fields: list[dict] = []
        if self.realms:
            old = self._fmt_old(changes, "境界")
            fields.append({"label": "境界",
                           "value": self.realm_name + (f"（{old}→）" if old and "境界" in changed_refs else "")})
            fields.append({"label": "寿元", "value": f"{self.lifespan_left:.0f}年"})
        for res in self.resources:
            ref = res["ref"]
            old = self._fmt_old(changes, ref)
            suffix = f"（{old}→）" if old and res.get("kind") == "currency" else ""
            fields.append({"label": ref, "value": self._fmt_value(ref) + suffix})
        fields.append({"label": "地点", "value": self.location or "—"})
        return fields

    @staticmethod
    def _fmt_old(changes, ref) -> str:
        for c in changes:
            if c["ref"] == ref:
                return str(c.get("old", ""))
        return ""

    def panel_cultivator(self) -> dict:
        """状态面板数据（触发词触发，引擎真数据）。"""
        data: dict = {"名字": self.name or "（未定）"}
        if self.realms:
            data["境界"] = self.realm_name
            data["寿元"] = f"{self.lifespan_left:.0f}年"
            data["修为"] = f"{self.progress:.0f}/100"
            if self.spirit:
                data["灵根"] = self.spirit
        for res in self.resources:
            data[res["ref"]] = self._fmt_value(res["ref"]) + \
                (f"/{res['max']}" if res.get("max") else "")
        data["地点"] = self.location or "—"
        data["物品"] = [f"{i['name']}×{i.get('count', 1)}" for i in self.inventory][:5] or ["无"]
        return data

    # ---- 境界与时间流（境界轴存在时） ----------------------------------------------

    def _advance_stage(self) -> bool:
        r = self.realms[self.realm_index]
        stages = r["stages"]
        limit = stages if isinstance(stages, int) else len(stages)
        if self.stage_index + 1 < limit:
            self.stage_index += 1
            self.age += self.schema.get("layer_cost_years", 1) or 1
            self.progress = 0.0
            return True
        return False

    def realm_breakthrough(self) -> bool:
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
            elif "location" in eff:
                loc = str(eff.get("location", "")).strip()[:12]
                if not loc:
                    rejected.append({"effect": eff, "why": "地点为空"})
                else:
                    old = self.location
                    self.location = loc
                    applied.append({"ref": "地点", "op": "=", "v": 1,
                                    "reason": f"{old or '未知'} → {loc}"})
            elif "flag" in eff:
                self.flags[str(eff["flag"])] = str(eff.get("value", "true")).lower() != "false"
                applied.append({"ref": f"flag:{eff['flag']}", "op": "=", "v": 1,
                                "reason": eff.get("note", "")})
            elif "anchor" in eff:
                applied.append({"ref": f"anchor:{eff['anchor']}", "op": "=", "v": 1,
                                "reason": "锚点触发请求"})
            else:
                rejected.append({"effect": eff, "why": "未知指令类型"})
        return applied, rejected

    def _match_resource(self, ref: str) -> dict | None:
        for res in self.resources:
            if res["ref"] in ref or ref in res["ref"]:
                return res
        return None

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

        res = self._match_resource(ref)
        if res is not None:
            self._apply_resource_delta(res, op, v, reason, eff, applied, rejected)
            return

        if ref == "修为" and self.realms:
            if op == "+" and v > 0:
                if self.gain_log[-_GAIN_WINDOW:].count("修为") >= _MAX_PROGRESS_GAINS_IN_WINDOW:
                    rejected.append({"effect": eff, "why": "心浮气躁，一时再难寸进"})
                    return
                self.gain_log.append("修为")
            delta = v if op == "+" else -v
            new_progress = self.progress + delta
            if new_progress >= 100.0 and self._advance_stage():
                self.progress = max(0.0, new_progress - 100.0)
                applied.append({"ref": "境界", "op": "+", "v": 1,
                                "reason": "修为圆满，推进一层"})
            else:
                self.progress = max(0.0, min(100.0, new_progress))
            applied.append({"ref": "修为", "op": op, "v": v, "reason": reason})
            return

        rejected.append({"effect": eff, "why": f"未知数值项：{ref}"})

    def _apply_resource_delta(self, res: dict, op: str, v: float, reason: str,
                              eff: dict, applied: list, rejected: list) -> None:
        ref, kind = res["ref"], res.get("kind", "vital")
        cur = self.attrs.get(ref, float(res.get("init", 0)))
        maximum = res.get("max")

        if kind == "currency" and op == "+" and v > 0:
            recent = self.gain_log[-_GAIN_WINDOW:]
            if recent.count(ref) >= _MAX_GAINS_IN_WINDOW:
                rejected.append({"effect": eff,
                                 "why": f"近期{ref}进项已足，新的收获须待机缘"})
                return
            if v > _MAX_CURRENCY_GAIN:
                reason += f"（机缘过大，被世界规则压缩至{_MAX_CURRENCY_GAIN}）"
                v = float(_MAX_CURRENCY_GAIN)
            self.gain_log.append(ref)
            key = _reason_key(reason)
            n = self.decay_counter.get(key, 0)
            self.decay_counter[key] = n + 1
            if n + 1 > _DECAY_START:
                v = round(v * (_DECAY_FACTOR ** (n + 1 - _DECAY_START)), 2)
                reason += "（重复收益递减）"
        elif kind == "currency":
            k2 = _reason_key("支出" + reason)
            self.decay_counter[k2] = self.decay_counter.get(k2, 0) + 1

        delta = v if op == "+" else -v
        new_val = cur + delta
        if new_val < 0:
            rejected.append({"effect": eff, "why": f"{ref}不足（当前 {cur:g}）"})
            return
        if maximum is not None:
            new_val = min(new_val, float(maximum))
        self.attrs[ref] = new_val
        applied.append({"ref": ref, "op": op, "v": v, "reason": reason, "old": f"{cur:g}"})

    def _apply_item(self, eff: dict, applied: list, rejected: list) -> None:
        name, action = str(eff["item"]), str(eff.get("action", "add"))
        note = str(eff.get("note", ""))
        m = re.search(r"([+-－—]|±)\s*([0-9０-９]+)", note)
        count = abs(int(m.group(2).translate(str.maketrans("０１２３４５６７８９", "0123456789")))) if m else 1
        if action == "add":
            count = min(count, _MAX_ITEM_COUNT)
            entry = next((i for i in self.inventory if i["name"] == name), None)
            if entry:
                entry["count"] = entry.get("count", 1) + count
            else:
                self.inventory.append({"name": name, "count": count, "note": note})
            applied.append({"ref": f"item:{name}", "op": "+", "v": count, "reason": note})
        elif action == "remove":
            entry = next((i for i in self.inventory if i["name"] == name), None)
            if not entry:
                rejected.append({"effect": eff, "why": f"未持有：{name}"})
                return
            entry["count"] = entry.get("count", 1) - count
            if entry["count"] <= 0:
                self.inventory.remove(entry)
            applied.append({"ref": f"item:{name}", "op": "-", "v": count, "reason": note})
        else:
            rejected.append({"effect": eff, "why": f"未知物品动作：{action}"})
