from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.notifications.models import NotificationEvent
from apps.records import classification_work
from apps.records.classification import (
    rebuild_classification_from_scratch,
    seed_live_records_from_baseline,
)
from apps.records.classification_work import (
    claim_next_work,
    process_claimed_work,
    process_ready_work,
)
from apps.records.domain import NormalizedResultObservation
from apps.records.models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    CanonicalResultRevision,
    ClassificationWork,
    LiveRecordsAverage,
    LiveRecordsSingle,
    ProcessedResult,
    ProcessedResultRecordLevel,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)
from integrations.wca.record_validation import refresh_wca_record_validations

NOW = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)


def observed(
    *,
    source_result="source-result-1",
    competition="TestOpen2026",
    competition_name="Test Open 2026",
    competitor="2020TEST01",
    competitor_name="Test Cuber",
    round_number=1,
    round_id="round-1",
    value=900,
    country_code="NL",
    event_id="333",
    event_name="3x3x3 Cube",
    kind="single",
    claim="",
    source="wca_live",
    ingestion_method="api_polling",
    entered_at=NOW,
    observed_at=NOW,
):
    return NormalizedResultObservation(
        source=source,
        ingestion_method=ingestion_method,
        source_result_identity=source_result,
        source_competition_id=competition,
        source_competitor_id=competitor,
        wca_competition_id=competition,
        competition_name=competition_name,
        competition_country_code="NL",
        competition_start_date=date(2026, 8, 8),
        competition_end_date=date(2026, 8, 9),
        competition_timezone="Europe/Amsterdam",
        round_id=round_id,
        round_number=round_number,
        round_name=f"Round {round_number}",
        event_id=event_id,
        event_name=event_name,
        competitor_name=competitor_name,
        competitor_wca_id=competitor,
        country_code=country_code,
        kind=kind,
        value=value,
        source_record_tag=claim,
        entered_at=entered_at,
        observed_at=observed_at,
        source_url="https://example.test/live",
        normalized_payload={"value": value, "claim": claim},
    )


def seed_baseline(*, kind="single", value=1000, people=()):
    activate_baseline()
    model = BaselineRecordsSingle if kind == "single" else BaselineRecordsAverage
    model.objects.create(record_holder="World", record_type="WR", event_333=value)
    model.objects.create(record_holder="Europe", record_type="CR", event_333=value)
    model.objects.create(record_holder="Netherlands", record_type="NR", event_333=value)
    for person in people:
        model.objects.create(record_holder=person, record_type="PR", event_333=value)
    seed_live_records_from_baseline()


def seed_two_country_baseline(*, value=1000, people=()):
    activate_baseline()
    BaselineRecordsSingle.objects.create(
        record_holder="World", record_type="WR", event_333=value
    )
    for continent in ("Europe", "South America"):
        BaselineRecordsSingle.objects.create(
            record_holder=continent, record_type="CR", event_333=value
        )
    for country in ("Netherlands", "Argentina"):
        BaselineRecordsSingle.objects.create(
            record_holder=country, record_type="NR", event_333=value
        )
    for person in people:
        BaselineRecordsSingle.objects.create(
            record_holder=person, record_type="PR", event_333=value
        )
    seed_live_records_from_baseline()


def activate_baseline():
    BaselineMetadata.objects.get_or_create(
        is_active=True,
        defaults={
            "export_generated_at": NOW,
            "downloaded_at": NOW,
            "source_filename": "test-export.zip",
            "source_version": "test",
            "rebuilt_at": NOW,
            "absorbed_competition_ids": [],
        },
    )


def settle():
    worker = "test-worker"
    while process_ready_work(worker, limit=100):
        pass


def level(result, record_level):
    return ProcessedResultRecordLevel.objects.get(
        processed_result=result, record_level=record_level
    )


