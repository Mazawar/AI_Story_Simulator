"""设置 DAO：settings 键值表。"""

from __future__ import annotations

from ..database import Database


def get_setting(db: Database, key: str, default: str | None = None) -> str | None:
    row = db.conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row is not None else default


def set_setting(db: Database, key: str, value: str) -> None:
    db.conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.conn.commit()
