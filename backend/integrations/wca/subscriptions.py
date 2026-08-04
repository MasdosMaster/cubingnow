import asyncio
import json
from collections.abc import AsyncIterator
from itertools import count
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets

from .exceptions import WCAIntegrationError


class WCALiveSubscriptionClient:
    """Minimal Phoenix/Absinthe subscription client used by WCA Live.

    The GraphQL document is configuration because WCA Live's public schema can
    evolve independently of CubeRecord. Payload mapping remains outside this client.
    """

    CONTROL_TOPIC = "__absinthe__:control"

    def __init__(self, endpoint: str, reconnect_delay: float = 5.0):
        self.endpoint = self._with_version(endpoint)
        self.reconnect_delay = reconnect_delay
        self._references = count(1)

    @staticmethod
    def _with_version(endpoint: str) -> str:
        parts = urlsplit(endpoint)
        query = dict(parse_qsl(parts.query))
        query.setdefault("vsn", "2.0.0")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _message(join_ref, ref, topic, event, payload):
        return json.dumps([join_ref, str(ref), topic, event, payload])

    async def subscribe(self, query: str, variables: dict | None = None) -> AsyncIterator[dict]:
        while True:
            try:
                async with websockets.connect(self.endpoint, ping_interval=30) as socket:
                    join_ref = str(next(self._references))
                    await socket.send(
                        self._message(join_ref, join_ref, self.CONTROL_TOPIC, "phx_join", {})
                    )
                    subscription_ref = next(self._references)
                    await socket.send(
                        self._message(
                            join_ref,
                            subscription_ref,
                            self.CONTROL_TOPIC,
                            "doc",
                            {"query": query, "variables": variables or {}},
                        )
                    )
                    async for raw_message in socket:
                        _, _, topic, event, payload = json.loads(raw_message)
                        if event == "phx_error":
                            raise WCAIntegrationError(f"WCA Live subscription error: {payload}")
                        if topic == self.CONTROL_TOPIC and event == "subscription:data":
                            result = payload.get("result", payload)
                            if result.get("errors"):
                                raise WCAIntegrationError(
                                    f"WCA Live subscription errors: {result['errors']}"
                                )
                            yield result.get("data", result)
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException, json.JSONDecodeError):
                await asyncio.sleep(self.reconnect_delay)