@pytest.mark.django_db
def test_reconciliation_creates_immutable_revision_and_only_one_work_for_duplicates():
    data = observed(claim="WR")
    row = reconcile_result_observation(data)
    reconcile_result_observation(replace(data, observed_at=NOW + timedelta(seconds=5)))

    result = row.canonical_result
    assert result.revisions.count() == 1
    revision = result.revisions.get()
    assert revision.value == 900
    assert revision.action == CanonicalResultRevision.Action.ACTIVE
    assert ClassificationWork.objects.filter(canonical_result=result).count() == 1
    assert not hasattr(result, "processing_action")
    revision.value = 800
    with pytest.raises(ValidationError, match="immutable"):
        revision.save()


@pytest.mark.django_db
def test_provider_record_tag_change_does_not_create_classifier_work():
    data = observed(claim="NR")
    row = reconcile_result_observation(data)
    reconcile_result_observation(
        replace(data, source_record_tag="WR", observed_at=NOW + timedelta(seconds=1))
    )

    assert row.canonical_result.revisions.count() == 1
    assert ClassificationWork.objects.count() == 1


@pytest.mark.django_db
def test_revision_work_stays_pending_until_an_active_baseline_exists():
    reconcile_result_observation(observed())

    assert claim_next_work("worker") is None
    assert ClassificationWork.objects.get().status == "pending"


@pytest.mark.django_db
def test_wca_validation_refresh_creates_revisions_without_trusting_provider_tags():
    matching = reconcile_result_observation(
        observed(
            source_result="matching",
            competitor="2020MATCH01",
            value=900,
            source="cubingchina",
            ingestion_method="cubingchina_websocket",
        )
    )
    mismatching = reconcile_result_observation(
        observed(
            source_result="mismatching",
            competitor="2020MISS01",
            value=1100,
            claim="WR",
            source="cubingchina",
            ingestion_method="cubingchina_websocket",
        )
    )

    refresh_wca_record_validations(
        {
            "world_records": {"333": {"single": 1000}},
            "continental_records": {"_Europe": {"333": {"single": 1000}}},
            "national_records": {"Netherlands": {"333": {"single": 1000}}},
        },
        source_url="https://example.test/api/v0/records",
    )

    matching.canonical_result.refresh_from_db()
    mismatching.canonical_result.refresh_from_db()
    assert matching.canonical_result.validation_status == "verified"
    assert matching.canonical_result.revision == 2
    assert mismatching.canonical_result.validation_status == "pending"
    assert mismatching.canonical_result.validation_reason == "wca_records_api_no_record_match"
    assert mismatching.canonical_result.revision == 2
    assert ClassificationWork.objects.count() == 4


@pytest.mark.django_db
def test_missing_competition_timezone_is_flagged_instead_of_guessed():
    row = reconcile_result_observation(
        replace(observed(), competition_timezone="", competition_local_date=None)
    )

    revision = row.canonical_result.revisions.get()
    assert revision.competition_local_date is None
    assert revision.timezone_resolution_status == "unresolved"
    assert revision.timezone_resolution_reason == "missing_or_ambiguous_source_timezone"


@pytest.mark.django_db
def test_normal_single_classifies_all_four_levels_independently_and_ignores_tags():
    seed_baseline(people=("2020TEST01",))
    row = reconcile_result_observation(observed(value=900, claim=""))
    settle()

    processed = ProcessedResult.objects.get(canonical_result=row.canonical_result)
    assert set(
        processed.record_levels.values_list("record_level", "classification_outcome")
    ) == {("WR", "broken"), ("CR", "broken"), ("NR", "broken"), ("PR", "broken")}
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 900
    assert processed.competition_local_date == date(2026, 8, 8)


@pytest.mark.django_db
def test_non_record_still_creates_processed_result_and_average_uses_average_table():
    seed_baseline(kind="average", value=800, people=("2020TEST01",))
    row = reconcile_result_observation(observed(kind="average", value=900, claim="WR"))
    settle()

    processed = ProcessedResult.objects.get(canonical_result=row.canonical_result)
    assert set(processed.record_levels.values_list("classification_outcome", flat=True)) == {
        "none"
    }
    assert LiveRecordsAverage.objects.get(record_holder="World").event_333 == 800


