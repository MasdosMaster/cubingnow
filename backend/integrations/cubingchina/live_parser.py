import json

from bs4 import BeautifulSoup

from integrations.attendance_types import SourceCompetition

from .live_schemas import CubingChinaLiveCompetition, CubingChinaRoundDescriptor


class CubingChinaLivePageError(ValueError):
    """Raised when a CubingChina live page is unavailable or incompatible."""


def parse_live_competition(
    html: str, competition: SourceCompetition
) -> CubingChinaLiveCompetition:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#live-container")
    if container is None:
        raise CubingChinaLivePageError("CubingChina live container was not found")
    if container.get("data-type") != "WCA":
        raise CubingChinaLivePageError("CubingChina live competition is not marked as WCA")
    try:
        cubingchina_id = int(container["data-c"])
        events = json.loads(container["data-events"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CubingChinaLivePageError(f"Invalid CubingChina live metadata: {exc}") from exc

    rounds = []
    try:
        for event in events:
            event_id = str(event["i"])
            event_name = str(event.get("name") or event_id)
            for index, source_round in enumerate(event.get("rs", []), start=1):
                rounds.append(
                    CubingChinaRoundDescriptor(
                        event_id=event_id,
                        event_name=event_name,
                        round_id=str(source_round["i"]),
                        round_number=index,
                        round_name=str(source_round.get("name") or ""),
                        format=str(source_round.get("f") or ""),
                        cutoff=int(source_round.get("co") or 0),
                        time_limit=int(source_round.get("tl") or 0),
                        status=int(source_round.get("s") or 0),
                    )
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise CubingChinaLivePageError(f"Invalid CubingChina round metadata: {exc}") from exc
    if not rounds:
        raise CubingChinaLivePageError("CubingChina live page has no rounds")
    if not competition.wca_id:
        raise CubingChinaLivePageError("CubingChina competition has no WCA competition ID")
    return CubingChinaLiveCompetition(
        slug=competition.source_id,
        cubingchina_id=cubingchina_id,
        wca_competition_id=competition.wca_id,
        competition_name=competition.name,
        competition_start_date=competition.start_date,
        competition_end_date=competition.end_date,
        rounds=tuple(rounds),
    )
