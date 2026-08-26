"""对局会话与事件总线：推理线程 → SSE 订阅者。

每个对局一个 PlaySession：串行执行回合（同对局同时只跑一个回合），
生成过程中的增量与最终 TurnPayload 经事件总线发布到所有 SSE 订阅者。
"""

from __future__ import annotations

import asyncio
import threading

from ..core.engine import DirectEngine


class PlaySession:
    def __init__(self, engine: DirectEngine):
        self.engine = engine
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: list[asyncio.Queue] = []
        self._turn_lock = threading.Lock()

    # ---- 订阅 ---------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    # ---- 发布（推理线程安全） -------------------------------------------------

    def _publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def _publish_from_thread(self, event: dict) -> None:
        if self.loop is not None and self._subscribers:
            self.loop.call_soon_threadsafe(self._publish, event)

    # ---- 回合执行 -------------------------------------------------------------

    def submit(self, text: str) -> None:
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()

    def _run_turn(self, text: str) -> None:
        with self._turn_lock:
            try:
                for kind, data in self.engine.stream_handle(text):
                    if kind == "delta":
                        self._publish_from_thread({"type": "delta", "text": data})
                    elif kind in ("note", "turn"):
                        self._publish_from_thread({"type": kind, "payload": data.to_dict()})
            except Exception as e:  # 推理失败也要通知前端，避免界面卡在等待
                self._publish_from_thread({"type": "error", "message": str(e)})


class SessionRegistry:
    def __init__(self):
        self._sessions: dict[int, PlaySession] = {}
        self._lock = threading.Lock()

    def get(self, playthrough_id: int) -> PlaySession | None:
        with self._lock:
            return self._sessions.get(playthrough_id)

    def put(self, playthrough_id: int, session: PlaySession) -> PlaySession:
        with self._lock:
            self._sessions[playthrough_id] = session
            return session

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


REGISTRY = SessionRegistry()
