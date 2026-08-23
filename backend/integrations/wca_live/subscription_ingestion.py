import logging
from datetime import timedelta
from time import monotonic

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.records.classification_work import mark_classification_scopes_dirty
from apps.records.models import (
    IngestionRun,
    RecentRecordObservation,
    SubscriptionRound,
    WCALiveDiffTable,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)

from .ingestion import RECORD_LEVELS, persist_record_candidate, store_observation
from .observations import result_observations
from .result_values import is_complete
from .schemas import NormalizedRoundResult, RecordCandidate
from .snapshots import diff_snapshots, normalize_round_snapshot

logger = logging.getLogger(__name__)
METHOD = RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION


def _stored_result(state: WCALiveDiffTable) -> NormalizedRoundResult:
    return NormalizedRoundResult(
        result_id=state.result_id,
        stable_result_identity=state.stable_result_identity,
        competitor_wca_live_id=state.competitor_wca_live_id,
        competitor_wca_id=state.competitor_wca_id,
        competitor_name=state.competitor_name,
        country_code=state.country_code,
        attempts=tuple(state.attempts),
        best=state.best,
        average=state.average,
        single_record_tag=state.single_record_tag,
        average_record_tag=state.average_record_tag,
        entered_at=state.entered_at,
        meaningful_hash=state.meaningful_hash,
        payload=state.normalized_payload,
    )


def _candidate(
    target: SubscriptionRound,
    result: NormalizedRoundResult,
    kind: str,
    value: int,
    level: str,
    observed_at,
) -> RecordCandidate:
    return RecordCandidate(
        stable_result_identity=result.stable_result_identity,
        wca_live_record_id="",
        wca_live_result_id=result.result_id,
        wca_live_competition_id=target.wca_live_competition_id,
        wca_competition_id=target.wca_competition_id,
        competition_name=target.competition_name,
        competition_country_code=target.competition_country_code,
        competition_start_date=target.competition_start_date,
        competition_end_date=target.competition_end_date,
        round_id=target.round_id,
        round_number=target.round_number,
        round_name=target.round_name,
        event_id=target.event_id,
        event_name=target.event_name,
        competitor_name=result.competitor_name,
        competitor_wca_id=result.competitor_wca_id,
        competitor_wca_live_id=result.competitor_wca_live_id,
        country_code=result.country_code,
        kind=kind,
        raw_result=value,
        record_level=level,
        source_url=(
            "https://live.worldcubeassociation.org/competitions/"
            f"{target.wca_live_competition_id}/rounds/{target.round_id}"
        ),
        source_update_timestamp=result.entered_at,
        observed_at=observed_at,
        source="wca_live",
        source_result_id=result.result_id,
        source_competition_id=target.wca_live_competition_id,
        source_competitor_id=result.competitor_wca_live_id,
    )


def _withdraw_other_states(
    stable_result_identity: str,
    kind: str,
    observed_at,
    active_level: str | None = None,
) -> int:
    queryset = RecentRecordObservation.objects.filter(
        stable_result_identity=stable_result_identity,
        kind=kind,
        ingestion_method=METHOD,
        status=RecentRecordObservation.Status.ACTIVE,
    )
    if active_level:
        queryset = queryset.exclude(record_level=active_level)
    return queryset.update(
        status=RecentRecordObservation.Status.WITHDRAWN,
        withdrawn_at=observed_at,
        last_observed_at=observed_at,
    )


def _needs_record_synchronization(
    previous: NormalizedRoundResult | None,
    current: NormalizedRoundResult,
) -> bool:
    previous_pairs = (
        (
            (previous.best, previous.single_record_tag),
            (previous.average, previous.average_record_tag),
        )
        if previous is not None
        else ((None, ""), (None, ""))
    )
    current_pairs = (
        (current.best, current.single_record_tag),
        (current.average, current.average_record_tag),
    )
    if previous_pairs == current_pairs:
        return False
    return any(tag in RECORD_LEVELS for _value, tag in previous_pairs + current_pairs)


