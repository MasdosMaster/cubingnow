from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from apps.notifications.models import NotificationEvent
from apps.records.classification import reclassify_scope
from apps.records.classification_work import (
    mark_classification_scopes_dirty,
    process_ready_scopes,
    worker_identity,
)
from apps.records.domain import NormalizedResultObservation
from apps.records.models import (
    Achievement,
    CanonicalResult,
    ClassificationScopeWork,
    PersonalBestBaseline,
    RecordBenchmark,
    RecordValidation,
    ResultObservation,
    WCARecordSnapshot,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)
from apps.records.snapshot_differ import diff_result_values
from integrations.wca.record_validation import (
    parse_wca_records,
    refresh_wca_record_validations,
)


def observed(
    *,
    method="cubingchina_websocket",
    source="cubingchina",
    source_result="source-result-1",
    competitor="2020TEST01",
    competitor_name="Test Cuber",
    round_number=1,
    round_id="provider-round-1",
    value=390,
    attempt_number=1,
    country_code="NL",
    kind="single",
    claim="",
    entered_at=None,
    observed_at=None,
):
    observed_at = observed_at or datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    return NormalizedResultObservation(
        source=source,
        ingestion_method=method,
        source_result_identity=source_result,
        source_competition_id="provider-competition-1",
        source_competitor_id=competitor,
        wca_competition_id="TestOpen2026",
        competition_name="Test Open 2026",
        competition_country_code="ES",
        competition_start_date=date(2026, 8, 8),
        competition_end_date=date(2026, 8, 9),
        round_id=round_id,
        round_number=round_number,
        round_name=f"Round {round_number}",
        event_id="333",
        event_name="3x3x3 Cube",
        competitor_name=competitor_name,
        competitor_wca_id=competitor,
        country_code=country_code,
        kind=kind,
        value=value,
        attempt_number=attempt_number,
        source_record_tag=claim,
        entered_at=entered_at,
        observed_at=observed_at,
        source_url="https://example.test/live",
        normalized_payload={"value": value, "claim": claim},
    )


def test_full_round_attempt_diff_emits_only_the_new_attempt():
    changes = diff_result_values([590, 580], [590, 580, 570])
    assert [(row.change_type, row.attempt_number, row.value) for row in changes] == [
        ("added", 3, 570)
    ]
    assert diff_result_values([590, 580], [590, 580]) == ()


def test_full_round_attempt_diff_handles_corrections_retractions_and_average():
    changes = diff_result_values(
        [590, 580, 570],
        [590, 575],
        previous_average=580,
        current_average=None,
    )
    assert [row.change_type for row in changes] == [
        "corrected",
        "retracted",
        "retracted",
    ]
    assert changes[0].previous_value == 580 and changes[0].value == 575


@pytest.mark.django_db
def test_ws_and_api_reconcile_to_one_result_with_both_provenances():
    entered_at = datetime(2026, 8, 8, 9, 59, tzinfo=UTC)
    api = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        attempt_number=None,
        value=390,
        claim="WR",
        entered_at=entered_at,
    )
    ws = replace(
        api,
        ingestion_method="graphql_subscription",
        attempt_number=1,
        observed_at=api.observed_at + timedelta(seconds=8),
    )

    first = reconcile_result_observation(api)
    second = reconcile_result_observation(ws)

    assert first.canonical_result_id == second.canonical_result_id
    result = CanonicalResult.objects.get()
    assert result.observations.count() == 2
    assert result.entered_at == entered_at
    assert set(result.observations.values_list("ingestion_method", flat=True)) == {
        "api_polling",
        "graphql_subscription",
    }


@pytest.mark.django_db
def test_provider_neutral_natural_identity_reconciles_cubingchina_and_wca_live():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    cubingchina = observed(value=390, source_result="cubingchina-result-9")
    wca_live = replace(
        cubingchina,
        source="wca_live",
        ingestion_method="graphql_subscription",
        source_result_identity="wca-live-result-7",
        source_competition_id="wca-live-competition-1",
        source_competitor_id="wca-live-person-1",
        round_id="wca-live-round-1",
        entered_at=datetime(2026, 8, 8, 9, 59, tzinfo=UTC),
        observed_at=cubingchina.observed_at + timedelta(seconds=5),
    )

    first = reconcile_result_observation(cubingchina)
    second = reconcile_result_observation(wca_live)

    assert first.canonical_result_id == second.canonical_result_id
    result = CanonicalResult.objects.get()
    result.refresh_from_db()
    assert result.validation_status == "verified"
    assert set(result.observations.values_list("source", flat=True)) == {
        "cubingchina",
        "wca_live",
    }
    assert Achievement.objects.get(result=result, type="WR").qualification.show_on_homepage


