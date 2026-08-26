"""SQLite 连接管理：连接、事务、schema 版本。

安全约束（项目级验收条件）：
- 本模块不提供任何接受外部 SQL 文本的透传接口；
- 全部 SQL 以内联字面量形式写在 db/dao/ 与 db/migrations.py 的调用点上，
  所有外部输入一律通过参数绑定（占位符 ?）传入，禁止拼接/format/f-string 组装 SQL。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def schema_version(self) -> int:
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    @contextmanager
    def transaction(self):
        """事务：异常回滚。存档写入等复合操作必须走这里。"""
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()
