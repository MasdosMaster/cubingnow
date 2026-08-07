import asyncio
import logging
import random
from datetime import date

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.records.models import (
    IngestionRun,
    IngestionWorkerStatus,
    RecentRecordObservation,
    SubscriptionRound,
)

from .api_client import WCALiveAPIClient
from .discovery import discover_weekend_rounds
from .queries import ROUND_UPDATED_SUBSCRIPTION
from .subscription_client import WCALiveSubscriptionClient
from .subscription_ingestion import process_round_snapshot

logger = logging.getLogger(__name__)
METHOD = RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION


class SubscriptionSupervisor:
    def __init__(
        self,
        weekend_start: date,
        weekend_end: date,
        api_endpoint: str,
        websocket_endpoint: str,
        lookback_days: int = 7,
        discovery_interval: int = 900,
        catchup_minutes: int = 60,
        run: IngestionRun | None = None,
        api_client_factory=WCALiveAPIClient,
        subscription_client_factory=WCALiveSubscriptionClient,
    ):
        self.weekend_start = weekend_start
        self.weekend_end = weekend_end
        self.api_endpoint = api_endpoint
        self.websocket_endpoint = websocket_endpoint
        self.lookback_days = lookback_days
        self.discovery_interval = max(discovery_interval, 10)
        self.catchup_minutes = max(catchup_minutes, 0)
        self.run = run
        self.api_client_factory = api_client_factory
        self.subscription_client_factory = subscription_client_factory
        self._stopping = False

    async def run_forever(self) -> None:
        reconnect_attempt = 0
        while not self._stopping:
            if timezone.now().date() > self.weekend_end:
                logger.info("subscription_period_finished weekend_end=%s", self.weekend_end)
                await self._db(self._retire_all_targets)
                return
            try:
                targets = await self._discover()
                if not targets:
                    logger.warning("subscription_no_rounds_discovered")
                    await self._db(self._set_worker_disconnected, "No relevant rounds discovered")
                    await self._heartbeat_sleep(min(self.discovery_interval, 30))
                    continue
                client = self.subscription_client_factory(self.websocket_endpoint)
                async with client:
                    reconnect_attempt = 0
                    await self._db(self._set_worker_connected)
                    await self._subscribe_targets(client, targets)
                    await self._connected_loop(client, targets)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reconnect_attempt += 1
                await self._db(self._set_worker_disconnected, str(exc))
                ceiling = min(
                    settings.WCA_RETRY_MAX_SECONDS,
                    settings.WCA_RETRY_BASE_SECONDS * (2 ** min(reconnect_attempt - 1, 10)),
                )
                delay = random.uniform(0, max(ceiling, 0))
                logger.exception(
                    "subscription_reconnect_scheduled attempt=%d delay_seconds=%.2f",
                    reconnect_attempt,
                    delay,
                )
                await self._heartbeat_sleep(delay)

    def stop(self) -> None:
        self._stopping = True

    async def _connected_loop(self, client, targets: dict[str, SubscriptionRound]) -> None:
        message_task = asyncio.create_task(client.next_message())
        discovery_task = asyncio.create_task(asyncio.sleep(self.discovery_interval))
        heartbeat_task = asyncio.create_task(asyncio.sleep(30))
        try:
            while not self._stopping and timezone.now().date() <= self.weekend_end:
                done, _pending = await asyncio.wait(
                    {message_task, discovery_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if message_task in done:
                    round_id, data = message_task.result()
                    message_task = asyncio.create_task(client.next_message())
                    await self._db(self._record_message_received, round_id)
                    round_payload = data.get("roundUpdated") if isinstance(data, dict) else None
                    if not round_payload:
                        logger.error(
                            "subscription_payload_missing_round round_id=%s payload_type=%s",
                            round_id,
                            type(data).__name__,
                        )
                    else:
                        try:
                            await self._db(
                                process_round_snapshot,
                                round_id,
                                round_payload,
                                self.run,
                                self.catchup_minutes,
                            )
                        except Exception:
                            logger.exception(
                                "subscription_snapshot_processing_failed round_id=%s", round_id
                            )
                if discovery_task in done:
                    targets = await self._discover()
                    await self._retire_missing_subscriptions(client, targets)
                    await self._subscribe_targets(client, targets)
                    discovery_task = asyncio.create_task(asyncio.sleep(self.discovery_interval))
                if heartbeat_task in done:
                    await self._db(self._heartbeat, client.websocket_diagnostics)
                    heartbeat_task = asyncio.create_task(asyncio.sleep(30))
        finally:
            for task in (message_task, discovery_task, heartbeat_task):
                task.cancel()
            await asyncio.gather(
                message_task, discovery_task, heartbeat_task, return_exceptions=True
            )

    async def _discover(self) -> dict[str, SubscriptionRound]:
        client = self.api_client_factory(self.api_endpoint)
        targets, metadata = await self._db(
            discover_weekend_rounds,
            client,
            self.weekend_start,
            self.weekend_end,
            self.lookback_days,
        )
        return await self._db(self._persist_targets, targets, metadata)

    async def _subscribe_targets(self, client, targets: dict[str, SubscriptionRound]) -> None:
        for round_id in targets:
            if round_id in client.subscribed_round_ids:
                continue
            try:
                subscription_id = await client.subscribe_round(round_id, ROUND_UPDATED_SUBSCRIPTION)
                await self._db(self._mark_round_subscribed, round_id, subscription_id)
            except Exception as exc:
                logger.exception("round_subscription_failed round_id=%s", round_id)
                await self._db(self._mark_round_error, round_id, str(exc))
        await self._db(self._update_subscribed_count, len(client.subscribed_round_ids))

    async def _retire_missing_subscriptions(self, client, targets) -> None:
        for round_id in client.subscribed_round_ids - set(targets):
            try:
                await client.unsubscribe_round(round_id)
                await self._db(self._mark_round_retired, round_id)
            except Exception:
                logger.exception("round_unsubscribe_failed round_id=%s", round_id)

    async def _heartbeat_sleep(self, delay: float) -> None:
        remaining = max(delay, 0)
        while remaining > 0 and not self._stopping:
            interval = min(remaining, 30)
            await asyncio.sleep(interval)
            remaining -= interval
            await self._db(self._heartbeat)

    @staticmethod
    async def _db(function, *args):
        return await sync_to_async(function, thread_sensitive=True)(*args)

    def _persist_targets(self, targets, metadata) -> dict[str, SubscriptionRound]:
        metadata = {
            **metadata,
            "weekend_start": self.weekend_start.isoformat(),
            "weekend_end": self.weekend_end.isoformat(),
            "lookback_days": self.lookback_days,
            "discovery_interval_seconds": self.discovery_interval,
            "catchup_minutes": self.catchup_minutes,
        }
        active_ids = {target.round_id for target in targets}
        persisted = {}
        for target in targets:
            row, _created = SubscriptionRound.objects.update_or_create(
                round_id=target.round_id,
                defaults={
                    "wca_live_competition_id": target.wca_live_competition_id,
                    "wca_competition_id": target.wca_competition_id,
                    "competition_name": target.competition_name,
                    "competition_start_date": target.competition_start_date,
                    "competition_end_date": target.competition_end_date,
                    "event_id": target.event_id,
                    "event_name": target.event_name,
                    "round_number": target.round_number,
                    "round_name": target.round_name,
                    "active": True,
                },
            )
            if _created or row.subscription_status == SubscriptionRound.Status.RETIRED:
                row.subscription_status = SubscriptionRound.Status.DISCOVERED
                row.subscription_id = ""
                row.save(update_fields=["subscription_status", "subscription_id", "updated_at"])
            persisted[row.round_id] = row
        relevant = SubscriptionRound.objects.filter(
            competition_start_date__lte=self.weekend_end,
            competition_end_date__gte=self.weekend_start,
        )
        relevant.exclude(round_id__in=active_ids).update(
            active=False,
            subscription_status=SubscriptionRound.Status.RETIRED,
            subscription_id="",
        )
        now = timezone.now()
        worker = (
            IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).only("metadata").first()
        )
        if worker and worker.metadata.get("websocket"):
            metadata["websocket"] = worker.metadata["websocket"]
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=now,
            last_successful_discovery_at=now,
            last_error="",
            metadata=metadata,
        )
        return persisted

    @staticmethod
    def _set_worker_connected():
        now = timezone.now()
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=True,
            heartbeat_at=now,
            last_connection_at=now,
            last_error="",
        )

    @staticmethod
    def _set_worker_disconnected(error: str):
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=False,
            heartbeat_at=timezone.now(),
            last_error=error,
            subscribed_round_count=0,
        )
        SubscriptionRound.objects.filter(active=True).update(
            subscription_status=SubscriptionRound.Status.DISCOVERED,
            subscription_id="",
        )

    @staticmethod
    def _heartbeat(websocket_diagnostics: dict | None = None):
        queryset = IngestionWorkerStatus.objects.filter(ingestion_method=METHOD)
        if websocket_diagnostics is None:
            queryset.update(heartbeat_at=timezone.now())
            return
        status = queryset.only("metadata").first()
        if status is None:
            return
        status.metadata = {**status.metadata, "websocket": websocket_diagnostics}
        status.heartbeat_at = timezone.now()
        status.save(update_fields=["heartbeat_at", "metadata", "updated_at"])

    @staticmethod
    def _record_message_received(round_id: str):
        now = timezone.now()
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=now, last_message_at=now
        )
        SubscriptionRound.objects.filter(round_id=round_id).update(last_message_at=now)
        logger.info("subscription_snapshot_received round_id=%s", round_id)

    @staticmethod
    def _mark_round_subscribed(round_id: str, subscription_id: str):
        SubscriptionRound.objects.filter(round_id=round_id).update(
            subscription_status=SubscriptionRound.Status.SUBSCRIBED,
            subscription_id=subscription_id,
            last_subscribed_at=timezone.now(),
            last_error="",
        )

    @staticmethod
    def _mark_round_error(round_id: str, error: str):
        SubscriptionRound.objects.filter(round_id=round_id).update(
            subscription_status=SubscriptionRound.Status.ERROR,
            subscription_id="",
            last_error=error,
        )

    @staticmethod
    def _mark_round_retired(round_id: str):
        SubscriptionRound.objects.filter(round_id=round_id).update(
            active=False,
            subscription_status=SubscriptionRound.Status.RETIRED,
            subscription_id="",
        )

    @staticmethod
    def _update_subscribed_count(count: int):
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=timezone.now(), subscribed_round_count=count
        )

    def _retire_all_targets(self):
        SubscriptionRound.objects.filter(
            competition_start_date__lte=self.weekend_end,
            competition_end_date__gte=self.weekend_start,
        ).update(
            active=False,
            subscription_status=SubscriptionRound.Status.RETIRED,
            subscription_id="",
        )
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=False,
            subscribed_round_count=0,
            heartbeat_at=timezone.now(),
        )
