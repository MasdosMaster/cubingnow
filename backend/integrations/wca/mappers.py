from datetime import date, datetime

from .exceptions import WCAPayloadError
from .schemas import RecordObservation


def _date(value: str) -> date:
    return date.fromisoformat(value)


def map_record(payload: dict, observed_at: datetime) -> RecordObservation:
    """Map the WCA Live ``recentRecords`` shape into CubeRecord terminology."""
    try:
        result = payload["result"]
        competitor = result["person"]
        competition_event = result["round"]["competitionEvent"]
        competition = competition_event["competition"]
        event = competition_event["event"]
        venues = competition.get("venues", [])
        venue = venues[0] if venues else {}
        return RecordObservation(
            source_id=str(payload["id"]),
            competition_id=competition["wcaId"],
            competition_name=competition["name"],
            competition_country_code=venue.get("country", {}).get("iso2", "XX"),
            competition_city="",
            competition_timezone=venue.get("timezone", ""),
            competition_start_date=_date(competition["startDate"]),
            competition_end_date=_date(competition["endDate"]),
            competitor_wca_id=competitor["wcaId"],
            competitor_name=competitor["name"],
            competitor_country_code=competitor["country"]["iso2"],
            event_id=event["id"],
            event_name=event["name"],
            result_kind=payload["type"].lower(),
            result_value=int(payload["attemptResult"]),
            record_level=payload["tag"].upper(),
            observed_at=observed_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WCAPayloadError(f"Invalid WCA record payload: {exc}") from exc
