from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CubingChinaRoundDescriptor:
    event_id: str
    event_name: str
    round_id: str
    round_number: int
    round_name: str
    format: str
    cutoff: int
    time_limit: int
    status: int


@dataclass(frozen=True)
class CubingChinaLiveCompetition:
    slug: str
    cubingchina_id: int
    wca_competition_id: str
    competition_name: str
    competition_start_date: date
    competition_end_date: date
    rounds: tuple[CubingChinaRoundDescriptor, ...]


@dataclass(frozen=True)
class CubingChinaDiscoveryEntry:
    slug: str
    wca_competition_id: str
    competition_name: str
    competition_start_date: date
    competition_end_date: date
    live: CubingChinaLiveCompetition | None = None
    error: str = ""
    detail_verified: bool = True


@dataclass(frozen=True)
class NormalizedCubingChinaResult:
    result_id: str
    stable_result_identity: str
    competitor_number: int
    competitor_name: str
    competitor_wca_id: str
    region: str
    country_code: str
    attempts: tuple[int, ...]
    best: int | None
    average: int | None
    single_record_tag: str
    average_record_tag: str
    meaningful_hash: str
    payload: dict


@dataclass(frozen=True)
class CubingChinaSnapshotDiff:
    additions: tuple[str, ...]
    changes: tuple[str, ...]
    removals: tuple[str, ...]
    unchanged: tuple[str, ...]