@pytest.mark.django_db
def test_cubingchina_bogus_wr_claim_is_evidence_not_classification():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    row = reconcile_result_observation(observed(value=412, claim="WR"))

    row.refresh_from_db()
    assert row.source_record_tag == "WR"
    assert row.source_claim_trusted is False
    assert row.result_evidence_trusted is False
    assert not Achievement.objects.filter(result=row.canonical_result, type="WR").exists()
    assert NotificationEvent.objects.count() == 0


@pytest.mark.django_db
def test_cubingchina_record_is_classified_without_a_source_label_but_not_published():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    row = reconcile_result_observation(observed(value=390, claim=""))

    achievement = Achievement.objects.get(result=row.canonical_result, type="WR")
    assert achievement.classification_reason == "effective_live_benchmark"
    assert achievement.source_claim_supported is False
    assert row.canonical_result.validation_status == "pending"
    assert achievement.qualification.show_on_homepage is False
    assert achievement.qualification.notification_eligible is False


@pytest.mark.django_db
def test_effective_live_record_replay_handles_later_slower_result():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    alice_observation = observed(
        source_result="alice-1",
        competitor="2020ALICE1",
        competitor_name="Alice",
        value=390,
    )
    alice = reconcile_result_observation(alice_observation).canonical_result
    bob = reconcile_result_observation(
        observed(
            source_result="bob-1",
            competitor="2020BOB001",
            competitor_name="Bob",
            value=395,
            observed_at=datetime(2026, 8, 8, 10, 1, tzinfo=UTC),
        )
    ).canonical_result

    assert Achievement.objects.filter(result=alice, type="WR", status="active").exists()
    assert not Achievement.objects.filter(result=bob, type="WR", status="active").exists()

    reconcile_result_observation(replace(alice_observation, value=490))
    assert not Achievement.objects.filter(result=alice, type="WR", status="active").exists()
    assert Achievement.objects.filter(result=bob, type="WR", status="active").exists()


@pytest.mark.django_db
def test_pr_progression_uses_effective_personal_best():
    PersonalBestBaseline.objects.create(
        competitor_wca_id="2020TEST01", event_id="333", kind="single", value=620
    )
    start = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    results = []
    for index, value in enumerate((610, 615, 602), start=1):
        results.append(
            reconcile_result_observation(
                observed(
                    method="graphql_subscription",
                    source="wca_live",
                    source_result=f"round-result-{index}",
                    round_number=index,
                    round_id=f"round-{index}",
                    value=value,
                    entered_at=start + timedelta(minutes=index),
                    observed_at=start + timedelta(minutes=index),
                )
            ).canonical_result
        )

    assert [
        Achievement.objects.filter(result=result, type="PR", status="active").exists()
        for result in results
    ] == [True, False, True]


@pytest.mark.django_db
def test_one_result_retains_all_achievements_with_homepage_precedence(
    django_capture_on_commit_callbacks,
):
    for level, region in (("WR", ""), ("CR", "Europe"), ("NR", "NL")):
        RecordBenchmark.objects.create(
            level=level,
            event_id="333",
            kind="single",
            region_code=region,
            value=400,
        )
    PersonalBestBaseline.objects.create(
        competitor_wca_id="2020TEST01", event_id="333", kind="single", value=410
    )
    with django_capture_on_commit_callbacks(execute=True):
        result = reconcile_result_observation(
            observed(
                method="graphql_subscription",
                source="wca_live",
                value=390,
                claim="WR",
            )
        ).canonical_result

    achievements = Achievement.objects.filter(result=result, status="active")
    assert set(achievements.values_list("type", flat=True)) == {"WR", "CR", "NR", "PR"}
    assert set(
        achievements.filter(qualification__show_on_homepage=True).values_list("type", flat=True)
    ) == {"WR"}
    assert set(
        achievements.filter(qualification__notification_eligible=True).values_list(
            "type", flat=True
        )
    ) == {"WR"}
    assert NotificationEvent.objects.count() == 1


@pytest.mark.django_db
def test_correction_updates_same_identity_and_replays_record_state():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    original_observation = observed(value=390)
    first = reconcile_result_observation(original_observation)
    result_id = first.canonical_result_id
    assert Achievement.objects.filter(result_id=result_id, type="WR", status="active").exists()

    corrected = reconcile_result_observation(replace(original_observation, value=490))
    corrected.canonical_result.refresh_from_db()
    assert corrected.canonical_result_id == result_id
    assert corrected.canonical_result.revision == 2
    assert corrected.canonical_result.status == "corrected"
    assert not Achievement.objects.filter(result_id=result_id, type="WR", status="active").exists()


