from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from .geography import CONTINENTS, continent_for_country_code
from .models import Attendance, AttendanceSyncRun, Competitor
from .serializers import AttendanceSerializer, CompetitorSerializer
from .weekend import alphabetical_key, attendance_window


class CompetitorViewSet(ReadOnlyModelViewSet):
    queryset = Competitor.objects.select_related("featured_profile").all()
    serializer_class = CompetitorSerializer
    lookup_field = "wca_id"


class AttendanceViewSet(ReadOnlyModelViewSet):
    queryset = Attendance.objects.select_related("competitor", "competition").all()
    serializer_class = AttendanceSerializer


@api_view(["GET"])
def competing_this_weekend(request):
    selected_continent = request.query_params.get("continent", "")
    if selected_continent and selected_continent not in CONTINENTS:
        return Response(
            {
                "detail": "Unknown continent.",
                "allowed_continents": list(CONTINENTS),
            },
            status=400,
        )

    window_start, window_end = attendance_window()
    attendances = Attendance.objects.filter(
        is_accepted=True,
        competition__start_date__lte=window_end,
        competition__end_date__gte=window_start,
    ).select_related("competitor", "competition")

    grouped = {}
    for attendance in attendances:
        competitor = attendance.competitor
        continent = competitor.continent or continent_for_country_code(
            competitor.country_code
        )
        if selected_continent and continent != selected_continent:
            continue
        row = grouped.setdefault(
            competitor.wca_id,
            {
                "wca_id": competitor.wca_id,
                "name": competitor.name,
                "country_code": competitor.country_code,
                "continent": continent,
                "competitions": {},
            },
        )
        competition = attendance.competition
        row["competitions"][competition.source_key] = {
            "id": competition.source_key,
            "wca_id": competition.wca_id,
            "name": competition.name,
            "country_code": competition.country_code,
            "city": competition.city,
            "start_date": competition.start_date,
            "end_date": competition.end_date,
        }

    results = sorted(
        grouped.values(),
        key=lambda row: (alphabetical_key(row["name"]), row["wca_id"]),
    )
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
        row["competitions"] = sorted(
            row["competitions"].values(),
            key=lambda competition: (
                competition["start_date"],
                alphabetical_key(competition["name"]),
            ),
        )

    last_sync = AttendanceSyncRun.objects.filter(
        status=AttendanceSyncRun.Status.SUCCEEDED,
        window_start=window_start,
        window_end=window_end,
    ).first()
    if last_sync is None:
        sync_status = "not_yet_synchronised"
    elif last_sync.finished_at < timezone.now() - timedelta(
        hours=settings.ATTENDANCE_SYNC_STALE_HOURS
    ):
        sync_status = "stale"
    else:
        sync_status = "fresh"

    return Response(
        {
            "window": {
                "start_date": window_start,
                "end_date": window_end,
                "timezone": settings.ATTENDANCE_WINDOW_TIME_ZONE,
            },
            "last_successful_sync_at": last_sync.finished_at if last_sync else None,
            "sync_status": sync_status,
            "selected_continent": selected_continent or "All",
            "continents": list(CONTINENTS),
            "count": len(results),
            "results": results,
        }
    )
