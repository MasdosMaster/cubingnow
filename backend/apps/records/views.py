from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import (
    Achievement,
    CubingChinaCompetitionTarget,
    CubingChinaRoundTarget,
    IngestionWorkerStatus,
    RecentRecordObservation,
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


@api_view(["GET"])
def ingestion_status(request):
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
        .order_by("competition_start_date", "slug")
        .values(
            "slug",
            "wca_competition_id",
            "status",
            "connected",
            "last_message_at",
            "last_snapshot_at",
            "last_error",
        )
    )
    for target in competition_health:
        target["last_message_at"] = _iso(target["last_message_at"])
        target["last_snapshot_at"] = _iso(target["last_snapshot_at"])
    cubingchina_worker["metadata"] = {
        **cubingchina_worker["metadata"],
        "competitions": competition_health,
    }
    if not cubingchina_worker["last_error"]:
        cubingchina_worker["last_error"] = next(
            (target["last_error"] for target in competition_health if target["last_error"]),
            "",
        )
    return Response(
        {
            "api_polling": _worker_payload(
                RecentRecordObservation.IngestionMethod.API_POLLING
            ),
            "graphql_subscription": _worker_payload(
                RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION
            ),
            "cubingchina_websocket": cubingchina_worker,
            "subscription_rounds": round_counts,
            "configuration": {
                "weekend_start": settings.WCA_WEEKEND_START,
                "weekend_end": settings.WCA_WEEKEND_END,
                "lookback_days": settings.WCA_COMPETITION_LOOKBACK_DAYS,
                "catchup_minutes": settings.WCA_SUBSCRIPTION_CATCHUP_MINUTES,
            },
            "generated_at": _iso(timezone.now()),
        }
    )


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
