from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class NormalizedResultObservation:
    """Provider-neutral result evidence passed into reconciliation."""

    source: str
    ingestion_method: str
    source_result_identity: str
    source_competition_id: str
    source_competitor_id: str
    wca_competition_id: str
    competition_name: str
    competition_country_code: str
    competition_start_date: date | None
    competition_end_date: date | None
    round_id: str
    round_number: int | None
    round_name: str
    event_id: str
    event_name: str
    competitor_name: str
    competitor_wca_id: str
    country_code: str
    kind: str
    value: int
    attempt_number: int | None
    source_record_tag: str
    entered_at: datetime | None
    observed_at: datetime
    source_url: str
    normalized_payload: dict
    raw_observation_id: int | None = None

    @property
    def observation_key(self) -> str:
        slot = f"attempt:{self.attempt_number}" if self.attempt_number is not None else "aggregate"
        return (
            f"{self.source}|{self.ingestion_method}|{self.source_result_identity}"
            f"|{self.kind}|{slot}"
        )

    @property
    def natural_result_prefix(self) -> str | None:
        if not (
            self.wca_competition_id and self.competitor_wca_id and self.round_number is not None
        ):
            return None
        return "|".join(
            [
                "wca",
                self.wca_competition_id.upper(),
                self.competitor_wca_id.upper(),
                self.event_id,
                str(self.round_number),
                self.kind,
            ]
        )

    @property
    def proposed_identity_key(self) -> str:
        prefix = self.natural_result_prefix
        if prefix:
            if self.kind == "single" and self.attempt_number is not None:
                return f"{prefix}|attempt:{self.attempt_number}"
            if self.kind == "average":
                return f"{prefix}|aggregate"
            return f"{prefix}|source-result:{self.source_result_identity}"
        slot = f"attempt:{self.attempt_number}" if self.attempt_number is not None else "aggregate"
        return (
            f"source|{self.source}|{self.source_competition_id}"
            f"|{self.source_result_identity}|{self.event_id}|{self.kind}|{slot}"
        )

    @property
    def material_fingerprint(self) -> tuple:
        """Fields that can change the reconciled fact or its canonical context.

        Observation time, raw-frame linkage, and the full provider payload are
        intentionally excluded. A new full snapshot should not rewrite every old
        attempt merely because another attempt was added to the same result row.
        """

        return (
            self.source,
            self.ingestion_method,
            self.source_result_identity,
            self.source_competition_id,
            self.source_competitor_id,
            self.wca_competition_id,
            self.competition_name,
            self.competition_country_code,
            self.competition_start_date,
            self.competition_end_date,
            self.round_id,
            self.round_number,
            self.round_name,
            self.event_id,
            self.event_name,
            self.competitor_name,
            self.competitor_wca_id,
            self.country_code,
            self.kind,
            self.value,
            self.attempt_number,
            self.source_record_tag,
            self.entered_at,
            self.source_url,
        )


@dataclass(frozen=True)
class ResultChange:
    change_type: str
    kind: str
    value: int | None
    attempt_number: int | None = None
    previous_value: int | None = None
