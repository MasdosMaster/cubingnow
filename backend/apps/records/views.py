from datetime import datetime, timedelta
from time import perf_counter

from django.conf import settings
from django.db.models import Count, F, Max, Min, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from apps.notifications.models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationEvent,
)
from integrations.weekend_window import resolve_weekend_window

from .models import (
    Achievement,
    CanonicalResult,
    ClassificationScopeWork,
    CubingChinaCompetitionTarget,
    CubingChinaRoundTarget,
    IngestionWorkerStatus,
    RecentRecordObservation,
    ResultObservation,
    SourceObservation,
    SubscriptionRound,
)
from .serializers import AchievementSerializer, RecentRecordObservationSerializer


class RecordViewSet(ReadOnlyModelViewSet):
    """Public WR/CR/NR/PR projection over canonical achievements."""

    serializer_class = AchievementSerializer

    def get_queryset(self):
        queryset = (
            Achievement.objects.select_related("result", "qualification")
            .prefetch_related("result__observations")
            .filter(
                status=Achievement.Status.ACTIVE,
                qualification__show_on_homepage=True,
            )
        )
        level = self.request.query_params.get("level")
        query = self.request.query_params.get("q")
        if level:
            queryset = queryset.filter(type=level.upper())
        if query:
            queryset = queryset.filter(
                Q(result__competitor_name__icontains=query)
                | Q(result__competitor_wca_id__icontains=query)
                | Q(result__event_name__icontains=query)
                | Q(result__competition_name__icontains=query)
            )
        return queryset


class RecentRecordObservationViewSet(ReadOnlyModelViewSet):
    serializer_class = RecentRecordObservationSerializer

    def get_queryset(self):
        other_pipeline = (
            RecentRecordObservation.objects.filter(canonical_key=OuterRef("canonical_key"))
            .exclude(ingestion_method=OuterRef("ingestion_method"))
            .order_by("detected_at")
        )
        queryset = RecentRecordObservation.objects.annotate(
            other_pipeline_detected_at=Subquery(other_pipeline.values("detected_at")[:1])
        )
        source = self.request.query_params.get("source")
        level = self.request.query_params.get("level")
        status = self.request.query_params.get(
            "status", RecentRecordObservation.Status.ACTIVE
        )
        query = self.request.query_params.get("q")
        if source:
            queryset = queryset.filter(ingestion_method=source)
        if level:
            queryset = queryset.filter(record_level=level.upper())
        if status:
            queryset = queryset.filter(status=status)
        if query:
            queryset = queryset.filter(
                Q(competitor_name__icontains=query)
                | Q(competitor_wca_id__icontains=query)
                | Q(event_name__icontains=query)
                | Q(competition_name__icontains=query)
            )
        return queryset


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def _worker_payload(method: str) -> dict:
    status = IngestionWorkerStatus.objects.filter(ingestion_method=method).first()
    observations = RecentRecordObservation.objects.filter(ingestion_method=method).count()
    if status is None:
        payload = {
            "status": "unknown",
            "heartbeat_at": None,
            "last_started_at": None,
            "last_stopped_at": None,
            "last_poll_started_at": None,
            "last_successful_poll_at": None,
            "last_connection_at": None,
            "last_message_at": None,
            "last_successful_discovery_at": None,
            "last_successful_snapshot_at": None,
            "subscribed_round_count": 0,
            "last_error": "",
            "observations_count": observations,
            "metadata": {},
        }
        if method in {
            RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
            RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET,
        }:
            payload["connected"] = False
        return payload
    if method == RecentRecordObservation.IngestionMethod.API_POLLING:
        stale_after = max(settings.WCA_API_POLL_INTERVAL_SECONDS * 3, 180)
    else:
        stale_after = 90
    fresh = bool(
        status.heartbeat_at
        and status.heartbeat_at >= timezone.now() - timedelta(seconds=stale_after)
    )
    running = "running" if status.is_running and fresh else "unknown"
    if not status.is_running and status.last_stopped_at:
        running = "stopped"
    payload = {
        "status": running,
        "heartbeat_at": _iso(status.heartbeat_at),
        "last_started_at": _iso(status.last_started_at),
        "last_stopped_at": _iso(status.last_stopped_at),
        "last_poll_started_at": _iso(status.last_poll_started_at),
        "last_successful_poll_at": _iso(status.last_successful_poll_at),
        "last_connection_at": _iso(status.last_connection_at),
        "last_message_at": _iso(status.last_message_at),
        "last_successful_discovery_at": _iso(status.last_successful_discovery_at),
        "last_successful_snapshot_at": _iso(status.last_successful_snapshot_at),
        "subscribed_round_count": status.subscribed_round_count,
        "last_error": status.last_error,
        "observations_count": observations,
        "metadata": status.metadata,
    }
    if method in {
        RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
        RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET,
    }:
        payload["connected"] = status.connected
    return payload


