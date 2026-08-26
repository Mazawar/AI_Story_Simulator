"""数据库迁移：schema v1（对应 DESIGN.md §7）。

迁移按 PRAGMA user_version 版本化，只增不改。
每条语句都是调用点上的固定字符串字面量，不含任何外部输入。
"""

from __future__ import annotations

import sqlite3

from .database import Database


def _v1(conn: sqlite3.Connection) -> None:
    # ---- 剧本包与剧本 -------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS packs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT NOT NULL,
            file_path      TEXT NOT NULL,
            format_version INTEGER NOT NULL DEFAULT 1,
            raw_text       TEXT NOT NULL,
            parse_status   TEXT NOT NULL DEFAULT 'pending',
            created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pack_sections (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id   INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
            key       TEXT NOT NULL,
            kind      TEXT NOT NULL DEFAULT 'section',
            title     TEXT NOT NULL,
            body      TEXT NOT NULL,
            order_idx INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS storys (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT NOT NULL,
            source_type      TEXT NOT NULL CHECK (source_type IN ('pack','manual','novel')),
            source_pack_id   INTEGER REFERENCES packs(id),
            source_novel_id  INTEGER,
            world_rules_json TEXT,
            metadata_json    TEXT,
            quality_grade    TEXT,
            review_status    TEXT NOT NULL DEFAULT 'ready',
            status           TEXT NOT NULL DEFAULT 'active',
            created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_nodes (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id              INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            node_key              TEXT NOT NULL,
            title                 TEXT NOT NULL,
            summary               TEXT,
            location              TEXT,
            participants_json     TEXT,
            entry_conditions_json TEXT,
            hooks_text            TEXT,
            exits_json            TEXT,
            order_idx             INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_anchors (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id         INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            title            TEXT NOT NULL,
            trigger_json     TEXT,
            reveal_text      TEXT,
            spoiler_level    TEXT NOT NULL DEFAULT 'normal',
            is_triggered     INTEGER NOT NULL DEFAULT 0,
            sort_idx         INTEGER NOT NULL DEFAULT 0,
            source_refs_json TEXT,
            confidence       REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id         INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            name             TEXT NOT NULL,
            aliases_json     TEXT,
            role             TEXT,
            personality      TEXT,
            goal             TEXT,
            affinity         REAL NOT NULL DEFAULT 0,
            is_alive         INTEGER NOT NULL DEFAULT 1,
            memory_json      TEXT,
            source_refs_json TEXT,
            confidence       REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creation_steps (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id     INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            step_idx     INTEGER NOT NULL,
            question     TEXT NOT NULL,
            options_json TEXT NOT NULL,
            effect_json  TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panel_specs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id  INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            panel_key TEXT NOT NULL,
            spec_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trigger_words (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL REFERENCES storys(id) ON DELETE CASCADE,
            word     TEXT NOT NULL,
            action   TEXT NOT NULL
        )
        """
    )
    # ---- 原始小说（source_type='novel' 路径，阶段 3） ------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS novels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            author      TEXT,
            source_path TEXT NOT NULL,
            charset     TEXT,
            total_chars INTEGER,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id   INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
            idx        INTEGER NOT NULL,
            title      TEXT NOT NULL,
            char_count INTEGER,
            raw_text   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id   INTEGER NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
            chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
            idx        INTEGER NOT NULL,
            text       TEXT NOT NULL,
            embedding  BLOB
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extract_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id          INTEGER NOT NULL,
            chapter_id        INTEGER,
            payload_json      TEXT,
            gate_results_json TEXT,
            reviewed          INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # ---- 游戏运行与存档 ------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playthroughs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id         INTEGER NOT NULL REFERENCES storys(id),
            mode             TEXT NOT NULL CHECK (mode IN ('engine','direct')),
            player_json      TEXT,
            world_flags_json TEXT,
            current_node_key TEXT,
            turn_count       INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            playthrough_id    INTEGER NOT NULL REFERENCES playthroughs(id) ON DELETE CASCADE,
            idx               INTEGER NOT NULL,
            turn_payload_json TEXT,
            player_input      TEXT,
            adjudication_json TEXT,
            created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saves (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            playthrough_id INTEGER NOT NULL REFERENCES playthroughs(id) ON DELETE CASCADE,
            slot           TEXT NOT NULL,
            summary        TEXT,
            snapshot_json  TEXT NOT NULL,
            created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE (playthrough_id, slot)
        )
        """
    )
    # ---- 配置 ----------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            task     TEXT NOT NULL,
            template TEXT NOT NULL,
            version  INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # ---- 索引 ----------------------------------------------------------------
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pack_sections_pack ON pack_sections(pack_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_novel ON chunks(novel_id, chapter_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_play ON turns(playthrough_id, idx)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_saves_play ON saves(playthrough_id, slot)")
    conn.execute("PRAGMA user_version = 1")


def _v2(conn: sqlite3.Connection) -> None:
    # 引擎模式：滚动摘要 + 锚点触发记录（v2）
    conn.execute("ALTER TABLE playthroughs ADD COLUMN rolling_summary TEXT")
    conn.execute("PRAGMA user_version = 2")


MIGRATIONS: list[tuple[int, callable]] = [(1, _v1), (2, _v2)]


def migrate(db: Database) -> int:
    """应用未执行的迁移，返回当前 schema 版本。"""
    version = db.schema_version()
    for target, apply_fn in MIGRATIONS:
        if target <= version:
            continue
        with db.transaction() as conn:
            apply_fn(conn)
        version = target
    return version
