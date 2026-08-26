"""直通模式引擎（阶段 0 闭环，DESIGN.md §6.3）。

整包剧本作为系统提示词直灌；本引擎提供：
- 触发词拦截（命中不进 LLM）：存档/读取/面板类；
- 回合循环：上下文 → LLM → TurnPayload 落库；
- 存档：整体状态 + 消息历史快照。

引擎模式（数值/面板/锚点由代码执行）在阶段 2 落地。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..ai.backend import LLMBackend
from ..ai.prompts.wrapper import DIRECT_WRAPPER
from ..db import dao
from ..db.database import Database
from ..pack.models import Pack
from ..render.contract import TurnPayload, note_payload
from ..render.narrative_parser import parse_narrative


def _wrapped_system_prompt(pack: Pack) -> str:
    """输出合同 + 剧本包全文（合同为 Python 常量，随 PYZ 打包，冻结安全）。"""
    return DIRECT_WRAPPER + "\n" + pack.system_prompt()

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


def rebuild_history(payload: dict) -> str:
    """从 TurnPayload 近似重建 assistant 原文（无存档快照时的续玩回退）。"""
    parts: list[str] = []
    for block in payload.get("narrative", []):
        kind = block.get("type")
        if kind == "narration":
            parts.append(block.get("text", ""))
        elif kind == "dialogue":
            parts.append("> **%s：** %s" % (block.get("speaker", "?"), block.get("text", "")))
        elif kind == "broadcast":
            fields = "｜".join(
                "%s %s" % (f.get("label", ""), f.get("value", ""))
                for f in block.get("fields", [])
            )
            parts.append("【%s】" % fields)
        elif kind == "choices":
            parts.extend("【%s】%s" % (o.get("id", "?"), o.get("text", ""))
                         for o in block.get("options", []))
    return "\n".join(p for p in parts if p)


class DirectEngine:
    def __init__(self, db: Database, backend: LLMBackend, pack: Pack,
                 playthrough_id: int, history: list[dict] | None = None,
                 n_ctx: int = 32768):
        self.db = db
        self.backend = backend
        self.pack = pack
        self.playthrough_id = playthrough_id
        self.n_ctx = n_ctx
        # 消息历史：[system(输出合同+剧本包+玩家身世)] + 已发生的 [user/assistant ...]
        self.messages: list[dict] = [{"role": "system", "content": _wrapped_system_prompt(pack)}]
        if history:
            self.messages.extend(history)
        self._brief_applied = any(
            m["role"] == "user" and str(m["content"]).startswith("【人物已定】")
            for m in self.messages[1:]
        )
        with db.locked() as conn:
            self.turn_idx = int(conn.execute(
                "SELECT COALESCE(MAX(idx), 0) FROM turns WHERE playthrough_id = ?",
                (playthrough_id,),
            ).fetchone()[0])

    # ---- 上下文预算 -----------------------------------------------------------

    def _trim_history(self) -> None:
        """超长会话滑窗：system + 历史超过窗口 75% 时丢弃最旧回合。

        直通模式历史无上限增长，必须裁剪；裁剪会破坏 KV 前缀缓存
        （触发一次全量重推演），因此只在逼近上限时才发生。
        """
        budget_chars = int(self.n_ctx * 0.75 * 1.4)
        def total_chars() -> int:
            return sum(len(m["content"]) for m in self.messages)
        # 每轮丢一对 user/assistant，直到回到预算内（至少保留最近 4 条消息）
        while (total_chars() > budget_chars and len(self.messages) > 5):
            # messages[0] 是 system，从最旧的非 system 消息开始丢
            self.messages.pop(1)
            self.messages.pop(1)

    # ---- 回合 ---------------------------------------------------------------

    def stream_handle(self, user_input: str) -> Iterator[tuple[str, object]]:
        """流式处理一个回合。

        yield ("delta", 文本片段) 逐段输出；
        yield ("note"|"turn", TurnPayload) 为该回合最终产物（已落库）。
        """
        stripped = user_input.strip().strip("「」")
        if stripped in TRIGGER_WORDS:
            action = TRIGGER_WORDS[stripped]
            if action == "save":
                yield ("note", self._save("autosave"))
            elif action == "load":
                yield ("note", self._load("autosave"))
            else:
                yield ("note", self._emit(note_payload(
                    self.turn_idx + 1, _PANEL_NOTES.get(action, action), panel=action,
                ), player_input=stripped))
            return

        self.messages.append({"role": "user", "content": user_input})
        # 向导完成的人物设定固化进系统提示（历史中保留原消息，续玩安全）
        if user_input.startswith("【人物已定】") and not self._brief_applied:
            self.messages[0]["content"] += (
                "\n\n【玩家身世设定（已确认，据此展开剧情，禁止再次询问）】" + user_input
            )
            self._brief_applied = True
        self._trim_history()
        pieces: list[str] = []
        try:
            for piece in self.backend.stream(self.messages, max_tokens=1024):
                pieces.append(piece)
                yield ("delta", piece)
        except Exception:
            self.messages.pop()  # 失败回合不污染历史
            raise
        reply = "".join(pieces)
        self.messages.append({"role": "assistant", "content": reply})
        yield ("turn", self._emit(
            TurnPayload(turn_idx=self.turn_idx + 1, narrative=parse_narrative(reply)),
            player_input=user_input,
        ))

    def handle(self, user_input: str) -> TurnPayload:
        payload: TurnPayload | None = None
        for kind, data in self.stream_handle(user_input):
            if kind in ("note", "turn"):
                payload = data
        return payload

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
