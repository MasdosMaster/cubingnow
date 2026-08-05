import asyncio
import contextlib
import json
import time

import websockets


class CubingChinaWebSocketError(RuntimeError):
    """Raised for CubingChina WebSocket transport or protocol failures."""


class CubingChinaWebSocketClient:
    def __init__(
        self,
        endpoint: str = "wss://cubing.com/ws",
        keepalive_seconds: float = 55,
        connect_factory=None,
    ):
        self.endpoint = endpoint
        self.keepalive_seconds = max(keepalive_seconds, 1)
        self._connect_factory = connect_factory or websockets.connect
        self._socket = None
        self._keepalive_task = None
        self._last_activity = time.monotonic()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def connect(self) -> None:
        if self._socket is not None:
            return
        self._socket = await self._connect_factory(
            self.endpoint,
            ping_interval=None,
            close_timeout=10,
            max_size=16 * 1024 * 1024,
        )
        self._last_activity = time.monotonic()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def send(self, payload) -> None:
        if self._socket is None:
            raise CubingChinaWebSocketError("CubingChina socket is not connected")
        await self._socket.send(json.dumps(payload, separators=(",", ":")))
        self._last_activity = time.monotonic()

    async def select_competition(self, competition_id: int) -> None:
        await self.send({"type": "competition", "competitionId": competition_id})

    async def request_rounds(self) -> None:
        await self.send({"type": "result", "action": "rounds"})

    async def request_results(self, event_id: str, round_id: str) -> None:
        await self.send(
            {
                "type": "result",
                "action": "fetch",
                "params": {
                    "event": event_id,
                    "round": round_id,
                    "filter": "all",
                    "combine": False,
                },
            }
        )

    async def next_message(self):
        if self._socket is None:
            raise CubingChinaWebSocketError("CubingChina socket is not connected")
        try:
            raw = await self._socket.recv()
            self._last_activity = time.monotonic()
            message = json.loads(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CubingChinaWebSocketError(f"CubingChina socket receive failed: {exc}") from exc
        if message == "pong":
            return "pong"
        if not isinstance(message, dict):
            raise CubingChinaWebSocketError(
                f"Unexpected CubingChina message type: {type(message).__name__}"
            )
        if message.get("code") not in (None, 200):
            raise CubingChinaWebSocketError(f"CubingChina error response: {message}")
        return message

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(min(self.keepalive_seconds, 5))
                if time.monotonic() - self._last_activity >= self.keepalive_seconds:
                    await self.send("ping")
        except asyncio.CancelledError:
            raise
        except (OSError, websockets.WebSocketException):
            return

    async def close(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
        if self._socket:
            with contextlib.suppress(Exception):
                await self._socket.close()
        self._socket = None
        self._keepalive_task = None