@pytest.mark.django_db
def test_encoded_multi_blind_uses_lower_numeric_value_as_better():
    activate_baseline()
    baseline = 970010001
    for holder, record_type in (
        ("World", "WR"),
        ("Europe", "CR"),
        ("Netherlands", "NR"),
        ("2020TEST01", "PR"),
    ):
        BaselineRecordsSingle.objects.create(
            record_holder=holder,
            record_type=record_type,
            event_333mbf=baseline,
        )
    seed_live_records_from_baseline()
    row = reconcile_result_observation(
        observed(
            event_id="333mbf",
            event_name="3x3x3 Multi-Blind",
            value=960010001,
        )
    )
    settle()

    processed = ProcessedResult.objects.get(canonical_result=row.canonical_result)
    assert set(processed.record_levels.values_list("classification_outcome", flat=True)) == {
        "broken"
    }
    assert LiveRecordsSingle.objects.get(
        record_holder="World", record_type="WR"
    ).event_333mbf == 960010001


@pytest.mark.django_db
def test_equal_record_is_tied_and_marks_both_holders_as_shared():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=900)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="bob",
            competitor="2020BOBB01",
            value=900,
            entered_at=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    settle()

    wrs = ProcessedResultRecordLevel.objects.filter(record_level="WR").order_by(
        "processed_result__classification_at"
    )
    assert list(wrs.values_list("classification_outcome", flat=True)) == ["broken", "tied"]
    assert set(wrs.values_list("is_shared_tie", flat=True)) == {True}
    assert set(wrs.values_list("recognition_status", flat=True)) == {"recognized"}
    for record_level in ("CR", "NR"):
        rows = ProcessedResultRecordLevel.objects.filter(
            record_level=record_level
        ).order_by("processed_result__classification_at")
        assert list(rows.values_list("classification_outcome", flat=True)) == [
            "broken",
            "tied",
        ]
        assert set(rows.values_list("is_shared_tie", flat=True)) == {True}


@pytest.mark.django_db
def test_equal_personal_record_across_competitions_is_a_recognized_shared_tie():
    seed_baseline(people=("2020ALIC01",))
    reconcile_result_observation(
        observed(source_result="alice-one", competitor="2020ALIC01", value=900)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="alice-two",
            competition="OtherOpen2026",
            competition_name="Other Open",
            competitor="2020ALIC01",
            round_id="other-round",
            value=900,
            entered_at=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    settle()

    personal = ProcessedResultRecordLevel.objects.filter(record_level="PR").order_by(
        "processed_result__classification_at"
    )
    assert list(personal.values_list("classification_outcome", flat=True)) == [
        "broken",
        "tied",
    ]
    assert set(personal.values_list("recognition_status", flat=True)) == {"recognized"}
    assert set(personal.values_list("is_shared_tie", flat=True)) == {True}


@pytest.mark.django_db
def test_same_round_supersession_is_per_level_and_pr_remains_recognized():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    first = reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=950)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="bob",
            competitor="2020BOBB01",
            value=920,
            entered_at=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    settle()

    old = ProcessedResult.objects.get(canonical_result=first.canonical_result)
    assert level(old, "NR").recognition_status == "superseded_same_round"
    assert level(old, "NR").currently_holds is False
    assert level(old, "PR").recognition_status == "recognized"
    assert level(old, "PR").currently_holds is True


