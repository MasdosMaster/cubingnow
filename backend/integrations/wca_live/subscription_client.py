import asyncio
import contextlib
import json
import logging
from itertools import count
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from .exceptions import WCALiveIntegrationError

logger = logging.getLogger(__name__)


class WCALiveSubscriptionClient:
    """Multiplexed Phoenix v2 / Absinthe client for WCA Live subscriptions."""

    CONTROL_TOPIC = "__absinthe__:control"
    HEARTBEAT_TOPIC = "phoenix"

    def __init__(
        self,
        endpoint: str = "wss://live.worldcubeassociation.org/socket/websocket",
        heartbeat_interval: float = 25.0,
        reply_timeout: float = 20.0,
        connect_factory=None,
    ):
        self.endpoint = self._normalize_endpoint(endpoint)
        self.heartbeat_interval = heartbeat_interval
        self.reply_timeout = reply_timeout
        self._connect_factory = connect_factory or websockets.connect
        self._references = count(1)
        self._socket = None
        self._join_ref = None
        self._reader_task = None
        self._heartbeat_task = None
        self._pending = {}
        self._messages = asyncio.Queue()
        self._subscription_to_round = {}
        self._round_to_subscription = {}

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        parts = urlsplit(endpoint)
        path = parts.path.rstrip("/")
        if path.endswith("/socket"):
            path += "/websocket"
        query = dict(parse_qsl(parts.query))
        query.setdefault("vsn", "2.0.0")
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))

    @staticmethod
    def _message(join_ref, ref, topic, event, payload) -> str:
        return json.dumps([join_ref, str(ref), topic, event, payload])

    @property
    def subscribed_round_ids(self) -> set[str]:
        return set(self._round_to_subscription)

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
            max_size=8 * 1024 * 1024,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        reference = str(next(self._references))
        self._join_ref = reference
        reply = await self._send_and_wait(
            reference,
            reference,
            self.CONTROL_TOPIC,
            "phx_join",
            {},
        )
        self._require_ok(reply, "control channel join")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("subscription_connection_established endpoint=%s", self.endpoint)

    async def subscribe_round(self, round_id: str, query: str) -> str:
        round_id = str(round_id)
        existing = self._round_to_subscription.get(round_id)
        if existing:
            return existing
        reference = str(next(self._references))
        reply = await self._send_and_wait(
            self._join_ref,
            reference,
            self.CONTROL_TOPIC,
            "doc",
            {"query": query, "variables": {"id": round_id}},
        )
        response = self._require_ok(reply, f"round {round_id} subscription")
        subscription_id = response.get("subscriptionId")
        if not subscription_id:
            raise WCALiveIntegrationError(
                f"WCA Live did not return a subscriptionId for round {round_id}"
            )
        self._subscription_to_round[subscription_id] = round_id
        self._round_to_subscription[round_id] = subscription_id
        logger.info(
            "round_subscription_established round_id=%s subscription_id=%s",
            round_id,
            subscription_id,
        )
        return subscription_id

    async def unsubscribe_round(self, round_id: str) -> None:
        round_id = str(round_id)
        subscription_id = self._round_to_subscription.get(round_id)
        if not subscription_id:
            return
        reference = str(next(self._references))
        reply = await self._send_and_wait(
            self._join_ref,
            reference,
            self.CONTROL_TOPIC,
            "unsubscribe",
            {"subscriptionId": subscription_id},
        )
        self._require_ok(reply, f"round {round_id} unsubscribe")
        self._round_to_subscription.pop(round_id, None)
        self._subscription_to_round.pop(subscription_id, None)

    async def next_message(self) -> tuple[str, dict]:
        message = await self._messages.get()
        if isinstance(message, Exception):
            raise message
        return message

    async def _send_and_wait(self, join_ref, ref, topic, event, payload) -> dict:
        if self._socket is None:
            raise WCALiveIntegrationError("WCA Live subscription socket is not connected")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[str(ref)] = future
        try:
            await self._socket.send(self._message(join_ref, ref, topic, event, payload))
            return await asyncio.wait_for(future, timeout=self.reply_timeout)
        finally:
            self._pending.pop(str(ref), None)

    @staticmethod
    def _require_ok(reply: dict, action: str) -> dict:
        if reply.get("status") != "ok":
            raise WCALiveIntegrationError(
                f"WCA Live {action} failed: {reply.get('response', reply)}"
            )
        return reply.get("response", {})

    async def _reader_loop(self) -> None:
        try:
            async for raw_message in self._socket:
                frame = json.loads(raw_message)
                if not isinstance(frame, list) or len(frame) != 5:
                    raise WCALiveIntegrationError(
                        f"Invalid Phoenix frame received: {type(frame).__name__}"
                    )
                _join_ref, reference, topic, event, payload = frame
                if event == "phx_reply":
                    future = self._pending.get(str(reference))
                    if future and not future.done():
                        future.set_result(payload)
                elif event in {"phx_error", "phx_close"}:
                    raise WCALiveIntegrationError(
                        f"WCA Live Phoenix {event} topic={topic}: {payload}"
                    )
                elif topic == self.CONTROL_TOPIC and event == "subscription:data":
                    subscription_id = payload.get("subscriptionId", "")
                    round_id = self._subscription_to_round.get(subscription_id)
                    result = payload.get("result", {})
                    if result.get("errors"):
                        logger.error(
                            "round_subscription_payload_error round_id=%s errors=%s",
                            round_id or "unknown",
                            result["errors"],
                        )
                        continue
                    if not round_id:
                        logger.error(
                            "unknown_subscription_message subscription_id=%s", subscription_id
                        )
                        continue
                    await self._messages.put((round_id, result.get("data", result)))
            raise WCALiveIntegrationError("WCA Live closed the subscription socket")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate transport/protocol failures.
            error = (
                exc
                if isinstance(exc, WCALiveIntegrationError)
                else WCALiveIntegrationError(f"WCA Live socket reader failed: {exc}")
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            await self._messages.put(error)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                if self._socket is None:
                    return
                reference = str(next(self._references))
                await self._socket.send(
                    self._message(None, reference, self.HEARTBEAT_TOPIC, "heartbeat", {})
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any heartbeat transport failure.
            await self._messages.put(
                WCALiveIntegrationError(f"WCA Live heartbeat failed: {exc}")
            )

    async def close(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._socket:
            with contextlib.suppress(Exception):
                await self._socket.close()
        self._socket = None
        self._reader_task = None
        self._heartbeat_task = None
        self._pending.clear()
        self._subscription_to_round.clear()
        self._round_to_subscription.clear()
