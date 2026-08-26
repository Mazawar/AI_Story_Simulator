"""路径与全局常量。所有模块经由这里取路径，禁止自行拼相对路径。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "story_simulator.db"
MODELS_DIR = ROOT / "models"
SCRIPT_DIR = ROOT / "script"          # 剧本包素材（系统提示词文档，非小说）
WEB_ASSETS_DIR = ROOT / "assets" / "web"

APP_NAME = "AI Story Simulator"
APP_VERSION = "0.1.0"


def ensure_runtime_dirs() -> None:
    """创建运行时目录（幂等）。"""
    DATA_DIR.mkdir(exist_ok=True)
    MODELS_DIR.mkdir(exist_ok=True)
