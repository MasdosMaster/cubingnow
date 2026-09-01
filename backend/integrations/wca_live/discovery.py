import logging
import re
from datetime import date, timedelta

from .queries import COMPETITION_ROUNDS_QUERY, WEEKEND_COMPETITIONS_QUERY
from .schemas import RoundTarget

logger = logging.getLogger(__name__)


def competition_overlaps(comp: dict, weekend_start: date, weekend_end: date) -> bool:
    start = date.fromisoformat(comp["startDate"])
    end = date.fromisoformat(comp["endDate"])
    return start <= weekend_end and end >= weekend_start


def competition_lookback(weekend_start: date, lookback_days: int) -> date:
    return weekend_start - timedelta(days=max(lookback_days, 0))


def filter_overlapping_competitions(
    competitions: list[dict], weekend_start: date, weekend_end: date
) -> list[dict]:
    return [
        competition
        for competition in competitions
        if competition_overlaps(competition, weekend_start, weekend_end)
    ]


def round_timezones_from_wcif(wcif: dict) -> dict[tuple[str, int], str]:
    """Resolve unambiguous event/round activities to their venue timezone."""

    candidates: dict[tuple[str, int], set[str]] = {}

    def visit(activity: dict, timezone_id: str) -> None:
        code = str(activity.get("activityCode") or "")
        match = re.match(r"^([^-]+)-r(\d+)(?:-|$)", code)
        if match and timezone_id:
            key = (match.group(1), int(match.group(2)))
            candidates.setdefault(key, set()).add(timezone_id)
        for child in activity.get("childActivities") or []:
            visit(child, timezone_id)

    for venue in (wcif.get("schedule") or {}).get("venues") or []:
        timezone_id = str(venue.get("timezone") or "")
        for room in venue.get("rooms") or []:
            for activity in room.get("activities") or []:
                visit(activity, timezone_id)
    return {
        key: next(iter(timezones))
        for key, timezones in candidates.items()
        if len(timezones) == 1
    }


def flatten_competition_rounds(
    competition: dict,
    round_timezones: dict[tuple[str, int], str] | None = None,
) -> list[RoundTarget]:
    targets = []
    venues = competition.get("venues") or []
    default_timezone = (venues[0].get("timezone") or "") if len(venues) == 1 else ""
    round_timezones = round_timezones or {}
    for competition_event in competition.get("competitionEvents", []):
        event = competition_event["event"]
        for round_payload in competition_event.get("rounds", []):
            format_payload = round_payload.get("format") or {}
            cutoff_payload = round_payload.get("cutoff") or {}
            round_number = round_payload.get("number")
            competition_timezone = default_timezone or round_timezones.get(
                (event["id"], round_number), ""
            )
            targets.append(
                RoundTarget(
                    round_id=str(round_payload["id"]),
                    wca_live_competition_id=str(competition["id"]),
                    wca_competition_id=competition["wcaId"],
                    competition_name=competition["name"],
                    competition_country_code=(
                        (competition.get("venues") or [{}])[0]
                        .get("country", {})
                        .get("iso2", "")
                    ),
                    competition_timezone=competition_timezone,
                    competition_start_date=date.fromisoformat(competition["startDate"]),
                    competition_end_date=date.fromisoformat(competition["endDate"]),
                    event_id=event["id"],
                    event_name=event["name"],
                    round_number=round_number,
                    round_name=round_payload.get("name") or "",
                    format_id=str(format_payload.get("id") or ""),
                    format_sort_by=str(format_payload.get("sortBy") or ""),
                    expected_attempts=format_payload.get("numberOfAttempts"),
                    cutoff_attempts=cutoff_payload.get("numberOfAttempts"),
                    cutoff_value=cutoff_payload.get("attemptResult"),
                )
            )
    return targets


def discover_weekend_rounds(
    client,
    weekend_start: date,
    weekend_end: date,
    lookback_days: int = 7,
    wcif_client=None,
) -> tuple[list[RoundTarget], dict]:
    lookback = competition_lookback(weekend_start, lookback_days)
    logger.info("wca_discovery_started lookback_date=%s", lookback.isoformat())
    data = client.execute(WEEKEND_COMPETITIONS_QUERY, {"from": lookback.isoformat()})
    competitions = data.get("competitions", [])
    overlapping = filter_overlapping_competitions(competitions, weekend_start, weekend_end)
    targets = []
    detail_failures = 0
    timezone_resolution_failures = 0
    for competition in overlapping:
        try:
            detail = client.execute(
                COMPETITION_ROUNDS_QUERY, {"id": str(competition["id"])}
            )
        except Exception:
            detail_failures += 1
            logger.exception(
                "competition_round_discovery_failed competition_id=%s wca_id=%s",
                competition.get("id", ""),
                competition.get("wcaId", ""),
            )
            continue
        payload = detail.get("competition")
        if payload:
            round_timezones = {}
            if len(payload.get("venues") or []) > 1 and wcif_client is not None:
                try:
                    wcif = wcif_client.get_json(
                        f"/api/v0/competitions/{payload['wcaId']}/wcif/public"
                    )
                    round_timezones = round_timezones_from_wcif(wcif)
                except Exception:
                    timezone_resolution_failures += 1
                    logger.exception(
                        "competition_timezone_resolution_failed wca_id=%s",
                        payload.get("wcaId", ""),
                    )
            targets.extend(flatten_competition_rounds(payload, round_timezones))
    metadata = {
        "lookback_date": lookback.isoformat(),
        "competitions_fetched": len(competitions),
        "competitions_overlapping": len(overlapping),
        "rounds_discovered": len(targets),
        "competition_detail_failures": detail_failures,
        "timezone_resolution_failures": timezone_resolution_failures,
    }
    logger.info(
        "wca_discovery_completed competitions_fetched=%d competitions_overlapping=%d rounds_discovered=%d",
        len(competitions),
        len(overlapping),
        len(targets),
    )
    return targets, metadata
