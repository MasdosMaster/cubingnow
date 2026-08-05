import asyncio
import json

from integrations.wca_live.queries import ROUND_UPDATED_SUBSCRIPTION
from integrations.wca_live.subscription_client import WCALiveSubscriptionClient


class FakeSocket:
    def __init__(self):
        self.received = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        join_ref, reference, topic, event, payload = self.sent[-1]
        if event == "phx_join":
            await self.received.put(
                json.dumps([join_ref, reference, topic, "phx_reply", {"status": "ok", "response": {}}])
            )
        elif event == "doc":
            subscription_id = f"subscription-{payload['variables']['id']}"
            await self.received.put(
                json.dumps(
                    [
                        join_ref,
                        reference,
                        topic,
                        "phx_reply",
                        {"status": "ok", "response": {"subscriptionId": subscription_id}},
                    ]
                )
            )

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.received.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self):
        self.closed = True
        await self.received.put(None)


def test_multiplexes_rounds_and_routes_full_snapshot_payloads():
    async def scenario():
        socket = FakeSocket()

        async def connect_factory(*_args, **_kwargs):
            return socket

        client = WCALiveSubscriptionClient(
            "wss://example.test/socket",
            heartbeat_interval=3600,
            connect_factory=connect_factory,
        )
        await client.connect()
        await client.subscribe_round("round-1", ROUND_UPDATED_SUBSCRIPTION)
        await client.subscribe_round("round-2", ROUND_UPDATED_SUBSCRIPTION)
        assert client.subscribed_round_ids == {"round-1", "round-2"}
        await socket.received.put(
            json.dumps(
                [
                    "1",
                    None,
                    "__absinthe__:control",
                    "subscription:data",
                    {
                        "subscriptionId": "subscription-round-2",
                        "result": {"data": {"roundUpdated": {"id": "round-2", "results": []}}},
                    },
                ]
            )
        )
        round_id, data = await asyncio.wait_for(client.next_message(), 1)
        assert round_id == "round-2"
        assert data["roundUpdated"]["results"] == []
        await client.close()
        assert socket.closed

    asyncio.run(scenario())


def test_reconnect_requires_and_accepts_resubscription_without_shared_state():
    async def scenario():
        sockets = []

        async def connect_factory(*_args, **_kwargs):
            socket = FakeSocket()
            sockets.append(socket)
            return socket

        first = WCALiveSubscriptionClient(
            "wss://example.test/socket", heartbeat_interval=3600, connect_factory=connect_factory
        )
        await first.connect()
        first_id = await first.subscribe_round("round-1", ROUND_UPDATED_SUBSCRIPTION)
        await first.close()

        second = WCALiveSubscriptionClient(
            "wss://example.test/socket", heartbeat_interval=3600, connect_factory=connect_factory
        )
        await second.connect()
        second_id = await second.subscribe_round("round-1", ROUND_UPDATED_SUBSCRIPTION)
        assert first_id == second_id == "subscription-round-1"
        assert len(sockets) == 2
        assert sum(frame[3] == "doc" for frame in sockets[1].sent) == 1
        await second.close()

    asyncio.run(scenario())
