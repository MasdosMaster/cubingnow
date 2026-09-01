from datetime import date, datetime

from django.utils.dateparse import parse_datetime

from .exceptions import WCALivePayloadError
from .schemas import RecordCandidate


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"expected a timezone-aware datetime, got {value!r}")
    return parsed


def map_record(payload: dict, observed_at: datetime) -> RecordCandidate:
    """Map WCA Live's deployed ``recentRecords`` shape without recomputing its tag."""

    try:
        result = payload["result"]
        competitor = result["person"]
        round_payload = result["round"]
        competition_event = round_payload["competitionEvent"]
        competition = competition_event["competition"]
        event = competition_event["event"]
        format_payload = round_payload.get("format") or {}
        cutoff_payload = round_payload.get("cutoff") or {}
        wca_live_competition_id = str(competition.get("id") or "")
        round_id = str(round_payload.get("id") or "")
        venues = competition.get("venues") or []
        return RecordCandidate(
            stable_result_identity=str(result["id"]),
            wca_live_record_id=str(payload["id"]),
            wca_live_result_id=str(result["id"]),
            wca_live_competition_id=wca_live_competition_id,
            wca_competition_id=competition["wcaId"],
            competition_name=competition["name"],
            competition_country_code=(
                (venues or [{}])[0].get("country", {}).get("iso2", "")
            ),
            competition_timezone=(venues[0].get("timezone") or "") if len(venues) == 1 else "",
            competition_start_date=_date(competition["startDate"]),
            competition_end_date=_date(competition["endDate"]),
            round_id=round_id,
            round_number=round_payload.get("number"),
            round_name=round_payload.get("name") or "",
            event_id=event["id"],
            event_name=event["name"],
            competitor_name=competitor.get("name") or "",
            competitor_wca_id=competitor.get("wcaId") or "",
            competitor_wca_live_id=str(competitor.get("id") or ""),
            country_code=competitor.get("country", {}).get("iso2", ""),
            kind=payload["type"].lower(),
            raw_result=int(payload["attemptResult"]),
            record_level=payload["tag"].upper(),
            source_url=(
                "https://live.worldcubeassociation.org/competitions/"
                f"{wca_live_competition_id}/rounds/{round_id}"
                if wca_live_competition_id and round_id
                else ""
            ),
            source_update_timestamp=_datetime(result.get("enteredAt")),
            observed_at=observed_at,
            source="wca_live",
            source_result_id=str(result["id"]),
            source_competition_id=wca_live_competition_id,
            source_competitor_id=str(competitor.get("id") or ""),
            attempts=tuple(
                int(attempt.get("result") or 0) for attempt in (result.get("attempts") or [])
            ),
            final_best=result.get("best"),
            final_average=result.get("average"),
            expected_attempts=format_payload.get("numberOfAttempts"),
            cutoff_attempts=cutoff_payload.get("numberOfAttempts"),
            cutoff_value=cutoff_payload.get("attemptResult"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WCALivePayloadError(f"Invalid WCA Live record payload: {exc}") from exc
