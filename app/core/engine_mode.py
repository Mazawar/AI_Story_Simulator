"""引擎模式回合循环（DESIGN.md §6.3/§9）：数值/面板/锚点由代码执行，LLM 只做叙事裁决。

与 DirectEngine 的差异：
- 无整包系统提示词，每回合经上下文组装器注入 ~9k token；
- LLM 输出裁决 JSON（本地走 GBNF 强制合法）；
- 触发词（修士/任务/提示/本章结束）由引擎直接产出真数据面板，不进 LLM；
- 存档 = NumericState + 滚动摘要 + 锚点触发集，读档整体恢复。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from .. import config
from ..ai.backend import LLMBackend
from ..ai.context_assembler import assemble_messages, estimate_tokens, summarize_turns
from ..db import dao
from ..db.database import Database
from ..pack.anchors import parse_anchors, parse_random_events, parse_world_materials
from ..pack.creator import parse_character_cards
from ..pack.models import Pack
from ..pack.numeric import parse_numeric_schema
from ..pack.profile import build_pack_profile
from ..render.contract import TurnPayload, note_payload
from ..render.narrative_parser import parse_narrative
from .anchors import AnchorEngine
from .rules import NumericState

SUMMARY_EVERY = 10            # 每 N 回合滚动摘要一次
SETTLEMENT_KEY = "last_settle_turn"

log = logging.getLogger("story.adjudicate")

# 身份关键词 → 剧本包身份线名（凡人包四选一）
_IDENTITY_KEYWORDS = ("凡人", "散修", "宗门弟子", "家族子弟")

# 剧情停滞判定：连续 N 轮无任何新变化 → 引擎强制注入随机事件
STALL_AFTER_TURNS = 2


def _panel_block(title: str, data: dict) -> dict:
    """真数据面板 → 广播块形式的渲染数据。"""
    fields = [{"label": k, "value": str(v)} for k, v in data.items()]
    return {"type": "panel", "title": title, "fields": fields}


class EngineSession:
    name = "engine"

    def __init__(self, db: Database, backend: LLMBackend, pack: Pack,
                 playthrough_id: int, *, state: NumericState | None = None,
                 rolling_summary: str = "", schema: dict | None = None):
        self.db = db
        self.backend = backend
        self.pack = pack
        self.playthrough_id = playthrough_id
        # 数值 schema 三级来源：
        #   1) 显式传入（resume 时从 storys.metadata_json 读 AI 生成的 PackProfile）
        #   2) storys.metadata_json 里已持久化的 profile
        #   3) 确定性解析兜底（parse_numeric_schema → generic），
        #      同时后台跑 AI 通读生成 profile，写库后下一局生效
        self.story_id = self._resolve_story_id()
        self.schema = schema or self._load_persisted_profile() or parse_numeric_schema(pack)
        self.characters = parse_character_cards(pack)
        self.anchor_engine = AnchorEngine(parse_anchors(pack))

        self.state = state or NumericState.new_game(self.schema)
        self.rolling_summary = rolling_summary
        # 开局地点：AI 配置提供（剧本开局场景）；resume 传入的旧状态同样补齐。
        # 推演中场景变化由模型 location 指令更新。
        if not self.state.location and self.schema.get("starting_location"):
            self.state.location = self.schema["starting_location"]
        # 世界素材：全包 bullet 通用提取（事件池/NPC/探索条目通吃），大包用于
        # "世界将发生之事"清单与停滞注入；小包全文注入时素材已在剧本原文里
        self.random_events = parse_random_events(pack)
        self.materials = parse_world_materials(pack) or [
            {"group": "事件", "title": e["title"], "desc": e["desc"]}
            for e in self.random_events]
        # AI 生成的面板定义（profile.panels）；确定性兜底时为空
        self.profile_panels: list[dict] = self.schema.get("panels") or []
        self._profile_scheduled = False
        # 剧情主线（AI 从剧本提炼的节拍表）：系统按节拍推动剧情，玩家扮演角色
        self.storyline: list[dict] = self.schema.get("storyline") or []
        # 旧版 AI 配置（缺主线/地点表等新字段）→ 后台重新生成覆盖
        if (self.schema.get("source") == "profile"
                and ("starting_location" not in self.schema
                     or "storyline" not in self.schema
                     or "locations" not in self.schema)):
            self._schedule_profile_generation()
            self._profile_scheduled = True
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
        # 状态面板触发词：AI 配置的题材词（猎人面板/状态/生存…）+「修士」兼容
        panel_word = self.schema.get("panel_trigger_word") or "状态"
        if stripped in (panel_word, "修士", "状态", "面板"):
            payload = self._render_profile_panel()
            if payload is None:
                payload = TurnPayload(
                    turn_idx=self.turn_idx + 1,
                    narrative=[_panel_block(f"{panel_word}面板", self.state.panel_cultivator())],
                    system_note="修士面板（引擎实时数据）", panel="cultivator",
                    choices=self._engine_choices(),
                )
            yield ("note", self._emit(payload, player_input=stripped))
            return
        if stripped == "任务":
            yield ("note", self._emit(self._tasks_panel(), player_input=stripped))
            return
        if stripped == "提示":
            yield ("note", self._emit(self._hints_panel(), player_input=stripped))
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

    # ---- 身份线与任务面板 ---------------------------------------------------------

    def _resolve_identity(self, wizard_text: str) -> str | None:
        """从向导组合文本中解析身份（首个命中关键词）。"""
        for kw in _IDENTITY_KEYWORDS:
            if kw in wizard_text:
                return kw
        return None

    def _identity_line(self) -> dict | None:
        """当前身份对应的节点链。"""
        identity = self.state.extra.get("identity")
        if not identity:
            return None
        from ..pack.anchors import parse_identity_lines
        for line in parse_identity_lines(self.pack):
            if line["identity"] == identity:
                return line
        return None

    # ---- 世界活性：停滞检测与事件注入 ---------------------------------------------

    def _progress_made(self, applied: list[dict]) -> bool:
        """本轮是否有实质推进（flag/地点/锚点/物品变化）。"""
        return any(
            str(d["ref"]).startswith(("flag:", "item:", "anchor")) or d["ref"] == "地点"
            for d in applied
        )

    def _stall_turns(self, turn: int) -> int:
        last = int(self.state.extra.get("last_progress_turn", 0))
        return max(0, turn - last - 1) if last else turn - 1

    def _pick_stalled_event(self) -> dict | None:
        """停滞时注入的事件：优先已到窗口的时间表锚点，否则轮换世界素材池。"""
        turn = self.turn_idx + 1
        for a in self.anchor_engine.anchors:
            if a.get("is_triggered") or a["kind"] != "timeline":
                continue
            conds = a["trigger"].get("conds", [])
            if any(c.get("type") == "turn_gte" and turn >= c.get("v", 1 << 30)
                   for c in conds):
                return {"title": a["title"], "desc": a["desc"], "anchor": a["title"]}
        if not self.materials:
            return None
        used = set(self.state.extra.get("used_events", []))
        fresh = [m for m in self.materials if m["title"] not in used] or self.materials
        pick = fresh[int(self.state.extra.get("event_cursor", 0)) % len(fresh)]
        self.state.extra["event_cursor"] = int(self.state.extra.get("event_cursor", 0)) + 1
        used.add(pick["title"])
        self.state.extra["used_events"] = list(used)[-12:]
        return dict(pick)

    def _world_agenda(self) -> str:
        """世界活性清单（标题级，防剧透）：让模型知道世界有什么将要发生。"""
        upcoming = [a["title"] for a in self.anchor_engine.anchors
                    if not a.get("is_triggered") and a["kind"] == "timeline"][:6]
        pool = [m["title"] for m in self.materials if len(m["title"]) <= 10][:8]
        parts = []
        if upcoming:
            parts.append("大势将至：" + "、".join(upcoming))
        if pool:
            parts.append("四方风闻（随机事件素材）：" + "、".join(pool))
        return "\n".join(parts)

    # ---- AI 剧本配置（PackProfile） -----------------------------------------------

    def _resolve_story_id(self) -> int | None:
        with self.db.locked() as conn:
            row = conn.execute(
                "SELECT story_id FROM playthroughs WHERE id = ?", (self.playthrough_id,)
            ).fetchone()
        return int(row["story_id"]) if row else None

    def _load_persisted_profile(self) -> dict | None:
        if self.story_id is None:
            return None
        with self.db.locked() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM storys WHERE id = ?", (self.story_id,)
            ).fetchone()
        if not row or not row["metadata_json"]:
            return None
        try:
            meta = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            return None
        return meta if isinstance(meta, dict) and meta.get("source") == "profile" else None

    def _schedule_profile_generation(self) -> None:
        """后台 AI 通读剧本生成配置：用独立模型实例（不占回合推理的实例锁，
        否则回合会被阻塞数分钟）；生成完释放内存，写库后下一局生效。"""
        import threading

        def _work() -> None:
            backend = None
            try:
                # 在线 API 已配置且启用 → 用在线模型通读（质量与速度更佳）；
                # 否则用独立本地实例（不占回合推理的模型）
                db = self.db
                if (dao.settings.get_setting(db, "prefer_online") == "1"
                        and dao.settings.get_setting(db, "api_base_url")
                        and dao.settings.get_setting(db, "api_key")
                        and dao.settings.get_setting(db, "api_model")):
                    backend = self.backend if self.backend.name == "remote" else None
                if backend is None:
                    from ..ai.local import LocalBackend
                    from ..ai import find_model_file

                    model_file = find_model_file(config.MODELS_DIR)
                    if model_file is None:
                        return
                    backend = LocalBackend(model_file, n_ctx=16384)
                profile = build_pack_profile(self.pack, backend)
                if not profile or self.story_id is None:
                    return
                with self.db.locked() as conn:
                    conn.execute(
                        "UPDATE storys SET metadata_json = ? WHERE id = ?",
                        (json.dumps(profile, ensure_ascii=False), self.story_id),
                    )
                    self.db.conn.commit()
                print(f"[profile] 剧本配置已生成并入库：{self.pack.title} "
                      f"({len(profile.get('resources', []))} 资源, "
                      f"{len(profile.get('panels', []))} 面板)")
            except Exception as e:  # 后台生成失败不致命，兜底 schema 继续玩
                print(f"[profile] 生成失败（将沿用兜底配置）：{e}")
            finally:
                del backend   # 释放第二个实例的内存

        threading.Thread(target=_work, daemon=True).start()

    def _render_profile_panel(self) -> TurnPayload | None:
        """按 AI 配置渲染面板：fields.source 从引擎真实状态取数。"""
        if not self.profile_panels:
            return None
        panel = (next((p for p in self.profile_panels if p.get("key") == "cultivator"), None)
                 or self.profile_panels[0])
        fields = []
        for f in panel.get("fields", []):
            label, source = f["label"], f["source"]
            value = "—"
            if source == "realm" and self.state.realms:
                value = self.state.realm_name
            elif source == "progress" and self.state.realms:
                value = f"{self.state.progress:.0f}/100"
            elif source == "lifespan" and self.state.realms:
                value = f"{self.state.lifespan_left:.0f}年"
            elif source == "location":
                value = self.state.location or "未知"
            elif source == "inventory":
                items = [f"{i['name']}×{i.get('count', 1)}" for i in self.state.inventory]
                value = "、".join(items[:6]) if items else "无"
            elif source.startswith("res:"):
                value = self.state._fmt_value(source[4:])
            elif source.startswith("flags:"):
                prefix = source[6:]
                hits = [k for k in self.state.flags
                        if k.startswith(prefix) and self.state.flags[k]]
                value = "、".join(hits[-3:]) if hits else "无"
            fields.append({"label": label, "value": str(value)})
        return TurnPayload(
            turn_idx=self.turn_idx + 1,
            narrative=[{"type": "panel", "title": panel.get("title", "状态"),
                        "fields": fields}],
            system_note="面板（剧本配置定义 · 引擎实时数据）", panel="cultivator",
            choices=self._engine_choices(),
        )

    def _sync_identity_flags(self) -> list[dict]:
        """模型写的自然名 flag → 线:<节点> 登记（幂等）。"""
        line = self._identity_line()
        if line is None:
            return []
        applied = []
        for key in list(self.state.flags.keys()):
            for node in line["nodes"]:
                if f"线:{node}" not in self.state.flags and (key == f"线·{node}" or
                                                             (node in key and "线" in key)):
                    self.state.flags[f"线:{node}"] = True
                    applied.append({"ref": f"flag:线:{node}", "op": "=", "v": 1,
                                    "reason": "身份线节点达成"})
        return applied

    def _hints_panel(self) -> TurnPayload:
        """提示面板（剧本包「提示面板」的三段式，全部引擎真数据）。"""
        rows: list[dict] = []

        # 主线与支线
        next_anchor = next((a for a in self.anchor_engine.anchors
                            if not a.get("is_triggered") and a["kind"] == "timeline"), None)
        fired_recent = [a["title"] for a in self.anchor_engine.anchors
                        if a.get("is_triggered") and a["kind"] == "timeline"][-2:]
        if next_anchor:
            rows.append({"label": "主线 · 山雨欲来", "value": f"{next_anchor['title']}（渐近）"})
        for t in fired_recent:
            rows.append({"label": "主线 · 已历", "value": t})
        line = self._identity_line()
        if line is not None:
            nxt = next((n for n in line["nodes"]
                        if not self.state.flags.get(f"线:{n}")), None)
            rows.append({"label": f"支线 · {line['identity']}线",
                         "value": nxt or "全线功成"})
        if not any(r["label"].startswith("主线") for r in rows):
            rows.append({"label": "主线", "value": "此剧本无固定时间表，命运随行动展开"})

        # 角色互动：近期叙事中提到过的角色
        recent_text = "".join(t.get("text", "") for t in self.recent)
        met = [c["name"] for c in self.characters if c["name"] in recent_text][:5]
        rows.append({"label": "角色互动",
                     "value": "、".join(met) if met else "尚未结识他人"})

        # 系统操作
        rows.append({"label": "系统操作",
                     "value": "「存档」「读取存档」「修士」「任务」「提示」「本章结束」"})

        body = [{"type": "panel", "title": "提示面板", "fields": rows}]
        return TurnPayload(turn_idx=self.turn_idx + 1, narrative=body,
                           system_note="提示面板（引擎实时数据）", panel="hints",
                           choices=self._engine_choices())

    def _tasks_panel(self) -> TurnPayload:
        self._sync_identity_flags()
        line = self._identity_line()
        if line is None:
            body = [{
                "type": "panel", "title": "任务",
                "fields": [
                    {"label": "身份线", "value": "此剧本无固定支线，命运随行动展开"},
                    {"label": "已历事件", "value": "、".join(
                        a["title"] for a in self.anchor_engine.anchors
                        if a.get("is_triggered")) or "尚无"},
                    {"label": "境界目标", "value": f"{self.state.realm_name} → "
                        + (self.schema["realms"][self.state.realm_index + 1]["name"]
                           if self.state.realm_index + 1 < len(self.schema["realms"]) else "大道尽头")},
                ],
            }]
            return TurnPayload(turn_idx=self.turn_idx + 1, narrative=body,
                               system_note="任务面板（引擎实时数据）", panel="tasks",
                               choices=self._engine_choices())

        flags = self.state.flags
        done_next = None
        rows = []
        for i, node in enumerate(line["nodes"]):
            done = bool(flags.get(f"线:{node}"))
            rows.append({"label": node, "value": "✓ 已成" if done else "· 未竟"})
            if not done and done_next is None:
                done_next = len(rows) - 1
        body = [{
            "type": "panel", "title": f"身份线 · {line['identity']}",
            "fields": rows + [{"label": "当前所指", "value":
                               (line["nodes"][done_next] if done_next is not None else "全线功成")}],
        }]
        return TurnPayload(turn_idx=self.turn_idx + 1, narrative=body,
                           system_note="任务面板（引擎实时数据）", panel="tasks")

    # ---- 裁决回合 ----------------------------------------------------------------

    def _adjudicate(self, user_input: str) -> Iterator[tuple[str, object]]:
        turn = self.turn_idx + 1
        # 身份线存在时，向模型注入节点推进规则（进稳定前缀）
        line = self._identity_line()
        extra_system = ""
        if line:
            nodes_hint = "、".join(line["nodes"])
            extra_system = (
                f"\n\n【身份线 · {line['identity']}】玩家的支线进程：{nodes_hint}。\n"
                "当剧情确实完成某节点时，用 {\"flag\":\"线·<节点名>\"} 标记（每节点至多一次）；"
                "禁止跳步、禁止凭空完成。"
            )
        # 剧情主线（系统是导演）：当前幕注入 + 推进规则；幕切换时给章节卡
        chapter_card: dict | None = None
        if self.storyline:
            beat_idx = min(int(self.state.extra.get("beat_idx", 0)), len(self.storyline) - 1)
            cur = self.storyline[beat_idx]
            nxt = (self.storyline[beat_idx + 1]
                   if beat_idx + 1 < len(self.storyline) else None)
            extra_system += (
                f"\n\n【剧情主线 · 第{beat_idx + 1}幕：{cur['title']}】{cur['summary']}\n"
                "你是剧情推动者：本轮叙事必须把玩家卷入当前幕的剧情（NPC 按剧本行动、"
                "事件向你走来、冲突向你展开）；当前幕的关键冲突在叙事中落地后，"
                "在 effects 里加 {\"advance\":true} 推进到下一幕"
                + (f"（下一幕：{nxt['title']}）。" if nxt else "（已是大结局，自由收束）。")
                + "\n玩家是其扮演的角色，不是旁观者——剧情因玩家的选择而改变走向，"
                  "但幕内的核心事件必须发生。"
            )
        # 开局地点未定 → 提醒模型本轮声明（冷启动一次即可）
        if not self.state.location:
            extra_system += (
                "\n\n【开局】本轮请确定玩家当前所在地点，"
                "并在 effects 里用 {\"location\":\"地点名\"} 声明。"
            )
        # 剧情停滞 → 强制注入随机事件（世界活性的核心机制）
        stalled = self._stall_turns(turn) >= STALL_AFTER_TURNS
        forced_event = self._pick_stalled_event() if stalled else None
        if forced_event:
            extra_system += (
                f"\n\n【停滞警报】剧情已连续 {self._stall_turns(turn)} 轮无进展。"
                f"本轮必须发生事件「{forced_event['title']}」——{forced_event.get('desc', '')[:70]}。"
                "写成具体落到玩家头上的事件（谁/在哪/发生什么），"
                f"并在 effects 里用 {{\"flag\":\"事件·{forced_event['title']}\"}} 登记。"
            )
        messages = assemble_messages(
            self.pack, self.characters, self.state, self.recent,
            self.rolling_summary, self.anchor_engine.context_block(turn),
            user_input, turn, extra_system=extra_system,
            world_agenda=self._world_agenda(),
            player_role=self.schema.get("player_role", ""),
        )
        try:
            data = self.backend.generate_json(messages, max_tokens=2000, temperature=0.8)
        except Exception:
            # 裁决彻底失败（重试仍非法）：优雅降级为引擎旁白，不让回合卡死；
            # 失败根因必须留痕（data/play_errors.log）
            log.exception("裁决失败 turn=%s input=%r", turn, user_input[:60])
            note = ("命运的笔锋顿了顿——这一瞬世界没能推演下去。"
                    "换一种行动试试。")
            payload = TurnPayload(
                turn_idx=turn,
                narrative=[{"type": "narration", "text": note},
                           {"type": "broadcast", "fields": self.state.broadcast()}],
                choices=self._engine_choices(),
            )
            self._emit(payload, player_input=user_input)
            yield ("turn", payload)
            return

        # 向导首回合：解析身份并注入后续身份线指令（稳定前缀随之前进一次）
        if user_input.startswith("【人物已定】") and not self.state.extra.get("identity"):
            identity = self._resolve_identity(user_input)
            if identity:
                self.state.extra["identity"] = identity

        narrative_text = str(data.get("narrative", "")).strip() or "（这一刻，什么也没有发生。）"
        effects = [e for e in (data.get("effects") or []) if isinstance(e, dict)]

        # 剧情主线：模型宣告当前幕关键冲突落地 → 推进到下一幕 + 章节卡
        chapter_card: dict | None = None
        advanced = any(e.get("advance") in (True, "true", "True") for e in effects)
        if advanced and self.storyline:
            beat_idx = int(self.state.extra.get("beat_idx", 0))
            if beat_idx + 1 < len(self.storyline):
                self.state.extra["beat_idx"] = beat_idx + 1
                nxt = self.storyline[beat_idx + 1]
                chapter_card = {"type": "chapter", "num": beat_idx + 2,
                                "title": nxt["title"], "summary": nxt["summary"]}

        # 场景地点：裁决 JSON 的必填字段（GBNF 强制每轮输出），引擎直接采信
        scene_location = str(data.get("location", "")).strip()[:12]
        applied, _rejected = self.state.apply_effects(effects, narrative_text=narrative_text)
        if scene_location and scene_location != self.state.location:
            old = self.state.location
            self.state.location = scene_location
            applied.append({"ref": "地点", "op": "=", "v": 1,
                            "reason": f"{old or '未知'} → {scene_location}"})
        # 身份线节点自动登记：模型写的自然名 flag → 线:<节点>
        applied += self._sync_identity_flags()
        # 停滞注入的时间表事件：引擎直接放行（其自然条件可能尚未满足）
        if forced_event and "anchor" not in [k for e in effects if isinstance(e, dict)
                                             for k in e]:
            for a in self.anchor_engine.anchors:
                if a["kind"] == "timeline" and a["title"] == forced_event["title"] \
                        and not a.get("is_triggered"):
                    a["is_triggered"] = True
                    applied.append({"ref": "anchor", "op": "=", "v": 1,
                                    "reason": f"停滞注入：{a['title']}"})

        # 锚点请求按关键词去重（模型会对同一锚点连续请求）
        seen_reqs: set[str] = set()
        anchor_requests = []
        for e in effects:
            if "anchor" in e and str(e["anchor"]) not in seen_reqs:
                seen_reqs.add(str(e["anchor"]))
                anchor_requests.append(str(e["anchor"]))
        fired = self.anchor_engine.evaluate(self.state, turn, anchor_requests)
        if fired:
            applied.append({"ref": "anchor", "op": "=",
                            "v": 1, "reason": "、".join(a["title"] for a in fired)})
            # 已触发的时间表锚点结果进入滚动上下文（按标题去重）
            for a in fired:
                if a["kind"] == "timeline" and f"【{a['title']}】" not in self.rolling_summary:
                    self.rolling_summary = (self.rolling_summary + "；" if self.rolling_summary else "") \
                        + f"事件【{a['title']}】已发生"

        # 地点自动维护：从叙事匹配剧本地名表（长名优先、取最后出现处）
        if self.schema.get("locations"):
            hits = [(narrative_text.rfind(loc), loc) for loc in self.schema["locations"]
                    if loc in narrative_text]
            if hits:
                _, latest = max(hits, key=lambda x: x[0])
                if latest != self.state.location:
                    self.state.location = latest
                    applied.append({"ref": "地点", "op": "=", "v": 1,
                                    "reason": f"行至 {latest}"})

        # 模型根据本轮剧情实时提议选项（清洗后采纳）；缺失/不合规回退状态机生成
        model_choices = []
        for c in (data.get("choices") or []):
            if isinstance(c, str):
                t = c.strip().lstrip("【】").strip()
                if 2 <= len(t) <= 24:
                    model_choices.append(t)
        deduped = list(dict.fromkeys(model_choices))[:4]
        # 不足 4 个 → 状态机兜底补足（模型承诺恰好 4 个，缺了就补齐）
        if len(deduped) < 4:
            for c in self._engine_choices():
                if c["text"] not in deduped:
                    deduped.append(c["text"])
                if len(deduped) >= 4:
                    break
        final_choices = [{"id": chr(65 + i), "text": t, "tags": [], "hint": ""}
                         for i, t in enumerate(deduped[:4])]

        blocks = parse_narrative(narrative_text)
        # 幕切换 → 章节卡置顶（系统的"剧情推动"可见化）
        if chapter_card is not None:
            blocks.insert(0, chapter_card)
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
            choices=final_choices,
            fx={"level": "major"} if any(d["ref"] == "境界" for d in applied) else None,
        )
        self._emit(payload, player_input=user_input,
                   adjudication={"effects": effects, "applied": applied})

        # 实质推进则刷新停滞计时；时间表事件进滚动摘要
        if self._progress_made(applied):
            self.state.extra["last_progress_turn"] = turn
        if self._profile_scheduled and self.schema.get("source") != "profile":
            self._schedule_profile_generation()
            self._profile_scheduled = True   # 已调度；置位语义复用为"已完成调度"
        if turn % SUMMARY_EVERY == 0:
            self.rolling_summary = summarize_turns(
                self.backend, self.recent, self.rolling_summary)
        self._persist_state()
        yield ("turn", payload)

    def _engine_choices(self) -> list[dict]:
        """引擎生成的兜底选项：随状态/身份线/世界素材变化，措辞题材无关。"""
        st = self.state
        opts: list[str] = []

        # 1) 休整/成长向（题材无关措辞；有境界轴才谈"闭关"）
        if st.realms and st.progress >= 70:
            opts.append("专注提升能力，力求突破")
        elif st.inventory and st.realms and any("丹" in i["name"] for i in st.inventory):
            opts.append(f"使用{st.inventory[0]['name']}辅助恢复")
        else:
            opts.append("休整片刻，恢复状态")

        # 2) 身份线当前节点行动
        line = self._identity_line()
        if line is not None:
            nxt = next((n for n in line['nodes']
                        if not self.state.flags.get(f'线:{n}')), None)
            if nxt:
                opts.append(f"设法{'' if len(nxt) > 3 else ''}{nxt}")

        # 3) 剧情动向：当前幕的标题（章节卡已公开展示，非剧透）
        if self.storyline:
            beat_idx = min(int(st.extra.get("beat_idx", 0)), len(self.storyline) - 1)
            opts.append(f"推动「{self.storyline[beat_idx]['title']}」的进展")

        # 4) 社交/地点向（随地点变化）
        where = st.location or "此地"
        opts.append(f"在{where}找人攀谈，打听近闻")
        opts.append("换一处地方走走，见识风物")

        # 去重并保证恰四项
        seen: set[str] = set()
        unique = [o for o in opts if not (o in seen or seen.add(o))]
        return [{"id": chr(65 + i), "text": t, "tags": [], "hint": ""}
                for i, t in enumerate(unique[:4])]

    # ---- 章节结算 ----------------------------------------------------------------

    def _resources_digest(self) -> str:
        return "｜".join(f"{r['ref']}{self.state.attrs.get(r['ref'], 0):g}"
                         for r in self.state.resources)

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
            f"当前：{self.state.realm_name or '旅途中'}，{self._resources_digest()}。",
        ]
        return self._emit(TurnPayload(
            turn_idx=self.turn_idx + 1,
            narrative=[{"type": "panel", "title": "本章结算",
                        "fields": [{"label": f"第{last + 1}-{self.turn_idx}回合", "value": s} for s in lines]}],
            system_note="本章已结算", panel="settlement",
            choices=self._engine_choices(),
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
            f"已存档 [{slot}]：{self.state.realm_name or '旅途中'}｜{self._resources_digest()}",
            panel="save"), player_input="存档")

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