@pytest.mark.django_db
def test_trusted_source_disagreement_blocks_qualification():
    api = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        attempt_number=None,
        value=390,
        claim="WR",
    )
    ws = replace(
        api,
        ingestion_method="graphql_subscription",
        attempt_number=1,
        value=395,
        observed_at=api.observed_at + timedelta(seconds=3),
    )
    reconcile_result_observation(api)
    row = reconcile_result_observation(ws)
    row.canonical_result.refresh_from_db()

    assert CanonicalResult.objects.count() == 1
    assert row.canonical_result.validation_status == "rejected"
    assert row.canonical_result.validation_reason == "trusted_source_disagreement"
    assert not Achievement.objects.filter(status="active").exists()


@pytest.mark.django_db
def test_reconciliation_is_database_idempotent():
    item = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        attempt_number=None,
        value=390,
        claim="WR",
    )
    first = reconcile_result_observation(item)
    second = reconcile_result_observation(item)
    assert first.pk == second.pk
    assert CanonicalResult.objects.count() == 1
    assert ResultObservation.objects.count() == 1
    assert Achievement.objects.filter(type="WR", status="active").count() == 1


@pytest.mark.django_db
def test_identical_attempt_values_keep_distinct_attempt_identities():
    api = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        attempt_number=None,
        value=390,
        claim="WR",
    )
    first_attempt = replace(
        api,
        ingestion_method="graphql_subscription",
        attempt_number=1,
        observed_at=api.observed_at + timedelta(seconds=1),
    )
    second_attempt = replace(
        first_attempt,
        attempt_number=2,
        source_record_tag="",
        observed_at=api.observed_at + timedelta(seconds=2),
    )

    api_row = reconcile_result_observation(api)
    first_row = reconcile_result_observation(first_attempt)
    second_row = reconcile_result_observation(second_attempt)

    assert api_row.canonical_result_id == first_row.canonical_result_id
    assert second_row.canonical_result_id != first_row.canonical_result_id
    assert CanonicalResult.objects.count() == 2


def wca_records_payload(*, world=400, europe=410, netherlands=420):
    return {
        "world_records": {"333": {"single": world}},
        "continental_records": {"_Europe": {"333": {"single": europe}}},
        "national_records": {"Netherlands": {"333": {"single": netherlands}}},
    }


def test_wca_records_parser_normalizes_all_three_official_scopes():
    parsed = parse_wca_records(wca_records_payload())
    assert parsed.records == {
        "WR|333|single|": 400,
        "CR|333|single|_Europe": 410,
        "NR|333|single|Netherlands": 420,
    }


