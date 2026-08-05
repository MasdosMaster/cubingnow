import re
from dataclasses import dataclass, field
from datetime import date

WCA_ID_PATTERN = re.compile(r"^\d{4}[A-Z]{4}\d{2}$")
SOURCES = {"wca", "cubingchina"}


def valid_wca_id(value: str | None) -> bool:
    return bool(value and WCA_ID_PATTERN.fullmatch(value.strip().upper()))


@dataclass(frozen=True)
class SourceCompetition:
    source: str
    source_id: str
    name: str
    start_date: date
    end_date: date
    country_code: str
    city: str = ""
    wca_id: str | None = None
    registration_path: str = ""
    source_url: str = ""

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"Unsupported attendance source: {self.source!r}")

    @property
    def source_key(self) -> str:
        if self.wca_id:
            return f"wca:{self.wca_id}"
        return f"{self.source}:{self.source_id}"

    def overlaps(self, window_start: date, window_end: date) -> bool:
        return self.start_date <= window_end and self.end_date >= window_start


@dataclass
class SourceRegistrant:
    wca_id: str
    name: str
    country_code: str
    continent: str
    sources: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.wca_id = self.wca_id.strip().upper()
        self.name = self.name.strip()
        self.country_code = self.country_code.strip().upper()
