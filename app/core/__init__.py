"""游戏引擎：回合循环、状态、触发词拦截、存档。"""

from .engine import TRIGGER_WORDS, DirectEngine
from .state import GameState, PlayerState, WorldState

__all__ = ["TRIGGER_WORDS", "DirectEngine", "GameState", "PlayerState", "WorldState"]
