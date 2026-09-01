from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class RoundTarget:
    round_id: str
    wca_live_competition_id: str
    wca_competition_id: str
    competition_name: str
    competition_start_date: date
    competition_end_date: date
    event_id: str
    event_name: str
    competition_country_code: str = ""
    round_number: int | None = None
    round_name: str = ""
    format_id: str = ""
    format_sort_by: str = ""
    expected_attempts: int | None = None
    cutoff_attempts: int | None = None
    cutoff_value: int | None = None


@dataclass(frozen=True)
class NormalizedRoundResult:
    result_id: str
    stable_result_identity: str
    competitor_wca_live_id: str
    competitor_wca_id: str
    competitor_name: str
    country_code: str
    attempts: tuple[int, ...]
    best: int | None
    average: int | None
    single_record_tag: str
    average_record_tag: str
    entered_at: datetime | None
    meaningful_hash: str
    payload: dict


@dataclass(frozen=True)
class RecordCandidate:
    stable_result_identity: str
    wca_live_record_id: str
    wca_live_result_id: str
    wca_live_competition_id: str
    wca_competition_id: str
    competition_name: str
    competition_start_date: date | None
    competition_end_date: date | None
    round_id: str
    round_name: str
    event_id: str
    event_name: str
    competitor_name: str
    competitor_wca_id: str
    competitor_wca_live_id: str
    country_code: str
    kind: str
    raw_result: int
    record_level: str
    source_url: str
    source_update_timestamp: datetime | None
    observed_at: datetime
    round_number: int | None = None
    source: str = "wca_live"
    source_result_id: str = ""
    source_competition_id: str = ""
    source_competitor_id: str = ""
    competition_country_code: str = ""
    attempts: tuple[int, ...] = ()
    final_best: int | None = None
    final_average: int | None = None
    expected_attempts: int | None = None
    cutoff_attempts: int | None = None
    cutoff_value: int | None = None


# Compatibility name used by the existing integration boundary.
RecordObservation = RecordCandidate