@pytest.mark.django_db
def test_same_day_supersession_crosses_competitions_and_round_precedes_day():
    seed_baseline(people=("2020ALIC01", "2020BOBB01", "2020CARA01"))
    first = reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=950)
    )
    settle()
    middle = reconcile_result_observation(
        observed(
            source_result="bob",
            competition="OtherOpen2026",
            competition_name="Other Open",
            competitor="2020BOBB01",
            round_id="other-round",
            value=930,
            entered_at=NOW + timedelta(hours=1),
            observed_at=NOW + timedelta(hours=1),
        )
    )
    settle()
    third = reconcile_result_observation(
        observed(
            source_result="cara",
            competitor="2020CARA01",
            value=920,
            entered_at=NOW + timedelta(hours=2),
            observed_at=NOW + timedelta(hours=2),
        )
    )
    settle()

    old = ProcessedResult.objects.get(canonical_result=first.canonical_result)
    same_day = ProcessedResult.objects.get(canonical_result=middle.canonical_result)
    newest = ProcessedResult.objects.get(canonical_result=third.canonical_result)
    assert level(old, "WR").recognition_status == "superseded_same_round"
    assert level(same_day, "WR").recognition_status == "superseded_same_day"
    assert level(newest, "WR").recognition_status == "recognized"


@pytest.mark.django_db
def test_same_day_supersession_applies_across_rounds_for_the_same_person():
    seed_baseline(people=("2020ALIC01",))
    first = reconcile_result_observation(
        observed(source_result="round-one", competitor="2020ALIC01", value=950)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="round-two",
            competitor="2020ALIC01",
            round_number=2,
            round_id="round-2",
            value=920,
            entered_at=NOW + timedelta(hours=1),
            observed_at=NOW + timedelta(hours=1),
        )
    )
    settle()

    old = ProcessedResult.objects.get(canonical_result=first.canonical_result)
    for record_level in ("WR", "CR", "NR", "PR"):
        assert level(old, record_level).recognition_status == "superseded_same_day"


@pytest.mark.django_db
def test_legitimate_later_record_has_ceased_holding_reason():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    first = reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=950)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="bob",
            competition="LaterOpen2026",
            competition_name="Later Open",
            competitor="2020BOBB01",
            round_id="later-round",
            value=920,
            entered_at=NOW + timedelta(days=12),
            observed_at=NOW + timedelta(days=12),
        )
    )
    settle()

    old_wr = level(ProcessedResult.objects.get(canonical_result=first.canonical_result), "WR")
    assert old_wr.recognition_status == "recognized"
    assert old_wr.currently_holds is False
    assert old_wr.ceased_holding_reason == "broken_by_other_other_competition"
    assert old_wr.ceased_holding_by is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("same_person", "same_competition", "expected_reason"),
    [
        (True, True, "broken_by_self_later_round"),
        (False, True, "broken_by_other_later_round"),
        (True, False, "broken_by_self_other_competition"),
        (False, False, "broken_by_other_other_competition"),
    ],
)
def test_legitimate_later_break_classifies_each_ceased_holding_reason(
    same_person, same_competition, expected_reason
):
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    first = reconcile_result_observation(
        observed(source_result="first", competitor="2020ALIC01", value=950)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="later",
            competition="TestOpen2026" if same_competition else "LaterOpen2026",
            competition_name="Test Open 2026" if same_competition else "Later Open",
            competitor="2020ALIC01" if same_person else "2020BOBB01",
            round_number=2,
            round_id="round-2",
            value=920,
            entered_at=NOW + timedelta(days=2),
            observed_at=NOW + timedelta(days=2),
        )
    )
    settle()

    old_wr = level(ProcessedResult.objects.get(canonical_result=first.canonical_result), "WR")
    assert old_wr.recognition_status == "recognized"
    assert old_wr.ceased_holding_reason == expected_reason


@pytest.mark.django_db
def test_slower_correction_preserves_invalid_history_and_repairs_live_state():
    seed_baseline(people=("2020TEST01",))
    data = observed(value=900)
    row = reconcile_result_observation(data)
    settle()
    reconcile_result_observation(
        replace(data, value=1100, observed_at=NOW + timedelta(minutes=1))
    )
    settle()

    rows = list(
        ProcessedResult.objects.filter(canonical_result=row.canonical_result).order_by(
            "canonical_revision"
        )
    )
    assert len(rows) == 2
    assert rows[0].is_valid_result is False
    assert rows[0].invalidity_reason == "corrected_result"
    assert level(rows[0], "WR").classification_outcome == "broken"
    assert level(rows[0], "WR").currently_holds is False
    assert rows[1].is_valid_result is True
    assert level(rows[1], "WR").classification_outcome == "none"
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 1000


