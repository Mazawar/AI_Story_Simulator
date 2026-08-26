"""路径与全局常量。所有模块经由这里取路径，禁止自行拼相对路径。

打包（PyInstaller onedir）后的目录约定：
  AIStorySimulator/
  ├─ AIStorySimulator.exe
  ├─ models/    script/    data/          ← 用户可见可替换（紧邻 exe）
  └─ _internal/  （代码 + web/dist 静态前端）
查找顺序：exe 目录 → _internal，兼顾两种放置方式。
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent


def _first_existing(*candidates: Path) -> Path:
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "story_simulator.db"
MODELS_DIR = _first_existing(ROOT / "models", ROOT / "_internal" / "models")
SCRIPT_DIR = _first_existing(ROOT / "script", ROOT / "_internal" / "script")
WEB_DIR = ROOT / "web"                # React 前端源码（仅开发环境存在）
WEB_DIST_DIR = _first_existing(ROOT / "web" / "dist", ROOT / "_internal" / "web" / "dist")

# 窗口图标：随包分发（spec datas 把 assets/icon.ico 放进 _internal/assets/）
ICON_PATH = ROOT / "assets" / "icon.ico"
if not ICON_PATH.is_file():
    ICON_PATH = ROOT / "_internal" / "assets" / "icon.ico"

APP_NAME = "AI Story Simulator"
APP_VERSION = "0.1.0"


def ensure_runtime_dirs() -> None:
    """创建运行时目录（幂等）。"""
    DATA_DIR.mkdir(exist_ok=True)
    MODELS_DIR.mkdir(exist_ok=True)