@pytest.mark.django_db
def test_cubingchina_result_is_independently_validated_and_published(
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        refresh_wca_record_validations(
            wca_records_payload(),
            source_url="https://www.worldcubeassociation.org/api/v0/records",
        )
        row = reconcile_result_observation(observed(value=390, claim=""))

    result = row.canonical_result
    result.refresh_from_db()
    assert result.validation_status == "verified"
    assert result.validation_reason == "wca_records_api_record_match"
    assert set(
        result.record_validations.filter(status="verified").values_list("level", flat=True)
    ) == {"WR", "CR", "NR"}
    achievement = Achievement.objects.get(result=result, type="WR")
    assert achievement.classification_reason == "wca_records_api_validation"
    assert achievement.qualification.show_on_homepage is True
    assert achievement.qualification.notification_eligible is True


@pytest.mark.django_db
def test_wca_snapshot_equality_still_validates_after_official_record_updates():
    refresh_wca_record_validations(
        wca_records_payload(world=390),
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    result = reconcile_result_observation(observed(value=390)).canonical_result

    validation = RecordValidation.objects.get(result=result, level="WR")
    achievement = Achievement.objects.get(result=result, type="WR")
    assert validation.status == "verified"
    assert validation.result_value == validation.benchmark_value == 390
    assert achievement.qualification.show_on_homepage is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("country_code", "records_region", "average"),
    [("HK", "Hong Kong", 613), ("MO", "Macau", 778)],
)
def test_records_api_display_name_validates_equal_national_record(
    country_code, records_region, average
):
    refresh_wca_record_validations(
        {
            "world_records": {},
            "continental_records": {},
            "national_records": {
                records_region: {"333": {"average": average}}
            },
        },
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    result = reconcile_result_observation(
        observed(
            value=average,
            attempt_number=None,
            country_code=country_code,
            kind="average",
            claim="NR",
        )
    ).canonical_result

    validation = RecordValidation.objects.get(result=result, level="NR")
    achievement = Achievement.objects.get(result=result, type="NR")
    assert validation.region_code == records_region
    assert validation.status == "verified"
    assert validation.result_value == validation.benchmark_value == average
    assert achievement.qualification.show_on_homepage is True


@pytest.mark.django_db
def test_cubingchina_bogus_claim_is_rejected_by_official_record_snapshot():
    refresh_wca_record_validations(
        {
            "world_records": {"333": {"single": 400}},
            "continental_records": {},
            "national_records": {},
        },
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    result = reconcile_result_observation(observed(value=412, claim="WR")).canonical_result
    result.refresh_from_db()

    assert result.validation_status == "rejected"
    assert result.validation_reason == "wca_records_api_record_mismatch"
    assert RecordValidation.objects.get(result=result, level="WR").status == "rejected"
    assert not Achievement.objects.filter(result=result, type="WR", status="active").exists()
    assert NotificationEvent.objects.count() == 0


@pytest.mark.django_db
def test_official_level_rejection_overrides_stale_higher_live_baselines():
    for level, region in (("WR", ""), ("CR", "Europe"), ("NR", "NL")):
        RecordBenchmark.objects.create(
            level=level,
            event_id="333",
            kind="single",
            region_code=region,
            value=500,
        )
    refresh_wca_record_validations(
        wca_records_payload(world=400, europe=410, netherlands=420),
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    result = reconcile_result_observation(observed(value=412)).canonical_result

    assert set(
        Achievement.objects.filter(result=result, status="active").values_list("type", flat=True)
    ) == {"NR"}
    assert Achievement.objects.get(result=result, type="NR").qualification.show_on_homepage


@pytest.mark.django_db
def test_wca_snapshots_are_content_deduplicated_but_rechecked():
    payload = wca_records_payload()
    refresh_wca_record_validations(
        payload, source_url="https://www.worldcubeassociation.org/api/v0/records"
    )
    refresh_wca_record_validations(
        payload, source_url="https://www.worldcubeassociation.org/api/v0/records"
    )
    assert WCARecordSnapshot.objects.count() == 1


@pytest.mark.django_db
def test_deferred_reconciliation_is_classified_once_by_durable_scope_worker():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    item = observed(
        method="graphql_subscription",
        source="wca_live",
        value=390,
        claim="WR",
    )
    row = reconcile_result_observation(item, defer_classification=True)

    assert not Achievement.objects.filter(result=row.canonical_result).exists()
    assert (
        mark_classification_scopes_dirty(
            {("333", "single")},
            observed_at=item.observed_at,
            debounce_seconds=0,
        )
        == 1
    )
    work = ClassificationScopeWork.objects.get(event_id="333", kind="single")
    assert work.requested_version == 1
    assert work.processed_version == 0

    assert process_ready_scopes(worker_identity(), limit=1) == 1

    work.refresh_from_db()
    assert work.requested_version == work.processed_version == 1
    assert work.dirty_since is None
    assert work.last_result_count == 1
    achievement = Achievement.objects.get(result=row.canonical_result, type="WR")
    assert achievement.qualification.notification_eligible is True


@pytest.mark.django_db
def test_scope_classification_query_count_is_bounded_across_a_large_burst(
    django_assert_max_num_queries,
):
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=1000
    )
    for index in range(50):
        reconcile_result_observation(
            observed(
                method="graphql_subscription",
                source="wca_live",
                source_result=f"burst-result-{index}",
                competitor=f"2026TEST{index:02d}",
                value=900 - index,
                claim="WR",
            ),
            defer_classification=True,
        )

    with django_assert_max_num_queries(20):
        reclassify_scope("333", "single")

    assert Achievement.objects.filter(type="WR", status="active").count() == 50


@pytest.mark.django_db
def test_scope_worker_removes_stale_validation_after_source_retraction():
    refresh_wca_record_validations(
        wca_records_payload(),
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    item = observed(value=390)
    row = reconcile_result_observation(item, defer_classification=True)
    mark_classification_scopes_dirty(
        {("333", "single")},
        observed_at=item.observed_at,
        debounce_seconds=0,
    )
    worker_id = worker_identity()
    assert process_ready_scopes(worker_id, limit=1) == 1
    assert RecordValidation.objects.filter(result=row.canonical_result).exists()

    retracted_at = item.observed_at + timedelta(seconds=1)
    assert retract_result_observation(
        item.observation_key,
        retracted_at,
        defer_classification=True,
    )
    mark_classification_scopes_dirty(
        {("333", "single")},
        observed_at=retracted_at,
        debounce_seconds=0,
    )
    assert process_ready_scopes(worker_id, limit=1) == 1

    assert not RecordValidation.objects.filter(result=row.canonical_result).exists()
    assert not Achievement.objects.filter(result=row.canonical_result, status="active").exists()
