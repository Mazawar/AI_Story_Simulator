"""AI 服务层：LLMBackend 抽象 + 本地/在线/演练后端 + 路由。"""

from .backend import CannedBackend, LLMBackend, Message, repair_json
from .local import LocalBackend, find_model_file
from .remote import RemoteBackend
from .router import resolve_backend

__all__ = [
    "LLMBackend", "CannedBackend", "LocalBackend", "RemoteBackend",
    "Message", "repair_json", "find_model_file", "resolve_backend",
]