def _websocket_queue_payload(diagnostics: dict | None) -> dict:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    counters = diagnostics.get("counters")

    def nonnegative_int(value) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    return {
        "message_queue_size": nonnegative_int(diagnostics.get("message_queue_size")),
        "peak_message_queue_size": nonnegative_int(
            diagnostics.get("peak_message_queue_size")
        ),
        "queue_capacity": diagnostics.get("queue_capacity"),
        "captured_at": diagnostics.get("captured_at"),
        "counters": counters if isinstance(counters, dict) else {},
        "last_frame": (
            diagnostics.get("last_frame")
            if isinstance(diagnostics.get("last_frame"), dict)
            else None
        ),
    }


def _notification_health(now) -> dict:
    counts = {choice: 0 for choice, _label in NotificationDelivery.Status.choices}
    counts.update(
        {
            row["status"]: row["count"]
            for row in NotificationDelivery.objects.values("status").annotate(
                count=Count("id")
            )
        }
    )
    queued = NotificationDelivery.objects.filter(
        status__in=[
            NotificationDelivery.Status.PENDING,
            NotificationDelivery.Status.RETRY,
        ]
    )
    due = queued.filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    oldest = queued.aggregate(created_at=Min("created_at"))["created_at"]
    last_sent = NotificationDelivery.objects.filter(
        status=NotificationDelivery.Status.SENT
    ).aggregate(sent_at=Max("sent_at"))["sent_at"]
    return {
        "deliveries": counts,
        "queued_count": queued.count(),
        "due_count": due.count(),
        "oldest_queued_at": _iso(oldest),
        "oldest_queue_age_seconds": max((now - oldest).total_seconds(), 0) if oldest else 0,
        "last_sent_at": _iso(last_sent),
        "events_count": NotificationEvent.objects.count(),
        "events_last_24h": NotificationEvent.objects.filter(
            created_at__gte=now - timedelta(hours=24)
        ).count(),
        "active_endpoint_count": NotificationEndpoint.objects.filter(active=True).count(),
        "inactive_endpoint_count": NotificationEndpoint.objects.filter(active=False).count(),
    }


def _record_pipeline_health() -> dict:
    return {
        "source_observation_count": SourceObservation.objects.count(),
        "result_observation_count": ResultObservation.objects.count(),
        "canonical_result_count": CanonicalResult.objects.count(),
        "active_achievement_count": Achievement.objects.filter(
            status=Achievement.Status.ACTIVE
        ).count(),
        "pending_validation_count": CanonicalResult.objects.filter(
            validation_status=CanonicalResult.ValidationStatus.PENDING
        ).count(),
        "rejected_validation_count": CanonicalResult.objects.filter(
            validation_status=CanonicalResult.ValidationStatus.REJECTED
        ).count(),
    }


