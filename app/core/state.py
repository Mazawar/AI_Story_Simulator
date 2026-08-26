"""游戏状态模型（DESIGN.md §9）。

阶段 0 直通模式仅使用最小状态；引擎模式（阶段 2）补全属性/锚点/NPC 记忆。
所有状态对象可整体序列化——存档快照的直接内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerState:
    """玩家：属性（按剧本包数值体系）、物品栏、身份背景。"""

    name: str = "无名者"
    background: str = ""                # 首轮选择的身份落点（时期/身份/资质）
    attrs: dict[str, float] = field(default_factory=dict)
    inventory: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "background": self.background,
            "attrs": self.attrs,
            "inventory": self.inventory,
        }


@dataclass
class WorldState:
    """世界：剧情 flag、事件时间表进度。"""

    flags: dict[str, bool] = field(default_factory=dict)
    timeline_progress: int = 0

    def to_dict(self) -> dict:
        return {"flags": self.flags, "timeline_progress": self.timeline_progress}


@dataclass
class GameState:
    """一局游戏的整体状态（存档快照的主体）。"""

    player: PlayerState = field(default_factory=PlayerState)
    world: WorldState = field(default_factory=WorldState)
    extra: dict = field(default_factory=dict)   # 直通模式存消息历史等

    def to_dict(self) -> dict:
        return {
            "player": self.player.to_dict(),
            "world": self.world.to_dict(),
            "extra": self.extra,
        }
