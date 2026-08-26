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
from ..ai.local import find_model_file, pick_context_window
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
    cached = _BACKEND_CACHE.get(n_ctx)
    if cached is not None:
        return cached
    from ..ai.local import LocalBackend

    model_file = find_model_file(config.MODELS_DIR)
    if model_file is not None:
        try:
            backend = LocalBackend(model_file, n_ctx=n_ctx)
            _BACKEND_CACHE[n_ctx] = backend
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
    """最近的直通模式对局（续玩入口数据）。"""
    with _db(request).locked() as conn:
        rows = conn.execute(
            "SELECT p.id, p.turn_count, p.updated_at, s.title AS story_title,"
            " (SELECT summary FROM saves WHERE playthrough_id = p.id"
            "  ORDER BY updated_at DESC LIMIT 1) AS save_summary"
            " FROM playthroughs p JOIN storys s ON s.id = p.story_id"
            " WHERE p.mode = 'direct' AND p.turn_count > 0"
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
    playthrough_id = dao.plays.create_playthrough(db, story_id, mode="direct")

    n_ctx = pick_context_window(len(pack.system_prompt()))
    engine = DirectEngine(db, _shared_backend(db, request.app.state.dry_run, n_ctx),
                          pack, playthrough_id, n_ctx=n_ctx)
    REGISTRY.put(playthrough_id, PlaySession(engine))
    return {"playthrough_id": playthrough_id, "pack_title": pack.title,
            "backend": engine.backend.name, "n_ctx": n_ctx}


def _session_or_404(playthrough_id: int) -> PlaySession:
    session = REGISTRY.get(playthrough_id)
    if session is None:
        raise HTTPException(status_code=404, detail="对局不存在（服务重启后请重新开始）")
    return session


@router.post("/play/{playthrough_id}/resume")
def resume_play(request: Request, playthrough_id: int):
    """续玩：重建引擎（优先用最新存档快照，无存档则从回合历史重建）。"""
    db = _db(request)
    with db.locked() as conn:
        row = conn.execute(
            "SELECT p.id, p.turn_count, s.title AS story_title, pk.raw_text AS pack_text,"
            " pk.file_path AS pack_file"
            " FROM playthroughs p"
            " JOIN storys s ON s.id = p.story_id"
            " LEFT JOIN packs pk ON pk.id = s.source_pack_id"
            " WHERE p.id = ? AND p.mode = 'direct'",
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
