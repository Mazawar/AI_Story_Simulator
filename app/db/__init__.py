"""数据层：连接管理（连接/事务/schema 版本）+ 迁移 + DAO。

全部 SQL 以内联字面量写在 DAO 与迁移的调用点上，外部输入一律参数绑定。
"""

from .database import Database
from .migrations import migrate

__all__ = ["Database", "migrate"]
