import logging
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.competitions.models import Competition
from integrations.attendance_types import SourceCompetition, SourceRegistrant
from integrations.cubingchina.attendance_parser import (
    parse_competition_detail as parse_cubingchina_competition_detail,
)
from integrations.cubingchina.attendance_parser import (
    parse_competition_index as parse_cubingchina_competition_index,
)
from integrations.cubingchina.attendance_parser import (
    parse_registrations as parse_cubingchina_registrations,
)
from integrations.cubingchina.scraper_client import CubingChinaScraperClient
from integrations.wca.attendance_parser import parse_competitions as parse_wca_competitions
from integrations.wca.attendance_parser import parse_registrations as parse_wca_registrations
from integrations.wca.scraper_client import WCAScraperClient
from integrations.weekend_window import rolling_weekend_window

from .models import Attendance, AttendanceSyncRun, Competitor

logger = logging.getLogger(__name__)


def attendance_window(
    as_of: date | datetime | None = None,
    timezone_name: str | None = None,
) -> tuple[date, date]:
    return rolling_weekend_window(
        as_of,
        timezone_name or settings.ATTENDANCE_WINDOW_TIME_ZONE,
    )


def alphabetical_key(name: str) -> str:
    normalised = unicodedata.normalize("NFKD", name).casefold()
    return "".join(character for character in normalised if not unicodedata.combining(character))


@dataclass
class CollectedCompetition:
    competition: SourceCompetition
    source_ids: set[str] = field(default_factory=set)
    registrations: dict[str, SourceRegistrant] = field(default_factory=dict)


def _merge_competition(
    current: SourceCompetition | None, incoming: SourceCompetition
) -> SourceCompetition:
    if current is None or incoming.source == "wca":
        return incoming
    return current


def _merge_registration(
    current: SourceRegistrant | None, incoming: SourceRegistrant
) -> SourceRegistrant:
    if current is None:
        return incoming
    sources = current.sources | incoming.sources
    preferred = incoming if "wca" in incoming.sources else current
    return replace(preferred, sources=sources)


def _wca_competitions(client, window_start, window_end) -> list[SourceCompetition]:
    rows = []
    page = 1
    per_page = 100
    while True:
        payload = client.get_json(
            "/api/v0/competitions",
            start=window_start.isoformat(),
            end=window_end.isoformat(),
            sort="start_date,end_date,name",
            include_cancelled="false",
            page=page,
            per_page=per_page,
        )
        page_rows = payload.get("data", []) if isinstance(payload, dict) else payload
        rows.extend(parse_wca_competitions(page_rows))
        if len(page_rows) < per_page:
            break
        page += 1
        if page > 20:
            raise ValueError("WCA competition pagination exceeded its safety limit")
    return [row for row in rows if row.overlaps(window_start, window_end)]


def _cubingchina_competitions(client, window_start, window_end) -> list[SourceCompetition]:
    by_source_id = {}
    for year in range(window_start.year, window_end.year + 1):
        html = client.get_page("/competition", year=year, lang="en")
        for row in parse_cubingchina_competition_index(html):
            by_source_id[row.source_id] = row
    return [row for row in by_source_id.values() if row.overlaps(window_start, window_end)]


def _match_wca_competition(
    china_competition: SourceCompetition,
    wca_competitions: list[SourceCompetition],
) -> SourceCompetition:
    if china_competition.wca_id:
        return china_competition
    matches = [
        competition
        for competition in wca_competitions
        if alphabetical_key(competition.name) == alphabetical_key(china_competition.name)
        and competition.start_date == china_competition.start_date
        and competition.end_date == china_competition.end_date
    ]
    if len(matches) == 1:
        return replace(china_competition, wca_id=matches[0].wca_id)
    return china_competition


