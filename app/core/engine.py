"""直通模式引擎（阶段 0 闭环，DESIGN.md §6.3）。

整包剧本作为系统提示词直灌；本引擎提供：
- 触发词拦截（命中不进 LLM）：存档/读取/面板类；
- 回合循环：上下文 → LLM → TurnPayload 落库；
- 存档：整体状态 + 消息历史快照。

引擎模式（数值/面板/锚点由代码执行）在阶段 2 落地。
"""

from __future__ import annotations

from ..ai.backend import LLMBackend
from ..db import dao
from ..db.database import Database
from ..pack.models import Pack
from ..render.contract import TurnPayload, narration_payload, note_payload

# 直通模式默认拦截的触发词（与三个剧本包「约束」章节一致）
TRIGGER_WORDS: dict[str, str] = {
    "存档": "save",
    "读取存档": "load",
    "修士": "panel_status",
    "任务": "panel_tasks",
    "提示": "panel_hints",
    "降级面板": "panel_lite_on",
    "恢复面板": "panel_lite_off",
    "本章结束": "chapter_settle",
}

_PANEL_NOTES = {
    "panel_status": "修士面板（引擎模式功能，阶段 2 提供；当前直通模式请直接向主持人提问）",
    "panel_tasks": "任务面板（引擎模式功能，阶段 2 提供）",
    "panel_hints": "提示面板（引擎模式功能，阶段 2 提供）",
    "panel_lite_on": "面板降级（引擎模式功能，阶段 2 提供）",
    "panel_lite_off": "面板恢复（引擎模式功能，阶段 2 提供）",
    "chapter_settle": "章节结算（引擎模式功能，阶段 2 提供）",
}


class DirectEngine:
    def __init__(self, db: Database, backend: LLMBackend, pack: Pack,
                 playthrough_id: int, history: list[dict] | None = None):
        self.db = db
        self.backend = backend
        self.pack = pack
        self.playthrough_id = playthrough_id
        # 消息历史：[system] + 已发生的 [user/assistant ...]
        self.messages: list[dict] = [{"role": "system", "content": pack.system_prompt()}]
        if history:
            self.messages.extend(history)
        self.turn_idx = int(
            db.conn.execute(
                "SELECT COALESCE(MAX(idx), 0) FROM turns WHERE playthrough_id = ?",
                (playthrough_id,),
            ).fetchone()[0]
        )

    # ---- 回合 ---------------------------------------------------------------

    def handle(self, user_input: str) -> TurnPayload:
        stripped = user_input.strip().strip("「」")
        if stripped in TRIGGER_WORDS:
            action = TRIGGER_WORDS[stripped]
            if action == "save":
                return self._save("autosave")
            if action == "load":
                return self._load("autosave")
            return self._emit(note_payload(
                self.turn_idx + 1, _PANEL_NOTES.get(action, action), panel=action,
            ), player_input=stripped)

        self.messages.append({"role": "user", "content": user_input})
        try:
            reply = self.backend.generate(self.messages, max_tokens=1024)
        except Exception:
            self.messages.pop()  # 失败回合不污染历史
            raise
        self.messages.append({"role": "assistant", "content": reply})
        return self._emit(narration_payload(self.turn_idx + 1, reply), player_input=user_input)

    def _emit(self, payload: TurnPayload, player_input: str | None = None) -> TurnPayload:
        dao.plays.add_turn(self.db, self.playthrough_id, payload.turn_idx,
                           payload.to_dict(), player_input)
        self.turn_idx = payload.turn_idx
        return payload

    # ---- 存档 ---------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "mode": "direct",
            "pack_title": self.pack.title,
            "turn_idx": self.turn_idx,
            "history": self.messages[1:],   # 去掉 system（可由包重建）
        }

    def _save(self, slot: str) -> TurnPayload:
        snap = self.snapshot()
        summary = f"{self.pack.title} · 第 {self.turn_idx} 回合"
        dao.plays.write_save(self.db, self.playthrough_id, slot, summary, snap)
        return self._emit(note_payload(self.turn_idx + 1, f"已存档 [{slot}] {summary}", panel="save"),
                          player_input="存档")

    def _load(self, slot: str) -> TurnPayload:
        snap = dao.plays.load_save(self.db, self.playthrough_id, slot)
        if snap is None:
            return self._emit(note_payload(self.turn_idx + 1, f"存档槽 [{slot}] 为空", panel="load"),
                              player_input="读取存档")
        self.messages = [{"role": "system", "content": self.pack.system_prompt()}]
        self.messages.extend(snap.get("history", []))
        return self._emit(
            note_payload(self.turn_idx + 1,
                         f"已读取 [{slot}]（{snap.get('turn_idx', 0)} 回合）", panel="load"),
            player_input="读取存档",
        )