def _synchronize_result_records(
    target: SubscriptionRound,
    result: NormalizedRoundResult,
    observed_at,
    raw_observation=None,
) -> tuple[int, int]:
    detected = 0
    withdrawn = 0
    pairs = (
        (RecentRecordObservation.Kind.SINGLE, result.best, result.single_record_tag),
        (RecentRecordObservation.Kind.AVERAGE, result.average, result.average_record_tag),
    )
    for kind, value, tag in pairs:
        active_level = tag if tag in RECORD_LEVELS and is_complete(value) else None
        withdrawn += _withdraw_other_states(
            result.stable_result_identity, kind, observed_at, active_level
        )
        if active_level:
            candidate = _candidate(target, result, kind, int(value), active_level, observed_at)
            _observation, created = persist_record_candidate(
                candidate,
                METHOD,
                result.payload,
                raw_observation=raw_observation,
                reconcile=False,
            )
            detected += int(created)
    return detected, withdrawn


def _synchronize_normalized_observations(
    target,
    previous: NormalizedRoundResult | None,
    current: NormalizedRoundResult,
    observed_at,
    raw_observation,
) -> set[tuple[str, str]]:
    dirty_scopes: set[tuple[str, str]] = set()
    current_rows = result_observations(target, current, observed_at, raw_observation.pk)
    current_keys = {row.observation_key for row in current_rows}
    previous_rows = (
        result_observations(target, previous, observed_at) if previous is not None else ()
    )
    previous_by_key = {row.observation_key: row for row in previous_rows}
    for row in current_rows:
        previous_row = previous_by_key.get(row.observation_key)
        if (
            previous_row is not None
            and previous_row.material_fingerprint == row.material_fingerprint
        ):
            continue
        reconcile_result_observation(row, defer_classification=True)
        dirty_scopes.add((row.event_id, row.kind))
    if previous is not None:
        for row in previous_rows:
            if row.observation_key not in current_keys and retract_result_observation(
                row.observation_key,
                observed_at,
                defer_classification=True,
            ):
                dirty_scopes.add((row.event_id, row.kind))
    return dirty_scopes


def _persist_states(
    target: SubscriptionRound,
    results: list[NormalizedRoundResult],
    existing: dict[str, WCALiveDiffTable],
    observed_at,
) -> None:
    if not results:
        return
    states = []
    for result in results:
        prior = existing.get(result.result_id)
        states.append(
            WCALiveDiffTable(
                round=target,
                result_id=result.result_id,
                stable_result_identity=result.stable_result_identity,
                competitor_wca_live_id=result.competitor_wca_live_id,
                competitor_wca_id=result.competitor_wca_id,
                competitor_name=result.competitor_name,
                country_code=result.country_code,
                attempts=list(result.attempts),
                best=result.best,
                average=result.average,
                single_record_tag=result.single_record_tag,
                average_record_tag=result.average_record_tag,
                entered_at=result.entered_at,
                meaningful_hash=result.meaningful_hash,
                normalized_payload=result.payload,
                active=True,
                first_observed_at=(prior.first_observed_at if prior is not None else observed_at),
                last_observed_at=observed_at,
                processed_at=observed_at,
            )
        )
    WCALiveDiffTable.objects.bulk_create(
        states,
        update_conflicts=True,
        unique_fields=["round", "result_id"],
        update_fields=[
            "stable_result_identity",
            "competitor_wca_live_id",
            "competitor_wca_id",
            "competitor_name",
            "country_code",
            "attempts",
            "best",
            "average",
            "single_record_tag",
            "average_record_tag",
            "entered_at",
            "meaningful_hash",
            "normalized_payload",
            "active",
            "last_observed_at",
            "processed_at",
        ],
    )


