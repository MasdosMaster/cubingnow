from apps.records.domain import NormalizedResultObservation
from apps.records.finalization import (
    all_expected_attempts_are_entered,
    cubingchina_finalization_rule,
    round_result_is_finalized,
)

from .live_schemas import NormalizedCubingChinaResult


def result_observations(
    target, result: NormalizedCubingChinaResult, observed_at, raw_observation_id=None
):
    if not round_result_is_finalized(
        result.attempts,
        cubingchina_finalization_rule(target),
        event_id=target.event_id,
    ):
        return ()
    competition = target.competition
    source_url = (
        f"https://cubing.com/live/{competition.slug}"
        f"#!/event/{target.event_id}/{target.round_id}/all"
    )
    common = {
        "source": "cubingchina",
        "ingestion_method": "cubingchina_websocket",
        "source_result_identity": result.stable_result_identity,
        "source_competition_id": str(competition.cubingchina_id or ""),
        "source_competitor_id": str(result.competitor_number),
        "wca_competition_id": competition.wca_competition_id,
        "competition_name": competition.competition_name,
        "competition_country_code": "",
        "competition_start_date": competition.competition_start_date,
        "competition_end_date": competition.competition_end_date,
        "round_id": target.round_id,
        "round_number": target.round_number,
        "round_name": target.round_name,
        "event_id": target.event_id,
        "event_name": target.event_name,
        "competitor_name": result.competitor_name,
        "competitor_wca_id": result.competitor_wca_id,
        "country_code": result.country_code,
        "entered_at": None,
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
    rule = cubingchina_finalization_rule(target)
    if result.average not in (None, 0) and all_expected_attempts_are_entered(
        result.attempts, rule.expected_attempts
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
