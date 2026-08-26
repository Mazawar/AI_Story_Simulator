"""引擎模式回合循环（DESIGN.md §6.3/§9）：数值/面板/锚点由代码执行，LLM 只做叙事裁决。

与 DirectEngine 的差异：
- 无整包系统提示词，每回合经上下文组装器注入 ~9k token；
- LLM 输出裁决 JSON（本地走 GBNF 强制合法）；
- 触发词（修士/任务/提示/本章结束）由引擎直接产出真数据面板，不进 LLM；
- 存档 = NumericState + 滚动摘要 + 锚点触发集，读档整体恢复。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from ..ai.backend import LLMBackend
from ..ai.context_assembler import assemble_messages, estimate_tokens, summarize_turns
from ..db import dao
from ..db.database import Database
from ..pack.anchors import parse_anchors
from ..pack.creator import parse_character_cards
from ..pack.models import Pack
from ..pack.numeric import parse_numeric_schema
from ..render.contract import TurnPayload, note_payload
from ..render.narrative_parser import parse_narrative
from .anchors import AnchorEngine
from .rules import NumericState

SUMMARY_EVERY = 10            # 每 N 回合滚动摘要一次
SETTLEMENT_KEY = "last_settle_turn"


def _panel_block(title: str, data: dict) -> dict:
    """真数据面板 → 广播块形式的渲染数据。"""
    fields = [{"label": k, "value": str(v)} for k, v in data.items()]
    return {"type": "panel", "title": title, "fields": fields}


class EngineSession:
    name = "engine"

    def __init__(self, db: Database, backend: LLMBackend, pack: Pack,
                 playthrough_id: int, *, state: NumericState | None = None,
                 rolling_summary: str = ""):
        self.db = db
        self.backend = backend
        self.pack = pack
        self.playthrough_id = playthrough_id
        self.schema = parse_numeric_schema(pack)
        self.characters = parse_character_cards(pack)
        self.anchor_engine = AnchorEngine(parse_anchors(pack))

        self.state = state or NumericState.new_game(self.schema)
        self.rolling_summary = rolling_summary
        # 恢复锚点触发集
        triggered = set(self.state.extra.get("triggered_anchors", []))
        for a in self.anchor_engine.anchors:
            if a["key"] in triggered:
                a["is_triggered"] = True

        with db.locked() as conn:
            self.turn_idx = int(conn.execute(
                "SELECT COALESCE(MAX(idx), 0) FROM turns WHERE playthrough_id = ?",
                (playthrough_id,),
            ).fetchone()[0])
        self.recent: list[dict] = self._load_recent()

    # ---- 基础 -----------------------------------------------------------------

    def _load_recent(self) -> list[dict]:
        with self.db.locked() as conn:
            rows = conn.execute(
                "SELECT player_input, turn_payload_json FROM turns"
                " WHERE playthrough_id = ? ORDER BY idx DESC LIMIT 8",
                (self.playthrough_id,),
            ).fetchall()
        turns = []
        for r in reversed(rows):
            payload = json.loads(r["turn_payload_json"])
            text = "\n".join(
                b.get("text", "") for b in payload.get("narrative", [])
                if b.get("type") in ("narration", "dialogue")
            )
            turns.append({"input": r["player_input"] or "", "text": text})
        return turns

    def _persist_state(self) -> None:
        self.state.extra["triggered_anchors"] = [
            a["key"] for a in self.anchor_engine.anchors if a.get("is_triggered")
        ]
        with self.db.locked() as conn:
            conn.execute(
                "UPDATE playthroughs SET player_json = ?, rolling_summary = ?,"
                " turn_count = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (json.dumps(self.state.to_dict(), ensure_ascii=False),
                 self.rolling_summary, self.turn_idx, self.playthrough_id),
            )
            self.db.conn.commit()

    def _emit(self, payload: TurnPayload, player_input: str | None = None,
              adjudication: dict | None = None) -> TurnPayload:
        dao.plays.add_turn(self.db, self.playthrough_id, payload.turn_idx,
                           payload.to_dict(), player_input, adjudication)
        self.turn_idx = payload.turn_idx
        narrative_text = "\n".join(
            b.get("text", "") for b in payload.narrative
            if b.get("type") in ("narration", "dialogue")
        )
        self.recent.append({"input": player_input or "", "text": narrative_text})
        self.recent = self.recent[-8:]
        return payload

    # ---- 回合循环 ----------------------------------------------------------------

    def stream_handle(self, user_input: str) -> Iterator[tuple[str, object]]:
        stripped = user_input.strip().strip("「」")

        if stripped == "存档":
            yield ("note", self._save("autosave"))
            return
        if stripped == "读取存档":
            yield ("note", self._load("autosave"))
            return
        if stripped == "修士":
            yield ("note", self._emit(TurnPayload(
                turn_idx=self.turn_idx + 1,
                narrative=[_panel_block("修士面板", self.state.panel_cultivator())],
                system_note="修士面板（引擎实时数据）", panel="cultivator",
            ), player_input=stripped))
            return
        if stripped == "任务":
            yield ("note", self._emit(note_payload(
                self.turn_idx + 1,
                "任务系统随身份线推进（阶段2后续接入；当前可关注世界动态）",
                panel="tasks"), player_input=stripped))
            return
        if stripped == "提示":
            fired = [a["title"] for a in self.anchor_engine.anchors if a.get("is_triggered")]
            hints = [f"境界 {self.state.realm_name}，修为 {self.state.progress:.0f}/100"]
            if self.state.progress >= 60:
                hints.append("修将圆满，可考虑闭关突破")
            if fired:
                hints.append("已历事件：" + "、".join(fired[-3:]))
            yield ("note", self._emit(note_payload(
                self.turn_idx + 1, "；".join(hints), panel="hints"), player_input=stripped))
            return
        if stripped == "本章结束":
            yield ("note", self._settlement())
            return
        if stripped in ("降级面板", "恢复面板"):
            yield ("note", self._emit(note_payload(
                self.turn_idx + 1, "面板密度切换（引擎模式下面板始终为真数据轻量版）",
                panel="density"), player_input=stripped))
            return

        yield from self._adjudicate(user_input)

    # ---- 裁决回合 ----------------------------------------------------------------

    def _adjudicate(self, user_input: str) -> Iterator[tuple[str, object]]:
        turn = self.turn_idx + 1
        messages = assemble_messages(
            self.pack, self.characters, self.state, self.recent,
            self.rolling_summary, self.anchor_engine.context_block(turn),
            user_input, turn,
        )
        data = self.backend.generate_json(messages, max_tokens=512, temperature=0.8)
        narrative_text = str(data.get("narrative", "")).strip() or "（这一刻，什么也没有发生。）"
        effects = data.get("effects", []) or []

        applied, _rejected = self.state.apply_effects(effects)
        anchor_requests = [str(e["anchor"]) for e in effects if isinstance(e, dict) and "anchor" in e]
        fired = self.anchor_engine.evaluate(self.state, turn, anchor_requests)
        if fired:
            applied.append({"ref": "anchor", "op": "=",
                            "v": 1, "reason": "、".join(a["title"] for a in fired)})
            # 已触发的时间表锚点结果进入滚动上下文（按标题去重）
            for a in fired:
                if a["kind"] == "timeline" and f"【{a['title']}】" not in self.rolling_summary:
                    self.rolling_summary = (self.rolling_summary + "；" if self.rolling_summary else "") \
                        + f"事件【{a['title']}】已发生"

        blocks = parse_narrative(narrative_text)
        blocks.append({"type": "broadcast", "fields": self.state.broadcast(applied)})
        entities = [
            {"ref": f"character:{c['name']}", "surface": c["name"]}
            for c in self.characters if c["name"] in narrative_text
        ][:8]

        payload = TurnPayload(
            turn_idx=turn,
            narrative=blocks,
            entities=entities,
            deltas=[{"ref": d["ref"], "op": d["op"], "v": d["v"], "reason": d["reason"]}
                    for d in applied],
            choices=self._engine_choices(),
            fx={"level": "major"} if any(d["ref"] == "境界" for d in applied) else None,
        )
        self._emit(payload, player_input=user_input,
                   adjudication={"effects": effects, "applied": applied})

        if turn % SUMMARY_EVERY == 0:
            self.rolling_summary = summarize_turns(
                self.backend, self.recent, self.rolling_summary)
        self._persist_state()
        yield ("turn", payload)

    def _engine_choices(self) -> list[dict]:
        """引擎生成的四向选项（确定性，基于当前状态）。"""
        opts = []
        opts.append("闭关修炼，打磨修为" if self.state.progress < 60 else "闭关冲击瓶颈")
        opts.append(f"在{self.state.location or '附近'}走动探察")
        opts.append("寻人打听消息")
        opts.append("清点行囊，谋划下一步")
        return [{"id": chr(65 + i), "text": t, "tags": [], "hint": ""}
                for i, t in enumerate(opts)]

    # ---- 章节结算 ----------------------------------------------------------------

    def _settlement(self) -> TurnPayload:
        last = int(self.state.extra.get(SETTLEMENT_KEY, 0))
        with self.db.locked() as conn:
            rows = conn.execute(
                "SELECT idx, player_input, turn_payload_json FROM turns"
                " WHERE playthrough_id = ? AND idx > ? ORDER BY idx",
                (self.playthrough_id, last),
            ).fetchall()
        deltas: dict[str, float] = {}
        for r in rows:
            payload = json.loads(r["turn_payload_json"])
            for d in payload.get("deltas", []):
                if isinstance(d, dict) and d.get("ref") in ("灵石", "修为"):
                    v = float(d.get("v", 0))
                    deltas[d["ref"]] = deltas.get(d["ref"], 0) + (v if d.get("op") == "+" else -v)
        self.state.extra[SETTLEMENT_KEY] = self.turn_idx
        self._persist_state()
        lines = [
            f"本章共 {len(rows)} 回合。",
            "，".join(f"{k}{'增' if v >= 0 else '减'}{abs(v):g}" for k, v in deltas.items()) or "数值平稳。",
            f"当前：{self.state.realm_name}，灵石{self.state.stones:g}，寿元余{self.state.lifespan_left:.0f}年。",
        ]
        return self._emit(TurnPayload(
            turn_idx=self.turn_idx + 1,
            narrative=[{"type": "panel", "title": "本章结算",
                        "fields": [{"label": f"第{last + 1}-{self.turn_idx}回合", "value": s} for s in lines]}],
            system_note="本章已结算", panel="settlement",
        ), player_input="本章结束")

    # ---- 存档 ----------------------------------------------------------------

    def snapshot(self) -> dict:
        self.state.extra["triggered_anchors"] = [
            a["key"] for a in self.anchor_engine.anchors if a.get("is_triggered")
        ]
        return {
            "mode": "engine", "pack_title": self.pack.title,
            "turn_idx": self.turn_idx, "state": self.state.to_dict(),
            "rolling_summary": self.rolling_summary,
        }

    def _save(self, slot: str):
        snap = self.snapshot()
        dao.plays.write_save(self.db, self.playthrough_id, slot,
                             f"{self.pack.title} · 第{self.turn_idx}回合 · {self.state.realm_name}", snap)
        return self._emit(note_payload(
            self.turn_idx + 1,
            f"已存档 [{slot}]：{self.state.realm_name}｜灵石{self.state.stones:g}｜"
            f"寿元余{self.state.lifespan_left:.0f}年", panel="save"), player_input="存档")

    def _load(self, slot: str):
        snap = dao.plays.load_save(self.db, self.playthrough_id, slot)
        if snap is None:
            return self._emit(note_payload(self.turn_idx + 1, f"存档槽 [{slot}] 为空", panel="load"),
                              player_input="读取存档")
        self.state = NumericState(self.schema, snap.get("state", {}))
        self.rolling_summary = snap.get("rolling_summary", "")
        triggered = set(self.state.extra.get("triggered_anchors", []))
        for a in self.anchor_engine.anchors:
            a["is_triggered"] = a["key"] in triggered
        return self._emit(note_payload(
            self.turn_idx + 1,
            f"已读取 [{slot}]：{self.state.realm_name}，继续你的命运", panel="load"),
            player_input="读取存档")
