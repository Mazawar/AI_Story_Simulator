"""对局 DAO：playthroughs / turns / saves。"""

from __future__ import annotations

import json

from ..database import Database


def create_playthrough(db: Database, story_id: int, mode: str, player_json: dict | None = None) -> int:
    cur = db.conn.execute(
        "INSERT INTO playthroughs (story_id, mode, player_json) VALUES (?, ?, ?)",
        (story_id, mode, json.dumps(player_json, ensure_ascii=False) if player_json else None),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def add_turn(
    db: Database, playthrough_id: int, idx: int, payload: dict,
    player_input: str | None, adjudication: dict | None = None,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO turns (playthrough_id, idx, turn_payload_json, player_input, adjudication_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                playthrough_id,
                idx,
                json.dumps(payload, ensure_ascii=False),
                player_input,
                json.dumps(adjudication, ensure_ascii=False) if adjudication else None,
            ),
        )
        conn.execute(
            "UPDATE playthroughs SET turn_count = ?, updated_at = datetime('now','localtime')"
            " WHERE id = ?",
            (idx, playthrough_id),
        )


def write_save(db: Database, playthrough_id: int, slot: str, summary: str, snapshot: dict) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO saves (playthrough_id, slot, summary, snapshot_json)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(playthrough_id, slot) DO UPDATE SET"
            " summary = excluded.summary, snapshot_json = excluded.snapshot_json,"
            " updated_at = datetime('now','localtime')",
            (
                playthrough_id,
                slot,
                summary,
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )


def load_save(db: Database, playthrough_id: int, slot: str) -> dict | None:
    row = db.conn.execute(
        "SELECT snapshot_json FROM saves WHERE playthrough_id = ? AND slot = ?",
        (playthrough_id, slot),
    ).fetchone()
    return json.loads(row["snapshot_json"]) if row is not None else None
