import logging
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


def flatten_competition_rounds(competition: dict) -> list[RoundTarget]:
    targets = []
    for competition_event in competition.get("competitionEvents", []):
        event = competition_event["event"]
        for round_payload in competition_event.get("rounds", []):
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
                    competition_start_date=date.fromisoformat(competition["startDate"]),
                    competition_end_date=date.fromisoformat(competition["endDate"]),
                    event_id=event["id"],
                    event_name=event["name"],
                    round_number=round_payload.get("number"),
                    round_name=round_payload.get("name") or "",
                )
            )
    return targets


def discover_weekend_rounds(
    client,
    weekend_start: date,
    weekend_end: date,
    lookback_days: int = 7,
) -> tuple[list[RoundTarget], dict]:
    lookback = competition_lookback(weekend_start, lookback_days)
    logger.info("wca_discovery_started lookback_date=%s", lookback.isoformat())
    data = client.execute(WEEKEND_COMPETITIONS_QUERY, {"from": lookback.isoformat()})
    competitions = data.get("competitions", [])
    overlapping = filter_overlapping_competitions(competitions, weekend_start, weekend_end)
    targets = []
    detail_failures = 0
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
            targets.extend(flatten_competition_rounds(payload))
    metadata = {
        "lookback_date": lookback.isoformat(),
        "competitions_fetched": len(competitions),
        "competitions_overlapping": len(overlapping),
        "rounds_discovered": len(targets),
        "competition_detail_failures": detail_failures,
    }
    logger.info(
        "wca_discovery_completed competitions_fetched=%d competitions_overlapping=%d rounds_discovered=%d",
        len(competitions),
        len(overlapping),
        len(targets),
    )
    return targets, metadata
