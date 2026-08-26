"""数据访问对象（DAO）。

约定：每个函数在调用点上使用固定 SQL 字面量 + 参数绑定，
不接受外部传入的 SQL 文本。
"""

from . import packs, plays, settings

__all__ = ["packs", "plays", "settings"]
