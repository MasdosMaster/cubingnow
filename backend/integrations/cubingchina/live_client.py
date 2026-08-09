import asyncio
import contextlib
import json
import time
from collections import Counter
from datetime import UTC, datetime

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
        self._reader_task = None
        self._messages = asyncio.Queue()
        self._counters = Counter(
            {
                "frames_received": 0,
                "bytes_received": 0,
                "messages_queued": 0,
                "messages_dequeued": 0,
                "pong_frames": 0,
                "error_frames": 0,
                "malformed_frames": 0,
            }
        )
        self._peak_message_queue_size = 0
        self._last_frame = None
        self._last_activity = time.monotonic()

    @property
    def websocket_diagnostics(self) -> dict:
        return {
            "counters": dict(self._counters),
            "message_queue_size": self._messages.qsize(),
            "peak_message_queue_size": self._peak_message_queue_size,
            "queue_capacity": None,
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "last_frame": self._last_frame,
        }

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
        self._reader_task = asyncio.create_task(self._reader_loop())
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
        message = await self._messages.get()
        if isinstance(message, Exception):
            raise message
        self._counters["messages_dequeued"] += 1
        return message

    async def _reader_loop(self) -> None:
        try:
            while True:
                raw = await self._socket.recv()
                self._last_activity = time.monotonic()
                self._counters["frames_received"] += 1
                self._counters["bytes_received"] += len(raw)
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    self._counters["malformed_frames"] += 1
                    raise CubingChinaWebSocketError(
                        "CubingChina returned invalid JSON"
                    ) from exc
                if message == "pong":
                    self._counters["pong_frames"] += 1
                    self._last_frame = {"type": "pong"}
                elif not isinstance(message, dict):
                    self._counters["malformed_frames"] += 1
                    raise CubingChinaWebSocketError(
                        f"Unexpected CubingChina message type: {type(message).__name__}"
                    )
                else:
                    self._last_frame = {
                        "type": str(message.get("type") or "unknown"),
                        "code": message.get("code"),
                        "data_type": type(message.get("data")).__name__,
                    }
                    if message.get("code") not in (None, 200):
                        self._counters["error_frames"] += 1
                        raise CubingChinaWebSocketError(
                            f"CubingChina returned error code {message.get('code')}"
                        )
                await self._messages.put(message)
                self._counters["messages_queued"] += 1
                self._peak_message_queue_size = max(
                    self._peak_message_queue_size, self._messages.qsize()
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate transport/protocol failures.
            error = (
                exc
                if isinstance(exc, CubingChinaWebSocketError)
                else CubingChinaWebSocketError(f"CubingChina socket receive failed: {exc}")
            )
            await self._messages.put(error)

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
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._socket:
            with contextlib.suppress(Exception):
                await self._socket.close()
        self._socket = None
        self._keepalive_task = None
        self._reader_task = None
