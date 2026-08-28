"""后端路由：按配置解析本地/在线/演练后端，含回落逻辑。

settings 键（存 SQLite settings 表）：
- api_base_url / api_key / api_model   在线 API 配置
- prefer_online                        "1" = 在线优先（失败回落本地）
- api_allow_private                    "1" = 允许内网/本机端点（Ollama 等）
- model_choice                         local=1.7B主力（默认）/ 4b=增强档
"""

from __future__ import annotations

from .. import config
from ..db import dao
from ..db.database import Database
from .backend import CannedBackend, LLMBackend
from .local import LocalBackend, find_model_file
from .remote import RemoteBackend


def resolve_model_file(db: Database | None = None):
    """按 model_choice 设置解析本地模型文件（auto=1.7B 主力，4b=增强档）。"""
    from .local import _PREFERRED_PATTERNS

    choice = ""
    if db is not None:
        choice = dao.settings.get_setting(db, "model_choice") or ""
    if choice == "4b":
        models_dir = config.MODELS_DIR
        if models_dir.is_dir():
            for p in sorted(models_dir.glob("*.gguf")):
                if "4b" in p.name.lower():
                    return p
    return find_model_file(config.MODELS_DIR)


def resolve_backend(db: Database | None = None, *, dry_run: bool = False,
                    api_base: str | None = None, api_key: str | None = None,
                    api_model: str | None = None) -> LLMBackend:
    """解析后端：显式 API 参数 > 设置表 > 本地模型 > 演练。"""
    if dry_run:
        return CannedBackend()

    base = key = model = None
    allow_private = False
    prefer_online = False
    if api_base and api_key and api_model:
        base, key, model = api_base, api_key, api_model
        if db is not None:
            dao.settings.set_setting(db, "api_base_url", api_base)
            dao.settings.set_setting(db, "api_key", api_key)
            dao.settings.set_setting(db, "api_model", api_model)
    elif db is not None:
        base = dao.settings.get_setting(db, "api_base_url")
        key = dao.settings.get_setting(db, "api_key")
        model = dao.settings.get_setting(db, "api_model")
        allow_private = dao.settings.get_setting(db, "api_allow_private") == "1"
        prefer_online = dao.settings.get_setting(db, "prefer_online") == "1"

    if prefer_online and base and key and model:
        return RemoteBackend(base, key, model, allow_private=allow_private)

    model_file = resolve_model_file(db)
    if model_file is not None:
        try:
            return LocalBackend(model_file)
        except RuntimeError as e:
            print(f"[router] 本地后端不可用：{e}")
    if base and key and model:
        return RemoteBackend(base, key, model, allow_private=allow_private)
    print("[router] 未找到本地模型且未配置在线 API，回落到演练后端（--dry-run 语义）")
    return CannedBackend()
