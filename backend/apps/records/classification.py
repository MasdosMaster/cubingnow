"""Revision-based incremental classification and narrow timeline repair."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from integrations.wca_live.result_values import is_complete

from .event_columns import event_field
from .models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    CanonicalResult,
    CanonicalResultRevision,
    LiveRecordsAverage,
    LiveRecordsSingle,
    ProcessedResult,
    ProcessedResultRecordLevel,
    RecordLevel,
)

REFERENCE_DATA = Path(__file__).resolve().parents[2] / "reference_data"
RECORD_LEVELS = (
    RecordLevel.WORLD,
    RecordLevel.CONTINENTAL,
    RecordLevel.NATIONAL,
    RecordLevel.PERSONAL,
)
DISPLAY_PRECEDENCE = {level: index for index, level in enumerate(RECORD_LEVELS)}


class StaleRevision(Exception):
    """The revision may be retained historically but cannot mutate live state."""


@lru_cache(maxsize=1)
def _countries() -> dict:
    with (REFERENCE_DATA / "countries.json").open(encoding="utf-8") as file:
        return json.load(file)["countries"]


def country_name(country_code: str) -> str:
    country = _countries().get((country_code or "").upper(), {})
    return country.get("wca_name") or country.get("display_name") or ""


def continent_name(country_code: str) -> str:
    return _countries().get((country_code or "").upper(), {}).get("continent", "")


def record_scope(result, record_level: str) -> str:
    if record_level == RecordLevel.WORLD:
        return "World"
    if record_level == RecordLevel.CONTINENTAL:
        return continent_name(result.country_code)
    if record_level == RecordLevel.NATIONAL:
        return country_name(result.country_code)
    return (result.competitor_wca_id or "").upper()


def classification_key(result) -> tuple:
    return (
        result.classification_at,
        result.canonical_result_id,
        getattr(result, "canonical_revision", getattr(result, "revision", 0)),
    )


def _table_model(kind: str, *, live: bool):
    if kind == CanonicalResult.Kind.SINGLE:
        return LiveRecordsSingle if live else BaselineRecordsSingle
    if kind == CanonicalResult.Kind.AVERAGE:
        return LiveRecordsAverage if live else BaselineRecordsAverage
    raise ValueError(f"Unsupported result kind {kind!r}")


def _record_value(
    *, kind: str, event_id: str, record_level: str, scope: str, live: bool, lock: bool = False
) -> int | None:
    field = event_field(event_id, kind)
    queryset = _table_model(kind, live=live).objects
    if lock:
        queryset = queryset.select_for_update()
        if live:
            # A brand-new WCA ID may have no baseline/live PR row. Creating the
            # empty row before reading provides a lock target for concurrent work.
            row, _created = queryset.get_or_create(
                record_holder=scope,
                record_type=record_level,
            )
            return getattr(row, field)
    row = queryset.filter(record_holder=scope, record_type=record_level).first()
    return getattr(row, field) if row else None


def _set_live_value(
    *, kind: str, event_id: str, record_level: str, scope: str, value: int | None
) -> None:
    field = event_field(event_id, kind)
    model = _table_model(kind, live=True)
    row, _created = model.objects.select_for_update().get_or_create(
        record_holder=scope,
        record_type=record_level,
    )
    if getattr(row, field) != value:
        setattr(row, field, value)
        row.save(update_fields=[field])


def _baseline_value(kind: str, event_id: str, record_level: str, scope: str) -> int | None:
    return _record_value(
        kind=kind,
        event_id=event_id,
        record_level=record_level,
        scope=scope,
        live=False,
    )


def seed_live_records_from_baseline() -> None:
    """Replace live cells with an exact copy of the active baseline tables."""

    for baseline_model, live_model in (
        (BaselineRecordsSingle, LiveRecordsSingle),
        (BaselineRecordsAverage, LiveRecordsAverage),
    ):
        live_model.objects.all().delete()
        fields = [field.name for field in baseline_model._meta.fields if field.name != "id"]
        live_model.objects.bulk_create(
            [live_model(**{field: getattr(row, field) for field in fields}) for row in baseline_model.objects.all()]
        )


def _active_baseline_absorbed_competitions() -> set[str]:
    metadata = BaselineMetadata.objects.filter(is_active=True).only(
        "absorbed_competition_ids"
    ).first()
    return set(metadata.absorbed_competition_ids if metadata else ())


def _is_absorbed(result, absorbed: set[str] | None = None) -> bool:
    absorbed = absorbed if absorbed is not None else _active_baseline_absorbed_competitions()
    return bool(result.wca_competition_id and result.wca_competition_id in absorbed)


def _revision_is_valid(revision: CanonicalResultRevision) -> bool:
    return (
        revision.action != CanonicalResultRevision.Action.RETRACTED
        and revision.canonical_status != CanonicalResult.Status.RETRACTED
        and revision.validation_status != CanonicalResult.ValidationStatus.REJECTED
        and is_complete(revision.value)
    )


def _processed_defaults(revision: CanonicalResultRevision) -> dict:
    return {
        "canonical_result_revision": revision,
        "identity_key": revision.identity_key,
        "wca_competition_id": revision.wca_competition_id,
        "competition_name": revision.competition_name,
        "competition_country_code": revision.competition_country_code,
        "competition_start_date": revision.competition_start_date,
        "competition_end_date": revision.competition_end_date,
        "competition_timezone": revision.competition_timezone,
        "competition_local_date": revision.competition_local_date,
        "timezone_resolution_status": revision.timezone_resolution_status,
        "timezone_resolution_reason": revision.timezone_resolution_reason,
        "round_id": revision.round_id,
        "round_number": revision.round_number,
        "round_name": revision.round_name,
        "event_id": revision.event_id,
        "event_name": revision.event_name,
        "competitor_name": revision.competitor_name,
        "competitor_wca_id": revision.competitor_wca_id,
        "country_code": revision.country_code,
        "kind": revision.kind,
        "value": revision.value,
        "formatted_result": revision.formatted_result,
        "entered_at": revision.entered_at,
        "first_observed_at": revision.first_observed_at,
        "last_observed_at": revision.last_observed_at,
        "source_url": revision.source_url,
        "canonical_status": revision.canonical_status,
        "validation_status": revision.validation_status,
        "validation_reason": revision.validation_reason,
        "classification_at": revision.classification_at,
        "classified_at": timezone.now(),
        "is_valid_result": _revision_is_valid(revision),
        "invalidity_reason": (
            "retracted_result"
            if revision.action == CanonicalResultRevision.Action.RETRACTED
            else ("invalid_canonical_result" if not _revision_is_valid(revision) else "")
        ),
    }


def _materialize_processed_result(revision: CanonicalResultRevision) -> ProcessedResult:
    processed, _created = ProcessedResult.objects.update_or_create(
        canonical_result=revision.canonical_result,
        canonical_revision=revision.revision,
        defaults=_processed_defaults(revision),
    )
    for level in RECORD_LEVELS:
        ProcessedResultRecordLevel.objects.get_or_create(
            processed_result=processed,
            record_level=level,
            defaults={"record_scope": record_scope(processed, level)},
        )
    return processed


def _invalidate_older_revisions(processed: ProcessedResult, action: str) -> None:
    reason = (
        "retracted_result"
        if action == CanonicalResultRevision.Action.RETRACTED
        else "corrected_result"
    )
    older_ids = list(
        ProcessedResult.objects.filter(
            canonical_result=processed.canonical_result,
            canonical_revision__lt=processed.canonical_revision,
            is_valid_result=True,
        ).values_list("pk", flat=True)
    )
    if not older_ids:
        return
    ProcessedResult.objects.filter(pk__in=older_ids).update(
        is_valid_result=False,
        invalidity_reason=reason,
        replacement=processed,
        updated_at=timezone.now(),
    )
    ProcessedResultRecordLevel.objects.filter(processed_result_id__in=older_ids).update(
        currently_holds=False,
        updated_at=timezone.now(),
    )


def _compare(value: int, incumbent: int | None) -> str:
    if incumbent is None or value < incumbent:
        return ProcessedResultRecordLevel.ClassificationOutcome.BROKEN
    if value == incumbent:
        return ProcessedResultRecordLevel.ClassificationOutcome.TIED
    return ProcessedResultRecordLevel.ClassificationOutcome.NONE


def _current_timeline(record_level: str, event_id: str, kind: str, scope: str):
    rows = list(
        ProcessedResult.objects.filter(
            event_id=event_id,
            kind=kind,
            is_valid_result=True,
            canonical_revision=F("canonical_result__revision"),
            canonical_result__status__in=[
                CanonicalResult.Status.ACTIVE,
                CanonicalResult.Status.CORRECTED,
            ],
            record_levels__record_level=record_level,
            record_levels__record_scope=scope,
        ).distinct()
    )
    absorbed = _active_baseline_absorbed_competitions()
    rows = [row for row in rows if not _is_absorbed(row, absorbed)]
    rows.sort(key=classification_key)
    return rows


def _assert_revision_is_current(revision: CanonicalResultRevision) -> None:
    head = CanonicalResult.objects.select_for_update().get(pk=revision.canonical_result_id)
    if head.revision != revision.revision:
        raise StaleRevision


def _same_competition(left: ProcessedResult, right: ProcessedResult) -> bool:
    if left.wca_competition_id and right.wca_competition_id:
        return left.wca_competition_id == right.wca_competition_id
    return left.competition_name == right.competition_name


def _same_round_key(row: ProcessedResult, scope: str) -> tuple:
    competition = row.wca_competition_id or row.competition_name
    round_key = row.round_id or row.round_number
    return competition, row.event_id, round_key, row.kind, scope


def _same_day_key(row: ProcessedResult, scope: str) -> tuple | None:
    if row.competition_local_date is None:
        return None
    return row.competition_local_date, row.event_id, row.kind, scope


def _adjudicate_timeline(record_level: str, event_id: str, kind: str, scope: str) -> None:
    results = _current_timeline(record_level, event_id, kind, scope)
    levels = {
        row.processed_result_id: row
        for row in ProcessedResultRecordLevel.objects.filter(
            processed_result__in=results,
            record_level=record_level,
            record_scope=scope,
        )
    }
    qualifying = [
        result
        for result in results
        if levels[result.pk].classification_outcome
        != ProcessedResultRecordLevel.ClassificationOutcome.NONE
    ]
    now = timezone.now()
    for result in results:
        child = levels[result.pk]
        child.recognition_status = (
            ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            if result in qualifying
            else ProcessedResultRecordLevel.RecognitionStatus.NOT_APPLICABLE
        )
        child.is_shared_tie = False
        child.currently_holds = False
        child.ceased_holding_reason = ""
        child.ceased_holding_by = None
        child.superseded_by = None

    round_groups: dict[tuple, list[ProcessedResult]] = defaultdict(list)
    day_groups: dict[tuple, list[ProcessedResult]] = defaultdict(list)
    value_groups: Counter[int] = Counter(row.value for row in qualifying)
    for result in qualifying:
        round_groups[_same_round_key(result, scope)].append(result)
        day_key = _same_day_key(result, scope)
        if day_key is not None:
            day_groups[day_key].append(result)

    for group in round_groups.values():
        best = min(row.value for row in group)
        best_rows = [row for row in group if row.value == best]
        winner = min(best_rows, key=classification_key)
        for result in group:
            if result.value > best:
                child = levels[result.pk]
                child.recognition_status = (
                    ProcessedResultRecordLevel.RecognitionStatus.SUPERSEDED_SAME_ROUND
                )
                child.superseded_by = winner

    for group in day_groups.values():
        best = min(row.value for row in group)
        best_rows = [row for row in group if row.value == best]
        winner = min(best_rows, key=classification_key)
        for result in group:
            child = levels[result.pk]
            if (
                result.value > best
                and child.recognition_status
                != ProcessedResultRecordLevel.RecognitionStatus.SUPERSEDED_SAME_ROUND
            ):
                child.recognition_status = (
                    ProcessedResultRecordLevel.RecognitionStatus.SUPERSEDED_SAME_DAY
                )
                child.superseded_by = winner

    final_value = _record_value(
        kind=kind,
        event_id=event_id,
        record_level=record_level,
        scope=scope,
        live=True,
    )
    for result in qualifying:
        child = levels[result.pk]
        child.is_shared_tie = value_groups[result.value] > 1
        if (
            child.recognition_status
            == ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            and result.value == final_value
        ):
            child.currently_holds = True
            continue
        if child.recognition_status != ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED:
            continue
        breaker = next(
            (
                later
                for later in qualifying
                if classification_key(later) > classification_key(result)
                and later.value < result.value
                and levels[later.pk].recognition_status
                == ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            ),
            None,
        )
        if breaker is None:
            continue
        same_person = bool(
            result.competitor_wca_id
            and result.competitor_wca_id == breaker.competitor_wca_id
        )
        same_competition = _same_competition(result, breaker)
        if same_competition and same_person:
            reason = ProcessedResultRecordLevel.CeasedHoldingReason.SELF_LATER_ROUND
        elif same_competition:
            reason = ProcessedResultRecordLevel.CeasedHoldingReason.OTHER_LATER_ROUND
        elif same_person:
            reason = ProcessedResultRecordLevel.CeasedHoldingReason.SELF_OTHER_COMPETITION
        else:
            reason = ProcessedResultRecordLevel.CeasedHoldingReason.OTHER_OTHER_COMPETITION
        child.ceased_holding_reason = reason
        child.ceased_holding_by = breaker

    if levels:
        for child in levels.values():
            child.updated_at = now
        ProcessedResultRecordLevel.objects.bulk_update(
            levels.values(),
            [
                "recognition_status",
                "is_shared_tie",
                "currently_holds",
                "ceased_holding_reason",
                "ceased_holding_by",
                "superseded_by",
                "updated_at",
            ],
        )


def _is_out_of_order(processed: ProcessedResult) -> bool:
    for level in RECORD_LEVELS:
        scope = record_scope(processed, level)
        if not scope:
            continue
        if any(
            row.pk != processed.pk
            and classification_key(row) > classification_key(processed)
            for row in _current_timeline(level, processed.event_id, processed.kind, scope)
        ):
            return True
    return False


@transaction.atomic
def _classify_incremental(
    revision: CanonicalResultRevision, processed: ProcessedResult
) -> None:
    _assert_revision_is_current(revision)
    if not processed.is_valid_result or _is_absorbed(processed):
        return
    affected = []
    for level in RECORD_LEVELS:
        scope = record_scope(processed, level)
        child = processed.record_levels.get(record_level=level)
        child.record_scope = scope
        if not scope:
            child.classification_outcome = (
                ProcessedResultRecordLevel.ClassificationOutcome.NONE
            )
            child.recognition_status = (
                ProcessedResultRecordLevel.RecognitionStatus.NOT_APPLICABLE
            )
            child.save()
            continue
        incumbent = _record_value(
            kind=processed.kind,
            event_id=processed.event_id,
            record_level=level,
            scope=scope,
            live=True,
            lock=True,
        )
        outcome = _compare(processed.value, incumbent)
        child.incumbent_value = incumbent
        child.classification_outcome = outcome
        child.recognition_status = (
            ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            if outcome != ProcessedResultRecordLevel.ClassificationOutcome.NONE
            else ProcessedResultRecordLevel.RecognitionStatus.NOT_APPLICABLE
        )
        child.save()
        if outcome == ProcessedResultRecordLevel.ClassificationOutcome.BROKEN:
            _set_live_value(
                kind=processed.kind,
                event_id=processed.event_id,
                record_level=level,
                scope=scope,
                value=processed.value,
            )
        affected.append((level, scope))
    for level, scope in affected:
        _adjudicate_timeline(level, processed.event_id, processed.kind, scope)


@transaction.atomic
def repair_timeline(
    record_level: str,
    event_id: str,
    kind: str,
    scope: str,
    replay_from=None,
    *,
    guard_revision: CanonicalResultRevision | None = None,
) -> None:
    """Replay one exact WR/CR/NR/PR timeline from its export baseline.

    ``replay_from`` documents the affected point and is intentionally accepted by
    the generic repair API.  The implementation recomputes the narrow timeline in
    full; this keeps the recovery path simple without returning to event/kind-wide
    production replays.
    """

    if not scope:
        return
    # Serialize only this live cell.  Canonical rows remain unlocked during the
    # potentially expensive replay.
    _record_value(
        kind=kind,
        event_id=event_id,
        record_level=record_level,
        scope=scope,
        live=True,
        lock=True,
    )
    incumbent = _baseline_value(kind, event_id, record_level, scope)
    timeline = _current_timeline(record_level, event_id, kind, scope)
    for result in timeline:
        child, _created = ProcessedResultRecordLevel.objects.get_or_create(
            processed_result=result,
            record_level=record_level,
            defaults={"record_scope": scope},
        )
        outcome = _compare(result.value, incumbent)
        child.record_scope = scope
        child.incumbent_value = incumbent
        child.classification_outcome = outcome
        child.recognition_status = (
            ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            if outcome != ProcessedResultRecordLevel.ClassificationOutcome.NONE
            else ProcessedResultRecordLevel.RecognitionStatus.NOT_APPLICABLE
        )
        child.is_shared_tie = False
        child.currently_holds = False
        child.ceased_holding_reason = ""
        child.ceased_holding_by = None
        child.superseded_by = None
        child.save()
        if outcome == ProcessedResultRecordLevel.ClassificationOutcome.BROKEN:
            incumbent = result.value
    if guard_revision is not None:
        _assert_revision_is_current(guard_revision)
    _set_live_value(
        kind=kind,
        event_id=event_id,
        record_level=record_level,
        scope=scope,
        value=incumbent,
    )
    _adjudicate_timeline(record_level, event_id, kind, scope)


def _affected_scopes(
    revision: CanonicalResultRevision, processed: ProcessedResult
) -> set[tuple[str, str]]:
    affected = {
        (level, scope)
        for level in RECORD_LEVELS
        if (scope := record_scope(processed, level))
    }
    previous = (
        CanonicalResultRevision.objects.filter(
            canonical_result_id=revision.canonical_result_id,
            revision__lt=revision.revision,
        )
        .order_by("-revision")
        .first()
    )
    if previous:
        affected.update(
            (level, scope)
            for level in RECORD_LEVELS
            if (scope := record_scope(previous, level))
        )
    return affected


def _classify_stale_historical(processed: ProcessedResult) -> None:
    """Materialize a stale revision without touching current live state."""

    for level in RECORD_LEVELS:
        scope = record_scope(processed, level)
        incumbent = _baseline_value(processed.kind, processed.event_id, level, scope) if scope else None
        earlier = list(
            ProcessedResult.objects.filter(
                event_id=processed.event_id,
                kind=processed.kind,
                record_levels__record_level=level,
                record_levels__record_scope=scope,
            )
            .exclude(pk=processed.pk)
            .distinct()
        )
        earlier = [
            row
            for row in earlier
            if classification_key(row) < classification_key(processed)
        ]
        earlier.sort(key=classification_key)
        for row in earlier:
            if incumbent is None or row.value < incumbent:
                incumbent = row.value
        child = processed.record_levels.get(record_level=level)
        child.record_scope = scope
        child.incumbent_value = incumbent
        child.classification_outcome = (
            _compare(processed.value, incumbent)
            if processed.is_valid_result and scope
            else ProcessedResultRecordLevel.ClassificationOutcome.NONE
        )
        child.recognition_status = (
            ProcessedResultRecordLevel.RecognitionStatus.RECOGNIZED
            if child.classification_outcome
            != ProcessedResultRecordLevel.ClassificationOutcome.NONE
            else ProcessedResultRecordLevel.RecognitionStatus.NOT_APPLICABLE
        )
        child.currently_holds = False
        child.save()


def classify_revision(
    revision: CanonicalResultRevision,
    *,
    publish_notifications: bool = True,
) -> bool:
    """Classify one immutable revision; return whether it was current at commit."""

    with transaction.atomic():
        processed = _materialize_processed_result(revision)
        _invalidate_older_revisions(processed, revision.action)

    if revision.canonical_result.revision > revision.revision:
        _classify_stale_historical(processed)
        return False

    if _is_absorbed(processed):
        # Export-absorbed rows remain a revision-level historical projection but
        # can never advance live state on top of the export that already contains
        # them.
        _classify_stale_historical(processed)
        return True

    needs_repair = revision.revision > 1 or _is_out_of_order(processed)
    try:
        if needs_repair:
            with transaction.atomic():
                for level, scope in sorted(_affected_scopes(revision, processed)):
                    repair_timeline(
                        level,
                        processed.event_id,
                        processed.kind,
                        scope,
                        replay_from=processed.classification_at,
                    )
                # All timeline writes above roll back together if this final,
                # short-lived head lock discovers a newer canonical revision.
                _assert_revision_is_current(revision)
        else:
            _classify_incremental(revision, processed)
    except StaleRevision:
        _classify_stale_historical(processed)
        return False

    if publish_notifications and processed.is_valid_result:
        from apps.notifications.services import publish_processed_result_after_commit

        publish_processed_result_after_commit(processed.pk)
    return True


def rebuild_classification_from_scratch(*, publish_notifications: bool = False) -> None:
    """Slow deterministic recovery path and incremental correctness oracle."""

    if not BaselineMetadata.objects.filter(is_active=True).exists():
        raise RuntimeError("Classification rebuild requires an active WCA baseline")
    ProcessedResultRecordLevel.objects.all().delete()
    ProcessedResult.objects.all().delete()
    seed_live_records_from_baseline()
    revisions = (
        CanonicalResultRevision.objects.select_related("canonical_result")
        .annotate(_classification_at=Coalesce("entered_at", "first_observed_at"))
        .order_by("_classification_at", "canonical_result_id", "revision")
    )
    for revision in revisions.iterator():
        classify_revision(revision, publish_notifications=publish_notifications)


def rebuild_live_records_after_baseline_refresh() -> None:
    """Rebuild current live cells without erasing retained revision history."""

    seed_live_records_from_baseline()
    absorbed = _active_baseline_absorbed_competitions()
    heads = CanonicalResultRevision.objects.select_related("canonical_result").filter(
        revision=F("canonical_result__revision"),
        canonical_result__status__in=[
            CanonicalResult.Status.ACTIVE,
            CanonicalResult.Status.CORRECTED,
        ],
    )
    heads = heads.annotate(
        _classification_at=Coalesce("entered_at", "first_observed_at")
    ).order_by("_classification_at", "canonical_result_id", "revision")
    for revision in heads.iterator():
        if revision.wca_competition_id in absorbed:
            continue
        classify_revision(revision, publish_notifications=False)


# Administrative compatibility name; production work is revision based.
def reclassify_all(*, publish_notifications: bool = False) -> None:
    rebuild_classification_from_scratch(publish_notifications=publish_notifications)
