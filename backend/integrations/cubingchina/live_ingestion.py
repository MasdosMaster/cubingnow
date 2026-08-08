import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.records.models import (
    CubingChinaResultState,
    CubingChinaRoundTarget,
    IngestionRun,
    IngestionWorkerStatus,
    RecentRecordObservation,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)
from integrations.wca_live.ingestion import (
    RECORD_LEVELS,
    persist_record_candidate,
    store_observation,
)
from integrations.wca_live.result_values import is_complete
from integrations.wca_live.schemas import RecordCandidate

from .live_schemas import NormalizedCubingChinaResult
from .live_snapshots import diff_snapshots, normalize_result, normalize_snapshot
from .observations import result_observations

logger = logging.getLogger(__name__)
METHOD = RecentRecordObservation.IngestionMethod.CUBINGCHINA_WEBSOCKET


def _stored_result(state: CubingChinaResultState) -> NormalizedCubingChinaResult:
    return NormalizedCubingChinaResult(
        result_id=state.result_id,
        stable_result_identity=state.stable_result_identity,
        competitor_number=state.competitor_number,
        competitor_name=state.competitor_name,
        competitor_wca_id=state.competitor_wca_id,
        region=state.region,
        country_code=state.country_code,
        attempts=tuple(state.attempts),
        best=state.best,
        average=state.average,
        single_record_tag=state.single_record_tag,
        average_record_tag=state.average_record_tag,
        meaningful_hash=state.meaningful_hash,
        payload=state.normalized_payload,
    )


def _candidate(
    target: CubingChinaRoundTarget,
    result: NormalizedCubingChinaResult,
    kind: str,
    value: int,
    level: str,
    observed_at,
) -> RecordCandidate:
    competition = target.competition
    source_url = (
        f"https://cubing.com/live/{competition.slug}"
        f"#!/event/{target.event_id}/{target.round_id}/all"
    )
    return RecordCandidate(
        stable_result_identity=result.stable_result_identity,
        wca_live_record_id="",
        wca_live_result_id="",
        wca_live_competition_id="",
        wca_competition_id=competition.wca_competition_id,
        competition_name=competition.competition_name,
        competition_start_date=competition.competition_start_date,
        competition_end_date=competition.competition_end_date,
        round_id=target.round_id,
        round_number=target.round_number,
        round_name=target.round_name,
        event_id=target.event_id,
        event_name=target.event_name,
        competitor_name=result.competitor_name,
        competitor_wca_id=result.competitor_wca_id,
        competitor_wca_live_id="",
        country_code=result.country_code,
        kind=kind,
        raw_result=value,
        record_level=level,
        source_url=source_url,
        source_update_timestamp=None,
        observed_at=observed_at,
        source="cubingchina",
        source_result_id=result.result_id,
        source_competition_id=str(competition.cubingchina_id or ""),
        source_competitor_id=str(result.competitor_number),
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


def _synchronize_records(
    target, result, observed_at, raw_observation=None
) -> tuple[int, int]:
    detected = 0
    withdrawn = 0
    pairs = (
        (RecentRecordObservation.Kind.SINGLE, result.best, result.single_record_tag),
        (RecentRecordObservation.Kind.AVERAGE, result.average, result.average_record_tag),
    )
    for kind, value, tag in pairs:
        if tag and tag not in RECORD_LEVELS:
            logger.warning(
                "cubingchina_unknown_record_tag result_id=%s kind=%s tag=%s",
                result.result_id,
                kind,
                tag,
            )
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
    previous: NormalizedCubingChinaResult | None,
    current: NormalizedCubingChinaResult,
    observed_at,
    raw_observation,
) -> None:
    current_rows = result_observations(
        target, current, observed_at, raw_observation.pk
    )
    current_keys = {row.observation_key for row in current_rows}
    for row in current_rows:
        reconcile_result_observation(row)
    if previous is not None:
        for row in result_observations(target, previous, observed_at):
            if row.observation_key not in current_keys:
                retract_result_observation(row.observation_key, observed_at)


def _persist_state(target, result, observed_at) -> None:
    state, created = CubingChinaResultState.objects.get_or_create(
        round=target,
        result_id=result.result_id,
        defaults={
            "stable_result_identity": result.stable_result_identity,
            "competitor_number": result.competitor_number,
            "meaningful_hash": result.meaningful_hash,
            "first_observed_at": observed_at,
            "last_observed_at": observed_at,
            "processed_at": observed_at,
        },
    )
    state.stable_result_identity = result.stable_result_identity
    state.competitor_number = result.competitor_number
    state.competitor_name = result.competitor_name
    state.competitor_wca_id = result.competitor_wca_id
    state.region = result.region
    state.country_code = result.country_code
    state.attempts = list(result.attempts)
    state.best = result.best
    state.average = result.average
    state.single_record_tag = result.single_record_tag
    state.average_record_tag = result.average_record_tag
    state.meaningful_hash = result.meaningful_hash
    state.normalized_payload = result.payload
    state.active = True
    state.last_observed_at = observed_at
    state.processed_at = observed_at
    if created:
        state.first_observed_at = observed_at
    state.save()


def _mark_snapshot_success(target, observed_at) -> None:
    target.last_snapshot_at = observed_at
    target.last_error = ""
    target.save(update_fields=["last_snapshot_at", "last_error", "updated_at"])
    target.competition.last_snapshot_at = observed_at
    target.competition.last_message_at = observed_at
    target.competition.last_error = ""
    target.competition.save(
        update_fields=["last_snapshot_at", "last_message_at", "last_error", "updated_at"]
    )
    IngestionWorkerStatus.objects.filter(ingestion_method=METHOD).update(
        heartbeat_at=observed_at,
        last_message_at=observed_at,
        last_successful_snapshot_at=observed_at,
    )


