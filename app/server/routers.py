"""API 路由（DESIGN.md §5.4）。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config
from ..ai import resolve_backend
from ..ai.backend import LLMBackend
from ..ai.local import LocalBackend, pick_context_window
from ..ai.router import resolve_model_file
from ..core.engine import DirectEngine, rebuild_history
from ..db import dao
from ..pack import load_packs, pack_meta
from .sessions import REGISTRY, PlaySession

router = APIRouter()

# 后端缓存：按上下文档位分开（剧本包提示词处理只付一次，KV 缓存跨对局复用）；
# canned（缺模型回落）不缓存，便于用户放入模型后生效
_BACKEND_CACHE: dict[int, LLMBackend] = {}


def _shared_backend(db, dry_run: bool, n_ctx: int) -> LLMBackend:
    if dry_run:
        from ..ai.backend import CannedBackend
        return CannedBackend()
    key = n_ctx
    cached = _BACKEND_CACHE.get(key)
    if cached is not None:
        return cached
    # 在线优先设置生效时直接用远程（配置无效则回落本地，不让开局 500）
    if (dao.settings.get_setting(db, "prefer_online") == "1"
            and dao.settings.get_setting(db, "api_base_url")
            and dao.settings.get_setting(db, "api_key")
            and dao.settings.get_setting(db, "api_model")):
        try:
            backend = resolve_backend(db)
            if backend.name == "remote":
                _BACKEND_CACHE[key] = backend
                return backend
        except Exception as e:
            print(f"[router] 在线后端不可用，回落本地：{e}")
    model_file = resolve_model_file(db)
    if model_file is not None:
        try:
            backend = LocalBackend(model_file, n_ctx=n_ctx)
            _BACKEND_CACHE[key] = backend
            return backend
        except RuntimeError as e:
            print(f"[router] 本地后端不可用：{e}")
    return resolve_backend(db)


def _db(request: Request):
    return request.app.state.db


# ---- 基础 -------------------------------------------------------------------


@router.get("/health")
def health(request: Request):
    return {
        "ok": True,
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "backend": request.app.state.backend_name,
    }


@router.get("/packs")
def list_packs(request: Request):
    packs = load_packs(config.SCRIPT_DIR)
    return {
        "packs": [
            {
                "title": p.title,
                "sections": len(p.sections),
                "chars": len(p.raw_text),
                "file": p.file_path,
                **pack_meta(p),
            }
            for p in packs
        ]
    }


# ---- 对局 -------------------------------------------------------------------


@router.get("/plays")
def list_plays(request: Request):
    """最近的对局（续玩入口数据，含引擎/直通两种模式）。"""
    with _db(request).locked() as conn:
        rows = conn.execute(
            "SELECT p.id, p.turn_count, p.updated_at, p.mode, s.title AS story_title,"
            " (SELECT summary FROM saves WHERE playthrough_id = p.id"
            "  ORDER BY updated_at DESC LIMIT 1) AS save_summary"
            " FROM playthroughs p JOIN storys s ON s.id = p.story_id"
            " WHERE p.turn_count > 0"
            " ORDER BY p.updated_at DESC LIMIT 8"
        ).fetchall()
    return {"plays": [dict(r) for r in rows]}


class PlayRequest(BaseModel):
    pack_title: str


@router.post("/play")
def create_play(request: Request, body: PlayRequest):
    packs = load_packs(config.SCRIPT_DIR)
    pack = next((p for p in packs if body.pack_title in p.title), None)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"未找到剧本包：{body.pack_title}")

    db = _db(request)
    pack_id = dao.packs.upsert_pack(db, pack)
    story_id = dao.packs.get_story_for_pack(db, pack_id, pack.title)

    mode = "engine"      # 引擎模式默认（数值/面板/锚点由代码执行）；?mode=direct 调试
    if request.query_params.get("mode") == "direct":
        mode = "direct"
    playthrough_id = dao.plays.create_playthrough(db, story_id, mode=mode)

    if mode == "engine":
        from ..core.engine_mode import EngineSession

        engine = EngineSession(db, _shared_backend(db, request.app.state.dry_run, 8192),
                               pack, playthrough_id)
        engine._persist_state()
        backend_name, n_ctx = engine.backend.name, 8192
        panel_word = engine.schema.get("panel_trigger_word") or "状态"
        player_role = engine.schema.get("player_role") or ""
    else:
        n_ctx = pick_context_window(len(pack.system_prompt()))
        engine = DirectEngine(db, _shared_backend(db, request.app.state.dry_run, n_ctx),
                              pack, playthrough_id, n_ctx=n_ctx)
        backend_name = engine.backend.name
        panel_word = "状态"
        player_role = ""
    REGISTRY.put(playthrough_id, PlaySession(engine))
    return {"playthrough_id": playthrough_id, "pack_title": pack.title,
            "backend": backend_name, "n_ctx": n_ctx, "mode": mode,
            "panel_word": panel_word, "player_role": player_role}


def _session_or_404(playthrough_id: int) -> PlaySession:
    session = REGISTRY.get(playthrough_id)
    if session is None:
        raise HTTPException(status_code=404, detail="对局不存在（服务重启后请重新开始）")
    return session


@router.post("/play/{playthrough_id}/resume")
def resume_play(request: Request, playthrough_id: int):
    """续玩：重建引擎（引擎模式用持久化状态；直通模式用存档快照/回合重建）。"""
    db = _db(request)
    with db.locked() as conn:
        row = conn.execute(
            "SELECT p.id, p.turn_count, p.mode, p.player_json, p.rolling_summary,"
            " s.title AS story_title, s.metadata_json,"
            " pk.raw_text AS pack_text, pk.file_path AS pack_file"
            " FROM playthroughs p"
            " JOIN storys s ON s.id = p.story_id"
            " LEFT JOIN packs pk ON pk.id = s.source_pack_id"
            " WHERE p.id = ?",
            (playthrough_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="对局不存在")
        save_row = conn.execute(
            "SELECT snapshot_json FROM saves WHERE playthrough_id = ?"
            " ORDER BY updated_at DESC LIMIT 1",
            (playthrough_id,),
        ).fetchone()
        if save_row is None:
            turn_rows = conn.execute(
                "SELECT turn_payload_json, player_input FROM turns"
                " WHERE playthrough_id = ? ORDER BY idx",
                (playthrough_id,),
            ).fetchall()

    # 重建剧本包：优先从 script/ 重载（文件可能已更新），回退库内原文
    packs = load_packs(config.SCRIPT_DIR)
    pack = next((p for p in packs if row["story_title"] in p.title), None)
    if pack is None:
        if not row["pack_text"]:
            raise HTTPException(status_code=404, detail="剧本包源不可用")
        from ..pack import split_sections
        from ..pack.models import Pack
        pack = Pack(title=row["story_title"], file_path=row["pack_file"] or "",
                    raw_text=row["pack_text"],
                    sections=split_sections(row["pack_text"]))

    mode = row["mode"] or "direct"
    if mode == "engine":
        from ..core.engine_mode import EngineSession
        from ..core.rules import NumericState
        from ..pack.numeric import parse_numeric_schema

        # schema 来源优先 AI 剧本配置（storys.metadata_json），回退确定性解析
        engine_schema = None
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
                if isinstance(meta, dict) and meta.get("source") == "profile":
                    engine_schema = meta
            except json.JSONDecodeError:
                pass
        state = NumericState(engine_schema or parse_numeric_schema(pack),
                             json.loads(row["player_json"]) if row["player_json"] else None)
        engine = EngineSession(db, _shared_backend(db, request.app.state.dry_run, 8192),
                               pack, playthrough_id, state=state, schema=engine_schema,
                               rolling_summary=row["rolling_summary"] or "")
        REGISTRY.put(playthrough_id, PlaySession(engine))
        return {"playthrough_id": playthrough_id, "pack_title": pack.title,
                "backend": engine.backend.name, "n_ctx": 8192, "mode": "engine",
                "resumed": True, "turn_count": engine.turn_idx}

    # ---- 直通模式续玩 -----------------------------------------------------------
    # 重建消息历史
    if save_row is not None:
        history = json.loads(save_row["snapshot_json"]).get("history", [])
    else:
        history = []
        for t in turn_rows:
            if t["player_input"]:
                history.append({"role": "user", "content": t["player_input"]})
            history.append({"role": "assistant",
                            "content": rebuild_history(json.loads(t["turn_payload_json"]))})

    n_ctx = pick_context_window(len(pack.system_prompt()))
    engine = DirectEngine(db, _shared_backend(db, request.app.state.dry_run, n_ctx),
                          pack, playthrough_id, history=history or None, n_ctx=n_ctx)
    REGISTRY.put(playthrough_id, PlaySession(engine))
    return {"playthrough_id": playthrough_id, "pack_title": pack.title,
            "backend": engine.backend.name, "n_ctx": n_ctx,
            "resumed": True, "turn_count": engine.turn_idx}


class InputRequest(BaseModel):
    text: str


@router.post("/play/{playthrough_id}/input")
def submit_input(playthrough_id: int, body: InputRequest):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="输入为空")
    _session_or_404(playthrough_id).submit(body.text)
    return {"ok": True}


@router.get("/play/{playthrough_id}/events")
async def events(playthrough_id: int, heartbeat: int = 15):
    session = _session_or_404(playthrough_id)
    queue = session.subscribe()
    heartbeat = max(1, min(heartbeat, 60))

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            session.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/play/{playthrough_id}/history")
def history(request: Request, playthrough_id: int):
    with _db(request).locked() as conn:
        rows = conn.execute(
            "SELECT idx, turn_payload_json, player_input FROM turns"
            " WHERE playthrough_id = ? ORDER BY idx",
            (playthrough_id,),
        ).fetchall()
    return {
        "turns": [
            {
                "idx": r["idx"],
                "player_input": r["player_input"],
                "payload": json.loads(r["turn_payload_json"]),
            }
            for r in rows
        ]
    }


@router.get("/play/{playthrough_id}/saves")
def saves(request: Request, playthrough_id: int):
    with _db(request).locked() as conn:
        rows = conn.execute(
            "SELECT slot, summary, updated_at FROM saves"
            " WHERE playthrough_id = ? ORDER BY updated_at DESC",
            (playthrough_id,),
        ).fetchall()
    return {"saves": [dict(r) for r in rows]}


# ---- 设置（在线 API / 模型档位） -----------------------------------------------

class SettingsIn(BaseModel):
    api_base_url: str | None = None
    api_key: str | None = None            # 空串/缺省 = 不修改已存密钥
    api_model: str | None = None
    prefer_online: bool | None = None
    api_allow_private: bool | None = None
    model_choice: str | None = None       # local | 4b


@router.get("/settings")
def get_settings(request: Request):
    db = _db(request)
    def s(key, default=""):
        return dao.settings.get_setting(db, key) or default
    key = s("api_key")
    return {
        "api_base_url": s("api_base_url"),
        "api_model": s("api_model"),
        "api_key_set": bool(key),
        "api_key_masked": (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("****" if key else ""),
        "prefer_online": s("prefer_online") == "1",
        "api_allow_private": s("api_allow_private") == "1",
        "model_choice": s("model_choice", "local"),
    }


@router.post("/settings")
def save_settings(request: Request, body: SettingsIn):
    db = _db(request)
    # 任意在线配置/模型档位变更 → 丢弃后端缓存，新对局立即按新设置路由
    _BACKEND_CACHE.clear()
    if body.api_base_url is not None:
        dao.settings.set_setting(db, "api_base_url", body.api_base_url.strip())
    if body.api_key:
        dao.settings.set_setting(db, "api_key", body.api_key.strip())
    if body.api_model is not None:
        dao.settings.set_setting(db, "api_model", body.api_model.strip())
    if body.prefer_online is not None:
        dao.settings.set_setting(db, "prefer_online", "1" if body.prefer_online else "0")
    if body.api_allow_private is not None:
        dao.settings.set_setting(db, "api_allow_private", "1" if body.api_allow_private else "0")
    if body.model_choice in ("local", "4b"):
        dao.settings.set_setting(db, "model_choice", body.model_choice)
    # 模型档位变更后丢弃本地后端缓存，下次开局生效
    if body.model_choice is not None:
        _BACKEND_CACHE.clear()
    return get_settings(request)


@router.post("/settings/test")
def test_settings(request: Request):
    """用当前设置真实连通一次在线 API（1 token 心跳请求）。"""
    import urllib.error

    db = _db(request)
    base = dao.settings.get_setting(db, "api_base_url")
    key = dao.settings.get_setting(db, "api_key")
    model = dao.settings.get_setting(db, "api_model")
    allow_private = dao.settings.get_setting(db, "api_allow_private") == "1"
    if not (base and key and model):
        return {"ok": False, "message": "请先填写 API 地址、密钥与模型名"}
    try:
        backend = resolve_backend(db)
        if backend.name != "remote":
            return {"ok": False, "message": "后端未走在线通道（检查配置）"}
        text = backend.generate([{"role": "user", "content": "回复：ok"}],
                                max_tokens=8, temperature=0)
        return {"ok": True, "message": f"连通成功，模型回复：{text[:40]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}