@pytest.mark.django_db
def test_faster_correction_turns_a_non_record_into_a_record():
    seed_baseline(people=("2020TEST01",))
    data = observed(value=1100)
    row = reconcile_result_observation(data)
    settle()
    reconcile_result_observation(
        replace(data, value=900, observed_at=NOW + timedelta(minutes=1))
    )
    settle()

    old, new = ProcessedResult.objects.filter(
        canonical_result=row.canonical_result
    ).order_by("canonical_revision")
    assert old.is_valid_result is False
    assert level(old, "WR").classification_outcome == "none"
    assert new.is_valid_result is True
    assert level(new, "WR").classification_outcome == "broken"
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 900


@pytest.mark.django_db
def test_scope_changing_correction_repairs_old_and_new_country_continent_scopes():
    seed_two_country_baseline(people=("2020TEST01",))
    data = observed(value=900, country_code="NL")
    reconcile_result_observation(data)
    settle()
    reconcile_result_observation(
        replace(
            data,
            value=850,
            country_code="AR",
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    settle()

    assert LiveRecordsSingle.objects.get(
        record_holder="Netherlands", record_type="NR"
    ).event_333 == 1000
    assert LiveRecordsSingle.objects.get(
        record_holder="Europe", record_type="CR"
    ).event_333 == 1000
    assert LiveRecordsSingle.objects.get(
        record_holder="Argentina", record_type="NR"
    ).event_333 == 850
    assert LiveRecordsSingle.objects.get(
        record_holder="South America", record_type="CR"
    ).event_333 == 850


@pytest.mark.django_db
def test_retraction_reinstates_earlier_same_round_result():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    alice = reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=950)
    )
    settle()
    bob = reconcile_result_observation(
        observed(
            source_result="bob",
            competitor="2020BOBB01",
            value=920,
            entered_at=NOW + timedelta(minutes=1),
            observed_at=NOW + timedelta(minutes=1),
        )
    )
    settle()
    retract_result_observation(bob.observation_key, NOW + timedelta(minutes=2))
    settle()

    alice_processed = ProcessedResult.objects.get(canonical_result=alice.canonical_result)
    assert level(alice_processed, "WR").recognition_status == "recognized"
    assert level(alice_processed, "WR").currently_holds is True
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 950
    assert ProcessedResult.objects.filter(
        canonical_result=bob.canonical_result,
        invalidity_reason="retracted_result",
    ).exists()


@pytest.mark.django_db
def test_retraction_reinstates_earlier_same_day_personal_record():
    seed_baseline(people=("2020ALIC01",))
    alice = reconcile_result_observation(
        observed(source_result="alice-one", competitor="2020ALIC01", value=950)
    )
    settle()
    later = reconcile_result_observation(
        observed(
            source_result="alice-two",
            competition="OtherOpen2026",
            competition_name="Other Open",
            competitor="2020ALIC01",
            round_id="other-round",
            value=920,
            entered_at=NOW + timedelta(hours=1),
            observed_at=NOW + timedelta(hours=1),
        )
    )
    settle()
    first = ProcessedResult.objects.get(canonical_result=alice.canonical_result)
    assert level(first, "PR").recognition_status == "superseded_same_day"

    retract_result_observation(later.observation_key, NOW + timedelta(hours=2))
    settle()

    level(first, "PR").refresh_from_db()
    assert level(first, "PR").recognition_status == "recognized"
    assert level(first, "PR").currently_holds is True


@pytest.mark.django_db
def test_out_of_order_arrival_repairs_history_and_final_live_value():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    bob = reconcile_result_observation(
        observed(
            source_result="bob",
            competitor="2020BOBB01",
            value=880,
            entered_at=NOW + timedelta(hours=1),
            observed_at=NOW + timedelta(hours=1),
        )
    )
    settle()
    alice = reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=900)
    )
    settle()

    assert level(ProcessedResult.objects.get(canonical_result=alice.canonical_result), "WR").classification_outcome == "broken"
    assert level(ProcessedResult.objects.get(canonical_result=bob.canonical_result), "WR").classification_outcome == "broken"
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 880


