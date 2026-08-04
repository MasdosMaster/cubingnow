from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class RecordObservation:
    source_id: str
    competition_id: str
    competition_name: str
    competition_country_code: str
    competition_city: str
    competition_timezone: str
    competition_start_date: date
    competition_end_date: date
    competitor_wca_id: str
    competitor_name: str
    competitor_country_code: str
    event_id: str
    event_name: str
    result_kind: str
    result_value: int
    record_level: str
    observed_at: datetime