def collect_weekend_attendance(
    wca_client,
    cubingchina_client,
    window_start: date,
    window_end: date,
) -> tuple[dict[str, CollectedCompetition], dict]:
    wca_competitions = _wca_competitions(wca_client, window_start, window_end)
    china_competitions = _cubingchina_competitions(cubingchina_client, window_start, window_end)
    collected: dict[str, CollectedCompetition] = {}

    def add_competition(source_competition, registrations):
        key = source_competition.source_key
        bundle = collected.get(key)
        if bundle is None:
            bundle = CollectedCompetition(competition=source_competition)
            collected[key] = bundle
        else:
            bundle.competition = _merge_competition(bundle.competition, source_competition)
        bundle.source_ids.add(source_competition.source)
        for registration in registrations:
            bundle.registrations[registration.wca_id] = _merge_registration(
                bundle.registrations.get(registration.wca_id), registration
            )

    for competition in wca_competitions:
        payload = wca_client.get_json(competition.registration_path)
        add_competition(competition, parse_wca_registrations(payload))

    for competition in china_competitions:
        detail_html = cubingchina_client.get_page(
            f"/competition/{competition.source_id}", lang="en"
        )
        competition = parse_cubingchina_competition_detail(detail_html, competition)
        competition = _match_wca_competition(competition, wca_competitions)
        registration_html = cubingchina_client.get_page(competition.registration_path, lang="en")
        add_competition(competition, parse_cubingchina_registrations(registration_html))

    stats = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "wca_competitions": len(wca_competitions),
        "cubingchina_competitions": len(china_competitions),
        "competitions": len(collected),
        "competitors": len(
            {wca_id for bundle in collected.values() for wca_id in bundle.registrations}
        ),
        "attendance_rows": sum(len(bundle.registrations) for bundle in collected.values()),
    }
    return collected, stats


@transaction.atomic
def persist_weekend_attendance(collected, observed_at):
    competition_rows = {}
    for source_key, bundle in collected.items():
        source = bundle.competition
        defaults = {
            "wca_id": source.wca_id,
            "name": source.name,
            "country_code": source.country_code,
            "city": source.city,
            "start_date": source.start_date,
            "end_date": source.end_date,
            "source_updated_at": observed_at,
        }
        competition, _created = Competition.objects.update_or_create(
            source_key=source_key, defaults=defaults
        )
        competition_rows[source_key] = competition

    Attendance.objects.filter(competition__in=competition_rows.values()).update(
        is_accepted=False, sources=[]
    )

    profiles = {}
    for bundle in collected.values():
        for registration in bundle.registrations.values():
            current = profiles.get(registration.wca_id)
            profiles[registration.wca_id] = _merge_registration(current, registration)

    competitor_rows = {}
    for wca_id, profile in profiles.items():
        existing = Competitor.objects.filter(wca_id=wca_id).first()
        defaults = {
            "name": profile.name or (existing.name if existing else ""),
            "country_code": profile.country_code
            or (existing.country_code if existing else ""),
            "continent": profile.continent or (existing.continent if existing else ""),
        }
        competitor, _created = Competitor.objects.update_or_create(wca_id=wca_id, defaults=defaults)
        competitor_rows[wca_id] = competitor

    for source_key, bundle in collected.items():
        competition = competition_rows[source_key]
        for wca_id, registration in bundle.registrations.items():
            Attendance.objects.update_or_create(
                competitor=competitor_rows[wca_id],
                competition=competition,
                defaults={
                    "observed_at": observed_at,
                    "is_accepted": True,
                    "sources": sorted(registration.sources),
                },
            )


def sync_weekend_attendance(
    *,
    as_of: date | datetime | None = None,
    wca_client=None,
    cubingchina_client=None,
):
    window_start, window_end = attendance_window(as_of)
    run = AttendanceSyncRun.objects.create(
        window_start=window_start,
        window_end=window_end,
    )
    own_wca_client = wca_client is None
    own_china_client = cubingchina_client is None
    wca_client = wca_client or WCAScraperClient(base_url=settings.WCA_PUBLIC_BASE_URL)
    cubingchina_client = cubingchina_client or CubingChinaScraperClient(
        base_url=settings.CUBINGCHINA_BASE_URL
    )
    try:
        collected, stats = collect_weekend_attendance(
            wca_client, cubingchina_client, window_start, window_end
        )
        observed_at = timezone.now()
        persist_weekend_attendance(collected, observed_at)
        run.status = AttendanceSyncRun.Status.SUCCEEDED
        run.finished_at = observed_at
        run.metadata = stats
        run.save(update_fields=["status", "finished_at", "metadata"])
        logger.info(
            "weekend_attendance_sync_succeeded window_start=%s window_end=%s "
            "competitions=%d competitors=%d attendance_rows=%d",
            window_start,
            window_end,
            stats["competitions"],
            stats["competitors"],
            stats["attendance_rows"],
        )
        return stats
    except Exception as exc:
        run.status = AttendanceSyncRun.Status.FAILED
        run.finished_at = timezone.now()
        run.error = str(exc)
        run.save(update_fields=["status", "finished_at", "error"])
        logger.exception(
            "weekend_attendance_sync_failed window_start=%s window_end=%s",
            window_start,
            window_end,
        )
        raise
    finally:
        if own_wca_client:
            wca_client.close()
        if own_china_client:
            cubingchina_client.close()
