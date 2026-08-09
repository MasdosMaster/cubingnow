import asyncio
import logging
import random
from datetime import UTC, datetime, time, timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.records.models import (
    CubingChinaCompetitionTarget,
    CubingChinaRoundTarget,
    IngestionRun,
    IngestionWorkerStatus,
    RecentRecordObservation,
)
from integrations.wca.record_validation import (
    RECORDS_PATH,
    fetch_wca_records,
    refresh_wca_record_validations,
)

from .live_client import CubingChinaWebSocketClient
from .live_discovery import discover_live_competitions
from .live_ingestion import process_result_update, process_round_snapshot, store_live_event
from .scraper_client import CubingChinaScraperClient

logger = logging.getLogger(__name__)
METHOD = RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET


class CubingChinaLiveSupervisor:
    def __init__(
        self,
        base_url: str,
        websocket_endpoint: str,
        wca_base_url: str | None = None,
        discovery_interval: int = 900,
        lookback_days: int = 1,
        lookahead_days: int = 7,
        completion_grace_minutes: int = 720,
        max_connections: int = 10,
        retry_base_seconds: float = 1,
        retry_max_seconds: float = 60,
        keepalive_seconds: float = 55,
        run: IngestionRun | None = None,
        scraper_client_factory=CubingChinaScraperClient,
        websocket_client_factory=CubingChinaWebSocketClient,
    ):
        self.base_url = base_url
        self.websocket_endpoint = websocket_endpoint
        self.wca_base_url = wca_base_url
        self.discovery_interval = max(discovery_interval, 10)
        self.lookback_days = max(lookback_days, 0)
        self.lookahead_days = max(lookahead_days, 0)
        self.completion_grace_minutes = max(completion_grace_minutes, 0)
        self.max_connections = max(max_connections, 1)
        self.retry_base_seconds = max(retry_base_seconds, 0)
        self.retry_max_seconds = max(retry_max_seconds, self.retry_base_seconds)
        self.keepalive_seconds = max(keepalive_seconds, 1)
        self.run = run
        self.scraper_client_factory = scraper_client_factory
        self.websocket_client_factory = websocket_client_factory
        self._stopping = False
        self._collectors: dict[int, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(self.max_connections)

    def stop(self) -> None:
        self._stopping = True

    async def run_forever(self) -> None:
        try:
            while not self._stopping:
                if self.wca_base_url:
                    try:
                        await self._refresh_wca_records()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("wca_record_validation_refresh_failed")
                try:
                    target_ids = await self._discover()
                    await self._reconcile_collectors(target_ids)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("cubingchina_discovery_cycle_failed")
                    await self._db(self._set_worker_error, str(exc))
                await self._sleep_with_heartbeat(self.discovery_interval)
        finally:
            self._stopping = True
            for task in self._collectors.values():
                task.cancel()
            if self._collectors:
                await asyncio.gather(*self._collectors.values(), return_exceptions=True)
            self._collectors.clear()
            await self._db(self._mark_all_disconnected)

    async def _refresh_wca_records(self) -> None:
        payload = await asyncio.to_thread(fetch_wca_records, self.wca_base_url)
        source_url = f"{self.wca_base_url.rstrip('/')}{RECORDS_PATH}"
        snapshot = await self._db(
            refresh_wca_record_validations,
            payload,
            source_url=source_url,
        )
        logger.info(
            "wca_record_validation_refreshed snapshot_id=%s records=%s",
            snapshot.pk,
            snapshot.record_count,
        )

    async def run_discovery_once(self) -> set[int]:
        """Run one discovery/reconciliation pass for tests and operations."""
        target_ids = await self._discover()
        await self._reconcile_collectors(target_ids)
        return target_ids

    async def _discover(self) -> set[int]:
        def fetch():
            with self.scraper_client_factory(base_url=self.base_url) as client:
                return discover_live_competitions(
                    client,
                    timezone.now().date(),
                    self.lookback_days,
                    self.lookahead_days,
                )

        entries, metadata = await asyncio.to_thread(fetch)
        return await self._db(self._persist_discovery, entries, metadata)

    async def _reconcile_collectors(self, target_ids: set[int]) -> None:
        for target_id, task in list(self._collectors.items()):
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("cubingchina_collector_ended target_id=%s", target_id)
                self._collectors.pop(target_id, None)
            elif target_id not in target_ids:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._collectors.pop(target_id, None)
        for target_id in sorted(target_ids):
            if target_id not in self._collectors:
                self._collectors[target_id] = asyncio.create_task(
                    self._collector_loop(target_id),
                    name=f"cubingchina-competition-{target_id}",
                )

    async def _collector_loop(self, target_id: int) -> None:
        reconnect_attempt = 0
        while not self._stopping and await self._db(self._target_is_collectable, target_id):
            client = None
            try:
                async with self._semaphore:
                    if not await self._db(self._target_is_collectable, target_id):
                        return
                    client = self.websocket_client_factory(
                        self.websocket_endpoint,
                        keepalive_seconds=self.keepalive_seconds,
                    )
                    async with client:
                        reconnect_attempt = 0
                        target = await self._db(self._mark_connected, target_id)
                        telemetry_task = asyncio.create_task(
                            self._telemetry_loop(target_id, client),
                            name=f"cubingchina-telemetry-{target_id}",
                        )
                        try:
                            await self._connected_session(client, target)
                        finally:
                            telemetry_task.cancel()
                            await asyncio.gather(telemetry_task, return_exceptions=True)
                            await self._db(
                                self._persist_websocket_diagnostics,
                                target_id,
                                self._client_diagnostics(client),
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reconnect_attempt += 1
                logger.exception(
                    "cubingchina_competition_reconnect target_id=%s attempt=%d",
                    target_id,
                    reconnect_attempt,
                )
                await self._db(self._mark_competition_error, target_id, str(exc))
                ceiling = min(
                    self.retry_max_seconds,
                    self.retry_base_seconds * (2 ** min(reconnect_attempt - 1, 10)),
                )
                await self._sleep_with_heartbeat(random.uniform(0, max(ceiling, 0)))
            finally:
                if client is not None:
                    await self._db(self._mark_disconnected, target_id)

    async def _telemetry_loop(self, target_id: int, client) -> None:
        while not self._stopping:
            await self._db(
                self._persist_websocket_diagnostics,
                target_id,
                self._client_diagnostics(client),
            )
            await asyncio.sleep(settings.WORKER_TELEMETRY_INTERVAL_SECONDS)

    @staticmethod
    def _client_diagnostics(client) -> dict:
        diagnostics = getattr(client, "websocket_diagnostics", {})
        return diagnostics if isinstance(diagnostics, dict) else {}

    async def _connected_session(self, client, target: dict) -> None:
        users = {}
        await client.select_competition(target["cubingchina_id"])
        while not users:
            message = await asyncio.wait_for(client.next_message(), timeout=30)
            if message == "pong":
                continue
            if message.get("type") == "users":
                users = message.get("data") or {}
                await self._db(store_live_event, message, "users", self.run)
            else:
                await self._handle_message(target["id"], message, users)

        await client.request_rounds()
        rounds = await self._db(self._round_descriptors, target["id"])
        for source_round in rounds:
            await client.request_results(source_round["event_id"], source_round["round_id"])
            while True:
                message = await asyncio.wait_for(client.next_message(), timeout=45)
                if message == "pong":
                    continue
                if message.get("type") == "result.all":
                    await self._db(
                        process_round_snapshot,
                        source_round["id"],
                        message.get("data") or [],
                        users,
                        self.run,
                    )
                    break
                users = await self._handle_message(target["id"], message, users)

        while not self._stopping:
            message = await client.next_message()
            if message == "pong":
                await self._db(self._heartbeat)
                continue
            users = await self._handle_message(target["id"], message, users)

    async def _handle_message(self, target_id: int, message: dict, users: dict) -> dict:
        message_type = str(message.get("type") or "")
        data = message.get("data")
        if message_type == "users":
            users = data or {}
            await self._db(store_live_event, message, "users", self.run)
        elif message_type in {"result.new", "result.update"}:
            if not isinstance(data, dict):
                raise ValueError(f"{message_type} data must be an object")
            round_target_id = await self._db(
                self._find_round_target,
                target_id,
                str(data.get("e") or ""),
                str(data.get("r") or ""),
            )
            if not round_target_id:
                raise ValueError(f"No target round for {data.get('e')}/{data.get('r')}")
            await self._db(
                process_result_update,
                round_target_id,
                data,
                users,
                message_type,
                self.run,
            )
        elif message_type in {"round.all", "round.update"}:
            await self._db(self._process_round_event, target_id, message_type, data)
            await self._db(
                store_live_event,
                {"competitionTargetId": target_id, "data": data},
                message_type,
                self.run,
            )
        elif message_type == "record.current":
            await self._db(
                store_live_event,
                {"competitionTargetId": target_id, "data": data},
                message_type,
                self.run,
            )
        elif message_type == "result.all":
            if isinstance(data, list) and data:
                event_id = str(data[0].get("e") or "")
                round_id = str(data[0].get("r") or "")
                round_target_id = await self._db(
                    self._find_round_target, target_id, event_id, round_id
                )
                if not round_target_id:
                    raise ValueError(f"No target round for {event_id}/{round_id}")
                await self._db(
                    process_round_snapshot,
                    round_target_id,
                    data,
                    users,
                    self.run,
                )
            else:
                # An empty unsolicited snapshot has no round identity. Empty
                # responses to our own fetches are handled in the sequential loop.
                logger.warning(
                    "cubingchina_unassociated_empty_snapshot target_id=%s", target_id
                )
        await self._db(self._record_message, target_id)
        return users

    async def _sleep_with_heartbeat(self, delay: float) -> None:
        remaining = max(delay, 0)
        while remaining > 0 and not self._stopping:
            interval = min(remaining, 30)
            await asyncio.sleep(interval)
            remaining -= interval
            await self._db(self._heartbeat)

    @staticmethod
    async def _db(function, *args, **kwargs):
        return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)

    def _persist_discovery(self, entries, metadata) -> set[int]:
        now = timezone.now()
        collectable_ids = set()
        seen_slugs = {entry.slug for entry in entries}
        for entry in entries:
            if not entry.detail_verified:
                row = CubingChinaCompetitionTarget.objects.filter(slug=entry.slug).first()
                if row is not None:
                    row.last_discovered_at = now
                    row.last_error = entry.error
                    row.save(
                        update_fields=[
                            "last_discovered_at",
                            "last_error",
                            "updated_at",
                        ]
                    )
                continue
            ready = entry.live is not None
            collectable = ready and self._entry_is_collectable(entry, now)
            status = (
                CubingChinaCompetitionTarget.Status.ACTIVE
                if collectable
                else CubingChinaCompetitionTarget.Status.PENDING
            )
            common_defaults = {
                "cubingchina_id": entry.live.cubingchina_id if ready else None,
                "wca_competition_id": entry.wca_competition_id,
                "competition_name": entry.competition_name,
                "competition_start_date": entry.competition_start_date,
                "competition_end_date": entry.competition_end_date,
                "status": status,
                "active": True,
                "last_discovered_at": now,
                "last_error": entry.error,
            }
            if ready:
                row, _created = CubingChinaCompetitionTarget.objects.update_or_create(
                    slug=entry.slug,
                    defaults=common_defaults,
                )
            else:
                pending_defaults = {**common_defaults, "cubingchina_id": None}
                row, _created = CubingChinaCompetitionTarget.objects.get_or_create(
                    slug=entry.slug,
                    defaults=pending_defaults,
                )
                if not _created:
                    if entry.wca_competition_id:
                        row.wca_competition_id = entry.wca_competition_id
                    if entry.competition_name:
                        row.competition_name = entry.competition_name
                    row.competition_start_date = entry.competition_start_date
                    row.competition_end_date = entry.competition_end_date
                    row.last_discovered_at = now
                    row.last_error = entry.error
                    row.save(
                        update_fields=[
                            "wca_competition_id",
                            "competition_name",
                            "competition_start_date",
                            "competition_end_date",
                            "last_discovered_at",
                            "last_error",
                            "updated_at",
                        ]
                    )
                    collectable = bool(row.cubingchina_id) and self._entry_is_collectable(
                        entry, now
                    )
            if ready:
                active_round_keys = set()
                for source_round in entry.live.rounds:
                    active_round_keys.add((source_round.event_id, source_round.round_id))
                    CubingChinaRoundTarget.objects.update_or_create(
                        competition=row,
                        event_id=source_round.event_id,
                        round_id=source_round.round_id,
                        defaults={
                            "event_name": source_round.event_name,
                            "round_number": source_round.round_number,
                            "round_name": source_round.round_name,
                            "format": source_round.format,
                            "cutoff": source_round.cutoff,
                            "time_limit": source_round.time_limit,
                            "source_status": source_round.status,
                            "active": True,
                            "last_error": "",
                        },
                    )
                for round_target in row.rounds.filter(active=True):
                    if (round_target.event_id, round_target.round_id) not in active_round_keys:
                        round_target.active = False
                        round_target.save(update_fields=["active", "updated_at"])
            if collectable:
                collectable_ids.add(row.pk)

        for existing in CubingChinaCompetitionTarget.objects.filter(active=True):
            retirement_at = datetime.combine(
                existing.competition_end_date + timedelta(days=1), time.min, tzinfo=UTC
            ) + timedelta(minutes=self.completion_grace_minutes)
            if existing.slug not in seen_slugs or now > retirement_at:
                existing.active = False
                existing.connected = False
                existing.status = CubingChinaCompetitionTarget.Status.RETIRED
                existing.save(
                    update_fields=["active", "connected", "status", "updated_at"]
                )
                existing.rounds.filter(active=True).update(active=False)
            elif (
                existing.cubingchina_id is not None
                and self._target_dates_are_collectable(existing, now)
            ):
                # Discovery is advisory. Keep a known live target running through a
                # transient index/detail/live-page failure and retire it only by age.
                collectable_ids.add(existing.pk)
        collectable_ids &= set(
            CubingChinaCompetitionTarget.objects.filter(
                pk__in=collectable_ids, active=True
            ).values_list("pk", flat=True)
        )
        counts = self._target_counts()
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=now,
            last_successful_discovery_at=now,
            last_error="",
            subscribed_round_count=counts["target_round_count"],
            metadata={
                **metadata,
                **counts,
                "lookback_days": self.lookback_days,
                "lookahead_days": self.lookahead_days,
                "completion_grace_minutes": self.completion_grace_minutes,
                "discovery_interval_seconds": self.discovery_interval,
                "max_connections": self.max_connections,
            },
        )
        return collectable_ids

    def _entry_is_collectable(self, entry, now) -> bool:
        ends = datetime.combine(
            entry.competition_end_date + timedelta(days=1), time.min, tzinfo=UTC
        ) + timedelta(minutes=self.completion_grace_minutes)
        return now <= ends

    def _target_dates_are_collectable(self, target, now) -> bool:
        ends = datetime.combine(
            target.competition_end_date + timedelta(days=1), time.min, tzinfo=UTC
        ) + timedelta(minutes=self.completion_grace_minutes)
        return now <= ends

    @staticmethod
    def _target_is_collectable(target_id: int) -> bool:
        return CubingChinaCompetitionTarget.objects.filter(
            pk=target_id,
            active=True,
            cubingchina_id__isnull=False,
        ).exists()

    @staticmethod
    def _round_descriptors(target_id: int) -> list[dict]:
        return list(
            CubingChinaRoundTarget.objects.filter(competition_id=target_id, active=True)
            .order_by("event_id", "round_number", "round_id")
            .values("id", "event_id", "round_id")
        )

    @staticmethod
    def _find_round_target(target_id: int, event_id: str, round_id: str) -> int | None:
        return (
            CubingChinaRoundTarget.objects.filter(
                competition_id=target_id,
                event_id=event_id,
                round_id=round_id,
                active=True,
            ).values_list("pk", flat=True).first()
        )

    @staticmethod
    def _mark_connected(target_id: int) -> dict:
        now = timezone.now()
        CubingChinaCompetitionTarget.objects.filter(pk=target_id).update(
            connected=True,
            status=CubingChinaCompetitionTarget.Status.ACTIVE,
            last_connected_at=now,
            last_error="",
        )
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=True,
            heartbeat_at=now,
            last_connection_at=now,
        )
        return CubingChinaCompetitionTarget.objects.values(
            "id", "cubingchina_id", "slug"
        ).get(pk=target_id)

    @staticmethod
    def _persist_websocket_diagnostics(target_id: int, diagnostics: dict) -> None:
        now = timezone.now()
        CubingChinaCompetitionTarget.objects.filter(pk=target_id).update(
            websocket_diagnostics=diagnostics
        )
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=now
        )

    @staticmethod
    def _mark_disconnected(target_id: int) -> None:
        CubingChinaCompetitionTarget.objects.filter(pk=target_id).update(connected=False)
        connected = CubingChinaCompetitionTarget.objects.filter(connected=True).count()
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=connected > 0,
            heartbeat_at=timezone.now(),
        )

    @staticmethod
    def _mark_competition_error(target_id: int, error: str) -> None:
        CubingChinaCompetitionTarget.objects.filter(pk=target_id).update(
            connected=False,
            status=CubingChinaCompetitionTarget.Status.ERROR,
            last_error=error,
        )
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=timezone.now(), last_error=error
        )

    @staticmethod
    def _record_message(target_id: int) -> None:
        now = timezone.now()
        CubingChinaCompetitionTarget.objects.filter(pk=target_id).update(last_message_at=now)
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=now, last_message_at=now
        )

    @staticmethod
    def _process_round_event(target_id: int, message_type: str, data) -> None:
        rows = data if message_type == "round.all" and isinstance(data, list) else [data]
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_id = str(row.get("e") or row.get("event") or "")
            round_id = str(row.get("i") or row.get("id") or "")
            if not event_id or not round_id:
                continue
            defaults = {}
            for source, destination in (
                ("s", "source_status"),
                ("f", "format"),
                ("co", "cutoff"),
                ("tl", "time_limit"),
                ("name", "round_name"),
            ):
                if source in row:
                    defaults[destination] = row[source]
            if defaults:
                CubingChinaRoundTarget.objects.filter(
                    competition_id=target_id, event_id=event_id, round_id=round_id
                ).update(**defaults)

    @staticmethod
    def _heartbeat() -> None:
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=timezone.now()
        )

    @staticmethod
    def _set_worker_error(error: str) -> None:
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            heartbeat_at=timezone.now(), last_error=error
        )

    @staticmethod
    def _target_counts() -> dict:
        return {
            "connected_competition_count": CubingChinaCompetitionTarget.objects.filter(
                connected=True
            ).count(),
            "target_competition_count": CubingChinaCompetitionTarget.objects.filter(
                active=True
            ).count(),
            "pending_competition_count": CubingChinaCompetitionTarget.objects.filter(
                active=True, status=CubingChinaCompetitionTarget.Status.PENDING
            ).count(),
            "target_round_count": CubingChinaRoundTarget.objects.filter(
                active=True, competition__active=True
            ).count(),
        }

    @staticmethod
    def _mark_all_disconnected() -> None:
        CubingChinaCompetitionTarget.objects.filter(connected=True).update(connected=False)
        IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
            connected=False, heartbeat_at=timezone.now()
        )