@api_view(["GET"])
def ingestion_status(request):
    started_at = perf_counter()
    now = timezone.now()
    weekend_start, weekend_end = resolve_weekend_window(
        settings.WCA_WEEKEND_START,
        settings.WCA_WEEKEND_END,
        timezone_name=settings.WCA_WEEKEND_TIME_ZONE,
    )
    classification_pending = ClassificationScopeWork.objects.filter(
        requested_version__gt=F("processed_version")
    )
    classification_times = classification_pending.aggregate(
        oldest_dirty_since=Min("dirty_since"),
        oldest_observed_at=Min("oldest_observed_at"),
    )
    classification_completed = ClassificationScopeWork.objects.aggregate(
        last_completed_at=Max("last_completed_at"),
        max_last_duration_ms=Max("last_duration_ms"),
    )
    oldest_observed_at = classification_times["oldest_observed_at"]
    classification_health = {
        "pending_scope_count": classification_pending.count(),
        "claimed_scope_count": classification_pending.exclude(claimed_by="").count(),
        "failed_scope_count": classification_pending.exclude(last_error="").count(),
        "oldest_dirty_since": _iso(classification_times["oldest_dirty_since"]),
        "oldest_observed_at": _iso(oldest_observed_at),
        "oldest_observation_lag_seconds": (
            max((now - oldest_observed_at).total_seconds(), 0)
            if oldest_observed_at
            else 0
        ),
        "last_completed_at": _iso(classification_completed["last_completed_at"]),
        "max_last_duration_ms": classification_completed["max_last_duration_ms"],
    }
    round_counts = {
        "discovered": SubscriptionRound.objects.filter(active=True).count(),
        "subscribed": SubscriptionRound.objects.filter(
            active=True, subscription_status=SubscriptionRound.Status.SUBSCRIBED
        ).count(),
        "errors": SubscriptionRound.objects.filter(
            active=True, subscription_status=SubscriptionRound.Status.ERROR
        ).count(),
    }
    cubingchina_counts = {
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
    cubingchina_worker = _worker_payload(
        RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET
    )
    cubingchina_worker.update(cubingchina_counts)
    competition_health = list(
        CubingChinaCompetitionTarget.objects.filter(active=True)
        .annotate(
            active_round_count=Count("rounds", filter=Q(rounds__active=True))
        )
        .order_by("competition_start_date", "slug")
        .values(
            "slug",
            "competition_name",
            "wca_competition_id",
            "competition_start_date",
            "competition_end_date",
            "status",
            "connected",
            "last_connected_at",
            "last_message_at",
            "last_snapshot_at",
            "last_error",
            "websocket_diagnostics",
            "active_round_count",
        )
    )
    cubingchina_counters = {}
    cubingchina_queue_size = 0
    cubingchina_peak_queue_size = 0
    for target in competition_health:
        target["competition_start_date"] = _iso(target["competition_start_date"])
        target["competition_end_date"] = _iso(target["competition_end_date"])
        target["last_connected_at"] = _iso(target["last_connected_at"])
        target["last_message_at"] = _iso(target["last_message_at"])
        target["last_snapshot_at"] = _iso(target["last_snapshot_at"])
        queue = _websocket_queue_payload(target.pop("websocket_diagnostics"))
        target["websocket"] = queue
        cubingchina_queue_size += queue["message_queue_size"]
        cubingchina_peak_queue_size = max(
            cubingchina_peak_queue_size, queue["peak_message_queue_size"]
        )
        for key, value in queue["counters"].items():
            if isinstance(value, int):
                cubingchina_counters[key] = cubingchina_counters.get(key, 0) + value
    cubingchina_worker["metadata"] = {
        **cubingchina_worker["metadata"],
        "competitions": competition_health,
    }
    if not cubingchina_worker["last_error"]:
        cubingchina_worker["last_error"] = next(
            (target["last_error"] for target in competition_health if target["last_error"]),
            "",
        )
    graphql_worker = _worker_payload(
        RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION
    )
    websocket_queues = {
        "wca_live": {
            **_websocket_queue_payload(graphql_worker["metadata"].get("websocket")),
            "connected": graphql_worker.get("connected", False),
            "connection_count": 1 if graphql_worker.get("connected") else 0,
        },
        "cubingchina": {
            "message_queue_size": cubingchina_queue_size,
            "peak_message_queue_size": cubingchina_peak_queue_size,
            "queue_capacity": None,
            "captured_at": max(
                (
                    target["websocket"]["captured_at"]
                    for target in competition_health
                    if target["websocket"]["captured_at"]
                ),
                default=None,
            ),
            "counters": cubingchina_counters,
            "connected": cubingchina_worker.get("connected", False),
            "connection_count": cubingchina_counts["connected_competition_count"],
        },
    }
    payload = {
        "api_polling": _worker_payload(
            RecentRecordObservation.IngestionMethod.API_POLLING
        ),
        "graphql_subscription": graphql_worker,
        "cubingchina_websocket": cubingchina_worker,
        "websocket_queues": websocket_queues,
        "classification": classification_health,
        "notifications": _notification_health(now),
        "record_pipeline": _record_pipeline_health(),
        "subscription_rounds": round_counts,
        "configuration": {
            "weekend_start": weekend_start.isoformat(),
            "weekend_end": weekend_end.isoformat(),
            "lookback_days": settings.WCA_COMPETITION_LOOKBACK_DAYS,
            "catchup_minutes": settings.WCA_SUBSCRIPTION_CATCHUP_MINUTES,
            "telemetry_interval_seconds": settings.WORKER_TELEMETRY_INTERVAL_SECONDS,
        },
        "generated_at": _iso(now),
    }
    payload["response_generation_ms"] = round((perf_counter() - started_at) * 1000, 2)
    return Response(payload)


@api_view(["GET"])
def record_comparison(request):
    rows = RecentRecordObservation.objects.values(
        "canonical_key", "ingestion_method", "detected_at", "status"
    )
    grouped = {}
    for row in rows:
        entry = grouped.setdefault(row["canonical_key"], {"canonical_key": row["canonical_key"]})
        entry[row["ingestion_method"]] = {
            "detected_at": _iso(row["detected_at"]),
            "status": row["status"],
        }
    comparisons = []
    for entry in grouped.values():
        api = entry.get(RecentRecordObservation.IngestionMethod.API_POLLING)
        subscription = entry.get(
            RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION
        )
        delta = None
        if api and subscription:
            api_dt = datetime.fromisoformat(api["detected_at"])
            subscription_dt = datetime.fromisoformat(subscription["detected_at"])
            delta = (api_dt - subscription_dt).total_seconds()
        timestamps = {
            method: value["detected_at"]
            for method, value in entry.items()
            if method != "canonical_key" and isinstance(value, dict)
        }
        entry["matched"] = len(timestamps) > 1
        entry["matching_pipelines"] = sorted(timestamps)
        entry["detection_timestamps_by_pipeline"] = timestamps
        if timestamps:
            earliest = min(datetime.fromisoformat(value) for value in timestamps.values())
            entry["detection_time_deltas_seconds"] = {
                method: (datetime.fromisoformat(value) - earliest).total_seconds()
                for method, value in timestamps.items()
            }
        else:
            entry["detection_time_deltas_seconds"] = {}
        entry["api_minus_subscription_seconds"] = delta
        comparisons.append(entry)
    comparisons.sort(key=lambda row: row["canonical_key"])
    return Response({"count": len(comparisons), "results": comparisons})
