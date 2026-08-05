import logging
from datetime import date, timedelta

from .attendance_parser import parse_competition_detail, parse_competition_index
from .live_parser import parse_live_competition
from .live_schemas import CubingChinaDiscoveryEntry

logger = logging.getLogger(__name__)


def rolling_discovery_window(
    as_of: date, lookback_days: int = 1, lookahead_days: int = 7
) -> tuple[date, date]:
    return (
        as_of - timedelta(days=max(lookback_days, 0)),
        as_of + timedelta(days=max(lookahead_days, 0)),
    )


def discover_live_competitions(
    client,
    as_of: date,
    lookback_days: int = 1,
    lookahead_days: int = 7,
) -> tuple[list[CubingChinaDiscoveryEntry], dict]:
    window_start, window_end = rolling_discovery_window(
        as_of, lookback_days, lookahead_days
    )
    index_html = client.get_page("/competition", lang="en")
    indexed = parse_competition_index(index_html)
    relevant = [row for row in indexed if row.overlaps(window_start, window_end)]
    entries = []
    detail_failures = 0
    pending_live_pages = 0
    for competition in relevant:
        try:
            detail_html = client.get_page(f"/competition/{competition.source_id}", lang="en")
            competition = parse_competition_detail(detail_html, competition)
        except Exception as exc:  # noqa: BLE001 - keep other discoveries alive.
            detail_failures += 1
            logger.warning(
                "cubingchina_competition_detail_failed slug=%s error=%s",
                competition.source_id,
                exc,
            )
            entries.append(
                CubingChinaDiscoveryEntry(
                    slug=competition.source_id,
                    wca_competition_id="",
                    competition_name=competition.name,
                    competition_start_date=competition.start_date,
                    competition_end_date=competition.end_date,
                    error=str(exc),
                    detail_verified=False,
                )
            )
            continue
        if not competition.wca_id:
            continue
        try:
            live_html = client.get_page(f"/live/{competition.source_id}", lang="en")
            live = parse_live_competition(live_html, competition)
            entries.append(
                CubingChinaDiscoveryEntry(
                    slug=competition.source_id,
                    wca_competition_id=competition.wca_id,
                    competition_name=competition.name,
                    competition_start_date=competition.start_date,
                    competition_end_date=competition.end_date,
                    live=live,
                )
            )
        except Exception as exc:  # noqa: BLE001 - live data may not exist yet.
            pending_live_pages += 1
            entries.append(
                CubingChinaDiscoveryEntry(
                    slug=competition.source_id,
                    wca_competition_id=competition.wca_id,
                    competition_name=competition.name,
                    competition_start_date=competition.start_date,
                    competition_end_date=competition.end_date,
                    error=str(exc),
                )
            )
    metadata = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "competitions_fetched": len(indexed),
        "competitions_relevant": len(relevant),
        "official_competitions_discovered": sum(
            bool(entry.wca_competition_id) for entry in entries
        ),
        "live_competitions_ready": sum(bool(entry.live) for entry in entries),
        "pending_live_pages": pending_live_pages,
        "competition_detail_failures": detail_failures,
    }
    logger.info(
        "cubingchina_discovery_completed relevant=%d ready=%d pending=%d failures=%d",
        len(relevant),
        metadata["live_competitions_ready"],
        pending_live_pages,
        detail_failures,
    )
    return entries, metadata