@pytest.mark.django_db(transaction=True)
def test_revisions_are_claimed_in_order_and_stale_revision_cannot_update_live_state():
    seed_baseline(people=("2020TEST01",))
    data = observed(value=900)
    reconcile_result_observation(data)
    reconcile_result_observation(
        replace(data, value=850, observed_at=NOW + timedelta(seconds=1))
    )

    first = claim_next_work("worker")
    assert first.revision == 1
    assert process_claimed_work(first, "worker") is True
    assert ClassificationWork.objects.get(pk=first.work_id).status == "stale"
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 1000
    assert NotificationEvent.objects.count() == 0

    second = claim_next_work("worker")
    assert second.revision == 2
    process_claimed_work(second, "worker")
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 850


@pytest.mark.django_db(transaction=True)
def test_failed_work_retries_idempotently(monkeypatch):
    seed_baseline(people=("2020TEST01",))
    reconcile_result_observation(observed(value=900))
    original = classification_work.classify_revision
    calls = 0

    def classify_then_fail_once(revision):
        nonlocal calls
        calls += 1
        current = original(revision, publish_notifications=False)
        if calls == 1:
            raise RuntimeError("worker interrupted after classification")
        return current

    monkeypatch.setattr(classification_work, "classify_revision", classify_then_fail_once)
    first = claim_next_work("worker")
    assert process_claimed_work(first, "worker") is False
    assert ClassificationWork.objects.get(pk=first.work_id).status == "failed"
    assert ProcessedResult.objects.count() == 1
    assert ProcessedResultRecordLevel.objects.count() == 4

    retry = claim_next_work("worker")
    assert retry.work_id == first.work_id
    assert process_claimed_work(retry, "worker") is True
    work = ClassificationWork.objects.get(pk=retry.work_id)
    assert work.status == "completed"
    assert work.attempts == 2
    assert ProcessedResult.objects.count() == 1
    assert ProcessedResultRecordLevel.objects.count() == 4
    assert LiveRecordsSingle.objects.get(record_holder="World").event_333 == 900


@pytest.mark.django_db
def test_incremental_state_matches_clean_rebuild():
    seed_baseline(people=("2020ALIC01", "2020BOBB01"))
    reconcile_result_observation(
        observed(source_result="alice", competitor="2020ALIC01", value=930)
    )
    settle()
    reconcile_result_observation(
        observed(
            source_result="bob",
            competitor="2020BOBB01",
            value=900,
            entered_at=NOW + timedelta(days=1),
            observed_at=NOW + timedelta(days=1),
        )
    )
    settle()
    before_live = list(
        LiveRecordsSingle.objects.order_by("record_holder").values(
            "record_holder", "record_type", "event_333"
        )
    )
    before_levels = list(
        ProcessedResultRecordLevel.objects.filter(
            processed_result__is_valid_result=True
        )
        .order_by(
            "processed_result__canonical_result_id",
            "processed_result__canonical_revision",
            "record_level",
        )
        .values(
            "record_level",
            "classification_outcome",
            "recognition_status",
            "currently_holds",
        )
    )

    with transaction.atomic():
        rebuild_classification_from_scratch()

    assert list(
        LiveRecordsSingle.objects.order_by("record_holder").values(
            "record_holder", "record_type", "event_333"
        )
    ) == before_live
    assert list(
        ProcessedResultRecordLevel.objects.filter(
            processed_result__is_valid_result=True
        )
        .order_by(
            "processed_result__canonical_result_id",
            "processed_result__canonical_revision",
            "record_level",
        )
        .values(
            "record_level",
            "classification_outcome",
            "recognition_status",
            "currently_holds",
        )
    ) == before_levels