def _finish_source_observation(source_observation, source_created, run) -> None:
    source_observation.processed_at = timezone.now()
    source_observation.processing_error = ""
    source_observation.save(update_fields=["processed_at", "processing_error"])
    if run and source_created:
        IngestionRun.objects.filter(pk=run.pk).update(
            observations_count=F("observations_count") + 1
        )


@transaction.atomic
def process_round_snapshot(
    round_target_id: int,
    rows: list[dict],
    users: dict,
    run: IngestionRun | None = None,
    observed_at=None,
) -> dict:
    observed_at = observed_at or timezone.now()
    target = CubingChinaRoundTarget.objects.select_related("competition").select_for_update().get(
        pk=round_target_id
    )
    source_payload = {
        "competitionId": target.competition.cubingchina_id,
        "event": target.event_id,
        "round": target.round_id,
        "results": rows,
        "users": users,
    }
    source_observation, source_created = store_observation(
        source_payload,
        "round_snapshot",
        run,
        METHOD,
        observed_at,
        source="cubingchina",
    )
    duplicate = not source_created
    try:
        current = normalize_snapshot(
            rows,
            users,
            int(target.competition.cubingchina_id),
            target.event_id,
            target.round_id,
        )
        stored_states = list(target.result_states.all())
        previous = {
            state.result_id: _stored_result(state) for state in stored_states if state.active
        }
        initial_snapshot = not stored_states
        diff = diff_snapshots(previous, current)
        records_detected = 0
        records_withdrawn = 0
        process_ids = set(diff.additions) | set(diff.changes)
        for result_id, result in current.items():
            if initial_snapshot or result_id in process_ids:
                detected, withdrawn = _synchronize_records(
                    target, result, observed_at, source_observation
                )
                records_detected += detected
                records_withdrawn += withdrawn
                _synchronize_normalized_observations(
                    target,
                    previous.get(result_id),
                    result,
                    observed_at,
                    source_observation,
                )
            _persist_state(target, result, observed_at)
        if diff.removals:
            removed_states = target.result_states.filter(
                result_id__in=diff.removals, active=True
            )
            for state in removed_states:
                stored = _stored_result(state)
                for kind in (
                    RecentRecordObservation.Kind.SINGLE,
                    RecentRecordObservation.Kind.AVERAGE,
                ):
                    records_withdrawn += _withdraw_other_states(
                        state.stable_result_identity, kind, observed_at
                    )
                for row in result_observations(target, stored, observed_at):
                    retract_result_observation(row.observation_key, observed_at)
            removed_states.update(
                active=False,
                last_observed_at=observed_at,
                processed_at=observed_at,
            )
        _mark_snapshot_success(target, observed_at)
        _finish_source_observation(source_observation, source_created, run)
        return {
            "rows": len(current),
            "additions": len(diff.additions),
            "changes": len(diff.changes),
            "removals": len(diff.removals),
            "records_detected": records_detected,
            "records_withdrawn": records_withdrawn,
            "duplicate": duplicate,
            "initial_snapshot": initial_snapshot,
        }
    except Exception as exc:
        source_observation.processing_error = str(exc)
        source_observation.save(update_fields=["processing_error"])
        target.last_error = str(exc)
        target.save(update_fields=["last_error", "updated_at"])
        raise


@transaction.atomic
def process_result_update(
    round_target_id: int,
    payload: dict,
    users: dict,
    event_type: str,
    run: IngestionRun | None = None,
    observed_at=None,
) -> dict:
    observed_at = observed_at or timezone.now()
    target = CubingChinaRoundTarget.objects.select_related("competition").select_for_update().get(
        pk=round_target_id
    )
    source_observation, source_created = store_observation(
        payload,
        event_type,
        run,
        METHOD,
        observed_at,
        source="cubingchina",
    )
    duplicate = not source_created
    try:
        result = normalize_result(
            payload,
            users,
            int(target.competition.cubingchina_id),
            expected_event_id=target.event_id,
            expected_round_id=target.round_id,
        )
        state = target.result_states.filter(result_id=result.result_id, active=True).first()
        changed = state is None or state.meaningful_hash != result.meaningful_hash
        records_detected = 0
        records_withdrawn = 0
        if changed:
            records_detected, records_withdrawn = _synchronize_records(
                target, result, observed_at, source_observation
            )
            _synchronize_normalized_observations(
                target,
                _stored_result(state) if state is not None else None,
                result,
                observed_at,
                source_observation,
            )
        _persist_state(target, result, observed_at)
        _mark_snapshot_success(target, observed_at)
        _finish_source_observation(source_observation, source_created, run)
        return {
            "changed": changed,
            "duplicate": duplicate,
            "records_detected": records_detected,
            "records_withdrawn": records_withdrawn,
        }
    except Exception as exc:
        source_observation.processing_error = str(exc)
        source_observation.save(update_fields=["processing_error"])
        raise


@transaction.atomic
def store_live_event(
    payload: dict,
    event_type: str,
    run: IngestionRun | None = None,
    observed_at=None,
) -> None:
    observed_at = observed_at or timezone.now()
    observation, created = store_observation(
        payload,
        event_type,
        run,
        METHOD,
        observed_at,
        source="cubingchina",
    )
    if not observation.processed_at:
        _finish_source_observation(observation, created, run)
