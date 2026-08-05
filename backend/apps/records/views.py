from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import (
    IngestionWorkerStatus,
    RecentRecordObservation,
    Record,
    SubscriptionRound,
)
from .serializers import RecentRecordObservationSerializer, RecordSerializer


class RecordViewSet(ReadOnlyModelViewSet):
    serializer_class = RecordSerializer

    def get_queryset(self):
        queryset = Record.objects.select_related(
            "result__competitor", "result__competition"
        ).all()
        level = self.request.query_params.get("level")
        status = self.request.query_params.get("status", Record.Status.ACTIVE)
        query = self.request.query_params.get("q")
        if level:
            queryset = queryset.filter(level=level.upper())
        if status:
            queryset = queryset.filter(status=status)
        if query:
            queryset = queryset.filter(
                Q(result__competitor__name__icontains=query)
                | Q(result__competitor__wca_id__icontains=query)
                | Q(result__event_name__icontains=query)
                | Q(result__competition__name__icontains=query)
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
        status = self.request.query_params.get("status")
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
            "last_error": "",
            "observations_count": observations,
        }
        if method == RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION:
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
        "subscribed_round_count": status.subscribed_round_count,
        "last_error": status.last_error,
        "observations_count": observations,
        "metadata": status.metadata,
    }
    if method == RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION:
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
    return Response(
        {
            "api_polling": _worker_payload(
                RecentRecordObservation.IngestionMethod.API_POLLING
            ),
            "graphql_subscription": _worker_payload(
                RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION
            ),
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
        entry["matched"] = bool(api and subscription)
        entry["api_minus_subscription_seconds"] = delta
        comparisons.append(entry)
    comparisons.sort(key=lambda row: row["canonical_key"])
    return Response({"count": len(comparisons), "results": comparisons})
