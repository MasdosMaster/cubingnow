from apps.records.domain import NormalizedResultObservation
from apps.records.finalization import (
    RoundFinalizationRule,
    all_expected_attempts_are_entered,
    round_result_is_finalized,
)

from .schemas import NormalizedRoundResult


def result_observations(
    target, result: NormalizedRoundResult, observed_at, raw_observation_id=None
):
    if not round_result_is_finalized(
        result.attempts,
        RoundFinalizationRule(
            expected_attempts=target.expected_attempts or 0,
            cutoff_attempts=target.cutoff_attempts,
            cutoff_value=target.cutoff_value,
        ),
        event_id=target.event_id,
    ):
        return ()
    source_url = (
        "https://live.worldcubeassociation.org/competitions/"
        f"{target.wca_live_competition_id}/rounds/{target.round_id}"
    )
    common = {
        "source": "wca_live",
        "ingestion_method": "graphql_subscription",
        "source_result_identity": result.stable_result_identity,
        "source_competition_id": target.wca_live_competition_id,
        "source_competitor_id": result.competitor_wca_live_id,
        "wca_competition_id": target.wca_competition_id,
        "competition_name": target.competition_name,
        "competition_country_code": target.competition_country_code,
        "competition_timezone": target.competition_timezone,
        "competition_start_date": target.competition_start_date,
        "competition_end_date": target.competition_end_date,
        "round_id": target.round_id,
        "round_number": target.round_number,
        "round_name": target.round_name,
        "event_id": target.event_id,
        "event_name": target.event_name,
        "competitor_name": result.competitor_name,
        "competitor_wca_id": result.competitor_wca_id,
        "country_code": result.country_code,
        "entered_at": result.entered_at,
        "observed_at": observed_at,
        "source_url": source_url,
        "normalized_payload": result.payload,
        "raw_observation_id": raw_observation_id,
    }
    observations = []
    if result.best not in (None, 0):
        observations.append(
            NormalizedResultObservation(
                **common,
                kind="single",
                value=result.best,
                source_record_tag=result.single_record_tag,
            )
        )
    if result.average not in (None, 0) and all_expected_attempts_are_entered(
        result.attempts, target.expected_attempts or 0
    ):
        observations.append(
            NormalizedResultObservation(
                **common,
                kind="average",
                value=result.average,
                source_record_tag=result.average_record_tag,
            )
        )
    return tuple(observations)
