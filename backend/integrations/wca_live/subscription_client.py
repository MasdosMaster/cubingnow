import asyncio
import contextlib
import json
import logging
from collections import Counter
from itertools import count
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from .exceptions import WCALiveIntegrationError

logger = logging.getLogger(__name__)

FRAME_COUNTER_KEYS = (
    "frames_received",
    "bytes_received",
    "reply_frames",
    "heartbeat_replies",
    "subscription_data_frames",
    "subscription_messages_queued",
    "subscription_error_frames",
    "unknown_subscription_ids",
    "unexpected_frames",
    "malformed_frames",
)


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
        self._frame_counters = Counter({key: 0 for key in FRAME_COUNTER_KEYS})
        self._last_frame = None
        self._last_unexpected_frame = None

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

    @property
    def websocket_diagnostics(self) -> dict:
        """Return a JSON-safe summary suitable for health metadata and logs."""
        return {
            "counters": dict(self._frame_counters),
            "message_queue_size": self._messages.qsize(),
            "last_frame": self._last_frame,
            "last_unexpected_frame": self._last_unexpected_frame,
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
                self._frame_counters["frames_received"] += 1
                self._frame_counters["bytes_received"] += len(raw_message)
                try:
                    frame = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError) as exc:
                    self._frame_counters["malformed_frames"] += 1
                    logger.error(
                        "websocket_frame_invalid_json raw_type=%s raw_length=%d error=%s",
                        type(raw_message).__name__,
                        len(raw_message),
                        exc,
                    )
                    raise WCALiveIntegrationError("Invalid JSON in WCA Live Phoenix frame") from exc
                if not isinstance(frame, list) or len(frame) != 5:
                    self._frame_counters["malformed_frames"] += 1
                    self._last_unexpected_frame = {
                        "reason": "invalid_shape",
                        "frame_type": type(frame).__name__,
                        "frame_length": len(frame) if isinstance(frame, list) else None,
                    }
                    logger.error(
                        "websocket_frame_invalid_shape frame_type=%s frame_length=%s",
                        type(frame).__name__,
                        len(frame) if isinstance(frame, list) else "n/a",
                    )
                    raise WCALiveIntegrationError(
                        f"Invalid Phoenix frame received: {type(frame).__name__}"
                    )
                join_ref, reference, topic, event, payload = frame
                payload_keys = sorted(payload) if isinstance(payload, dict) else []
                frame_summary = {
                    "join_ref": str(join_ref) if join_ref is not None else None,
                    "reference": str(reference) if reference is not None else None,
                    "topic": str(topic),
                    "event": str(event),
                    "payload_type": type(payload).__name__,
                    "payload_keys": payload_keys,
                }
                if isinstance(payload, dict) and payload.get("subscriptionId"):
                    frame_summary["subscription_id"] = str(payload["subscriptionId"])
                self._last_frame = frame_summary
                logger.debug(
                    "websocket_frame_received topic=%s event=%s reference=%s payload_type=%s payload_keys=%s",
                    topic,
                    event,
                    reference,
                    type(payload).__name__,
                    payload_keys,
                )
                if event == "phx_reply":
                    self._frame_counters["reply_frames"] += 1
                    future = self._pending.get(str(reference))
                    if future and not future.done():
                        future.set_result(payload)
                    elif topic == self.HEARTBEAT_TOPIC:
                        self._frame_counters["heartbeat_replies"] += 1
                    else:
                        self._frame_counters["unexpected_frames"] += 1
                        self._last_unexpected_frame = {
                            **frame_summary,
                            "reason": "reply_without_pending_request",
                        }
                        logger.warning(
                            "websocket_reply_without_pending_request topic=%s reference=%s payload_keys=%s",
                            topic,
                            reference,
                            payload_keys,
                        )
                elif event in {"phx_error", "phx_close"}:
                    raise WCALiveIntegrationError(
                        f"WCA Live Phoenix {event} topic={topic}: {payload}"
                    )
                elif event == "subscription:data":
                    self._frame_counters["subscription_data_frames"] += 1
                    if not isinstance(payload, dict):
                        self._frame_counters["malformed_frames"] += 1
                        logger.error(
                            "subscription_payload_invalid payload_type=%s", type(payload).__name__
                        )
                        raise WCALiveIntegrationError(
                            "WCA Live subscription payload was not an object"
                        )
                    subscription_id = payload.get("subscriptionId", "")
                    round_id = self._subscription_to_round.get(subscription_id)
                    result = payload.get("result", {})
                    if not isinstance(result, dict):
                        self._frame_counters["malformed_frames"] += 1
                        logger.error(
                            "subscription_result_invalid subscription_id=%s result_type=%s",
                            subscription_id,
                            type(result).__name__,
                        )
                        raise WCALiveIntegrationError(
                            "WCA Live subscription result was not an object"
                        )
                    if result.get("errors"):
                        self._frame_counters["subscription_error_frames"] += 1
                        logger.error(
                            "round_subscription_payload_error round_id=%s errors=%s",
                            round_id or "unknown",
                            result["errors"],
                        )
                        continue
                    if not round_id:
                        self._frame_counters["unknown_subscription_ids"] += 1
                        self._last_unexpected_frame = {
                            **frame_summary,
                            "reason": "unknown_subscription_id",
                        }
                        logger.error(
                            "unknown_subscription_message subscription_id=%s", subscription_id
                        )
                        continue
                    self._frame_counters["subscription_messages_queued"] += 1
                    await self._messages.put((round_id, result.get("data", result)))
                else:
                    self._frame_counters["unexpected_frames"] += 1
                    self._last_unexpected_frame = {
                        **frame_summary,
                        "reason": "unexpected_topic_or_event",
                    }
                    logger.warning(
                        "websocket_frame_ignored topic=%s event=%s reference=%s payload_type=%s payload_keys=%s",
                        topic,
                        event,
                        reference,
                        type(payload).__name__,
                        payload_keys,
                    )
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
            await self._messages.put(WCALiveIntegrationError(f"WCA Live heartbeat failed: {exc}"))

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