@transaction.atomic
def process_round_snapshot(
    round_id: str,
    round_payload: dict,
    run: IngestionRun | None = None,
    catchup_minutes: int = 60,
    observed_at=None,
) -> dict:
    started = monotonic()
    observed_at = observed_at or timezone.now()
    target = SubscriptionRound.objects.select_for_update().get(round_id=round_id)
    source_observation, source_created = store_observation(
        round_payload,
        "round_snapshot",
        run,
        METHOD,
        observed_at,
    )
    if source_observation.processed_at:
        logger.info("subscription_snapshot_duplicate_ignored round_id=%s", round_id)
        return {
            "rows": len(round_payload.get("results", [])),
            "additions": 0,
            "changes": 0,
            "removals": 0,
            "records_detected": 0,
            "records_withdrawn": 0,
            "duplicate": True,
        }

    try:
        current = normalize_round_snapshot(round_payload)
        stored_states = list(target.result_states.all())
        stored_by_id = {state.result_id: state for state in stored_states}
        previous = {
            state.result_id: _stored_result(state) for state in stored_states if state.active
        }
        initial_snapshot = not stored_states
        diff = diff_snapshots(previous, current)
        records_detected = 0
        records_withdrawn = 0
        dirty_scopes: set[tuple[str, str]] = set()
        catchup_threshold = observed_at - timedelta(minutes=max(catchup_minutes, 0))

        process_ids = set(diff.additions) | set(diff.changes)
        for result_id, result in current.items():
            should_evaluate = result_id in process_ids
            if initial_snapshot:
                should_evaluate = bool(result.entered_at and result.entered_at >= catchup_threshold)
            if should_evaluate:
                previous_result = previous.get(result_id)
                if _needs_record_synchronization(previous_result, result):
                    detected, withdrawn = _synchronize_result_records(
                        target, result, observed_at, source_observation
                    )
                    records_detected += detected
                    records_withdrawn += withdrawn
                dirty_scopes.update(
                    _synchronize_normalized_observations(
                        target,
                        previous_result,
                        result,
                        observed_at,
                        source_observation,
                    )
                )

        _persist_states(
            target,
            [current[result_id] for result_id in process_ids],
            stored_by_id,
            observed_at,
        )

        if diff.removals:
            removed_states = target.result_states.filter(result_id__in=diff.removals, active=True)
            for state in removed_states:
                stored = _stored_result(state)
                records_withdrawn += _withdraw_other_states(
                    state.stable_result_identity,
                    RecentRecordObservation.Kind.SINGLE,
                    observed_at,
                )
                records_withdrawn += _withdraw_other_states(
                    state.stable_result_identity,
                    RecentRecordObservation.Kind.AVERAGE,
                    observed_at,
                )
                for row in result_observations(target, stored, observed_at):
                    if retract_result_observation(
                        row.observation_key,
                        observed_at,
                        defer_classification=True,
                    ):
                        dirty_scopes.add((row.event_id, row.kind))
            removed_states.update(
                active=False, last_observed_at=observed_at, processed_at=observed_at
            )

        queued_scopes = mark_classification_scopes_dirty(
            dirty_scopes,
            observed_at=observed_at,
        )

        target.last_message_at = observed_at
        target.last_processed_snapshot_at = timezone.now()
        target.last_error = ""
        target.save(
            update_fields=[
                "last_message_at",
                "last_processed_snapshot_at",
                "last_error",
                "updated_at",
            ]
        )
        source_observation.processed_at = timezone.now()
        source_observation.processing_error = ""
        source_observation.save(update_fields=["processed_at", "processing_error"])
        if run and source_created:
            IngestionRun.objects.filter(pk=run.pk).update(
                observations_count=F("observations_count") + 1
            )

        stats = {
            "rows": len(current),
            "additions": len(diff.additions),
            "changes": len(diff.changes),
            "removals": len(diff.removals),
            "records_detected": records_detected,
            "records_withdrawn": records_withdrawn,
            "duplicate": False,
            "initial_snapshot": initial_snapshot,
            "classification_scopes_queued": queued_scopes,
            "duration_seconds": monotonic() - started,
        }
        logger.info(
            "subscription_snapshot_processed round_id=%s rows=%d additions=%d changes=%d removals=%d records_detected=%d classification_scopes=%d duration_seconds=%.3f",
            round_id,
            stats["rows"],
            stats["additions"],
            stats["changes"],
            stats["removals"],
            stats["records_detected"],
            stats["classification_scopes_queued"],
            stats["duration_seconds"],
        )
        return stats
    except Exception as exc:
        source_observation.processing_error = str(exc)
        source_observation.save(update_fields=["processing_error"])
        target.last_error = str(exc)
        target.save(update_fields=["last_error", "updated_at"])
        raise
