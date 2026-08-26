"""剧本包 DAO：packs / pack_sections / storys。"""

from __future__ import annotations

from ...db.database import Database
from ...pack.models import Pack


def upsert_pack(db: Database, pack: Pack) -> int:
    """写入或刷新一个剧本包及其章节，返回 pack id。"""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM packs WHERE title = ? AND file_path = ?",
            (pack.title, pack.file_path),
        ).fetchone()
        if row is not None:
            pack_id = int(row["id"])
            conn.execute(
                "UPDATE packs SET raw_text = ?, parse_status = ? WHERE id = ?",
                (pack.raw_text, "loaded", pack_id),
            )
            conn.execute("DELETE FROM pack_sections WHERE pack_id = ?", (pack_id,))
        else:
            cur = conn.execute(
                "INSERT INTO packs (title, file_path, raw_text, parse_status) VALUES (?, ?, ?, ?)",
                (pack.title, pack.file_path, pack.raw_text, "loaded"),
            )
            pack_id = int(cur.lastrowid)

        conn.executemany(
            "INSERT INTO pack_sections (pack_id, key, kind, title, body, order_idx)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (pack_id, s.key, "section", s.title, s.body, s.order_idx)
                for s in pack.sections
            ],
        )
    return pack_id


def list_packs(db: Database) -> list[dict]:
    rows = db.conn.execute(
        "SELECT id, title, file_path, parse_status, created_at FROM packs ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_story_for_pack(db: Database, pack_id: int, title: str) -> int:
    """取剧本包对应的 story（无则创建），返回 story id。"""
    row = db.conn.execute(
        "SELECT id FROM storys WHERE source_type = 'pack' AND source_pack_id = ?",
        (pack_id,),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = db.conn.execute(
        "INSERT INTO storys (title, source_type, source_pack_id) VALUES (?, 'pack', ?)",
        (title, pack_id),
    )
    return int(cur.lastrowid)
