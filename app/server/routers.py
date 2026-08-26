"""API 路由（DESIGN.md §5.4）。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config
from ..ai import resolve_backend
from ..core.engine import DirectEngine
from ..db import dao
from ..pack import load_packs
from .sessions import REGISTRY, PlaySession

router = APIRouter()


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
            }
            for p in packs
        ]
    }


# ---- 对局 -------------------------------------------------------------------


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

    engine = DirectEngine(db, resolve_backend(db, dry_run=request.app.state.dry_run),
                          pack, playthrough_id)
    REGISTRY.put(playthrough_id, PlaySession(engine))
    return {"playthrough_id": playthrough_id, "pack_title": pack.title,
            "backend": engine.backend.name}


def _session_or_404(playthrough_id: int) -> PlaySession:
    session = REGISTRY.get(playthrough_id)
    if session is None:
        raise HTTPException(status_code=404, detail="对局不存在（服务重启后请重新开始）")
    return session


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
