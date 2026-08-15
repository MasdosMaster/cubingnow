from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.notifications.models import (
    NotificationDelivery,
    NotificationEndpoint,
    NotificationEvent,
    NotificationProvider,
    NotificationType,
)
from apps.notifications.services import publish_achievement_notification
from apps.records.classification import reclassify_scope
from apps.records.classification_work import (
    mark_classification_scopes_dirty,
    process_ready_scopes,
    worker_identity,
)
from apps.records.domain import NormalizedResultObservation
from apps.records.finalization import RoundFinalizationRule, round_result_is_finalized
from apps.records.models import (
    Achievement,
    CanonicalResult,
    ClassificationScopeWork,
    PersonalBestBaseline,
    RecordBenchmark,
    RecordValidation,
    ResultObservation,
    SubscriptionResultState,
    SubscriptionRound,
    WCARecordSnapshot,
)
from apps.records.reconciliation import (
    reconcile_result_observation,
    retract_result_observation,
)
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
        source_record_tag=claim,
        entered_at=entered_at,
        observed_at=observed_at,
        source_url="https://example.test/live",
        normalized_payload={"value": value, "claim": claim},
    )


def test_ao5_is_final_only_after_all_five_attempt_positions_are_entered():
    rule = RoundFinalizationRule(expected_attempts=5)
    assert not round_result_is_finalized((495, 510, 488, 470), rule, event_id="333")
    assert round_result_is_finalized((495, 510, 488, 470, -1), rule, event_id="333")


def test_failed_cutoff_is_final_without_filling_the_remaining_positions():
    rule = RoundFinalizationRule(expected_attempts=5, cutoff_attempts=2, cutoff_value=1000)
    assert round_result_is_finalized((1200, -1, 0, 0, 0), rule, event_id="333")
    assert not round_result_is_finalized((900, -1, 0, 0, 0), rule, event_id="333")


@pytest.mark.django_db
def test_ws_and_api_reconcile_to_one_result_with_both_provenances():
    entered_at = datetime(2026, 8, 8, 9, 59, tzinfo=UTC)
    api = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        value=390,
        claim="WR",
        entered_at=entered_at,
    )
    ws = replace(
        api,
        ingestion_method="graphql_subscription",
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
def test_finalized_result_retires_legacy_projection_and_reuses_its_notification():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    old_observation = reconcile_result_observation(
        observed(
            method="api_polling",
            source="wca_live",
            source_result="legacy-result",
            value=390,
            claim="WR",
        )
    )
    old_result = old_observation.canonical_result
    old_result.identity_key += "|attempt:3"
    old_result.attempt_number = 3
    old_result.save(update_fields=["identity_key", "attempt_number", "updated_at"])
    old_observation.attempt_number = 3
    old_observation.save(update_fields=["attempt_number", "updated_at"])
    old_achievement = Achievement.objects.get(result=old_result, type="WR")

    endpoint = NotificationEndpoint()
    endpoint.issue_management_token()
    endpoint.save()
    old_event = NotificationEvent.objects.create(
        notification_type=NotificationType.RECORD_WR,
        deduplication_key=f"record:achievement:{old_result.identity_key}|WR",
        payload={
            "record_level": "WR",
            "event_id": "333",
            "kind": "single",
            "formatted_result": "3.90",
            "competitor_name": "Test Cuber",
            "competition_name": "Test Open 2026",
        },
        target_url="/",
        occurred_at=old_result.first_observed_at,
        achievement=old_achievement,
    )
    delivery = NotificationDelivery.objects.create(
        event=old_event,
        endpoint=endpoint,
        provider=NotificationProvider.WEBPUSH,
        status=NotificationDelivery.Status.RETRY,
    )

    final_observation = reconcile_result_observation(
        observed(
            method="api_polling",
            source="wca_live",
            source_result="final-result",
            value=390,
            claim="WR",
            observed_at=old_result.first_observed_at + timedelta(minutes=1),
        )
    )
    final_result = final_observation.canonical_result
    final_achievement = Achievement.objects.get(result=final_result, type="WR")

    old_achievement.refresh_from_db()
    old_achievement.qualification.refresh_from_db()
    assert old_achievement.status == Achievement.Status.WITHDRAWN
    assert old_achievement.qualification.show_on_homepage is False
    assert old_achievement.qualification.notification_eligible is False

    # Even if stale projection flags survive a partial rollout, the public API
    # still prefers the finalized fact and cannot show the duplicate.
    old_achievement.status = Achievement.Status.ACTIVE
    old_achievement.save(update_fields=["status", "updated_at"])
    old_achievement.qualification.show_on_homepage = True
    old_achievement.qualification.save(
        update_fields=["show_on_homepage", "updated_at"]
    )
    response = APIClient().get("/api/records/?level=WR")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["canonical_result_id"] == final_result.pk

    event, created = publish_achievement_notification(final_achievement)

    assert event == old_event
    assert created is False
    assert NotificationEvent.objects.count() == 1
    old_event.refresh_from_db()
    delivery.refresh_from_db()
    assert old_event.achievement_id == final_achievement.pk
    assert delivery.status == NotificationDelivery.Status.CANCELLED
    assert delivery.last_error_code == "finalized_identity_cutover"


@pytest.mark.django_db
def test_recovery_migration_retires_duplicates_and_cancels_the_existing_queue():
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    legacy_observation = reconcile_result_observation(
        observed(
            method="api_polling",
            source="wca_live",
            source_result="migration-legacy-result",
            value=390,
            claim="WR",
        )
    )
    legacy_result = legacy_observation.canonical_result
    legacy_result.identity_key += "|attempt:3"
    legacy_result.attempt_number = 3
    legacy_result.save(update_fields=["identity_key", "attempt_number", "updated_at"])
    legacy_observation.attempt_number = 3
    legacy_observation.save(update_fields=["attempt_number", "updated_at"])
    legacy_achievement = Achievement.objects.get(result=legacy_result, type="WR")

    final_observation = reconcile_result_observation(
        observed(
            method="api_polling",
            source="wca_live",
            source_result="migration-final-result",
            value=390,
            claim="WR",
            observed_at=legacy_result.first_observed_at + timedelta(minutes=1),
        )
    )
    final_achievement = Achievement.objects.get(
        result=final_observation.canonical_result,
        type="WR",
    )

    legacy_achievement.status = Achievement.Status.ACTIVE
    legacy_achievement.save(update_fields=["status", "updated_at"])
    legacy_achievement.qualification.show_on_homepage = True
    legacy_achievement.qualification.notification_eligible = True
    legacy_achievement.qualification.save(
        update_fields=["show_on_homepage", "notification_eligible", "updated_at"]
    )

    old_event = NotificationEvent.objects.create(
        notification_type=NotificationType.RECORD_WR,
        deduplication_key=f"record:achievement:{legacy_result.identity_key}|WR",
        payload={},
        target_url="/",
        occurred_at=legacy_result.first_observed_at,
        achievement=legacy_achievement,
    )
    current_event = NotificationEvent.objects.create(
        notification_type=NotificationType.RECORD_WR,
        deduplication_key=(
            f"record:achievement:{final_observation.canonical_result.identity_key}|WR"
        ),
        payload={},
        target_url="/",
        occurred_at=final_observation.canonical_result.first_observed_at,
        achievement=final_achievement,
    )

    def queued_delivery(event, status):
        item = NotificationEndpoint()
        item.issue_management_token()
        item.save()
        return NotificationDelivery.objects.create(
            event=event,
            endpoint=item,
            provider=NotificationProvider.WEBPUSH,
            status=status,
        )

    pending = queued_delivery(old_event, NotificationDelivery.Status.PENDING)
    retry = queued_delivery(current_event, NotificationDelivery.Status.RETRY)
    sent = queued_delivery(old_event, NotificationDelivery.Status.SENT)

    migration = import_module(
        "apps.records.migrations.0012_retire_superseded_attempt_results"
    )
    migration.retire_superseded_attempt_results(django_apps, None)

    legacy_result.refresh_from_db()
    legacy_observation.refresh_from_db()
    legacy_achievement.refresh_from_db()
    legacy_achievement.qualification.refresh_from_db()
    pending.refresh_from_db()
    retry.refresh_from_db()
    sent.refresh_from_db()
    assert legacy_result.status == CanonicalResult.Status.RETRACTED
    assert legacy_observation.status == ResultObservation.Status.RETRACTED
    assert legacy_achievement.status == Achievement.Status.WITHDRAWN
    assert legacy_achievement.qualification.show_on_homepage is False
    assert legacy_achievement.qualification.notification_eligible is False
    assert pending.status == NotificationDelivery.Status.CANCELLED
    assert retry.status == NotificationDelivery.Status.CANCELLED
    assert sent.status == NotificationDelivery.Status.SENT


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
        value=390,
        claim="WR",
    )
    ws = replace(
        api,
        ingestion_method="graphql_subscription",
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
def test_same_round_level_claim_from_two_methods_has_one_identity():
    api = observed(
        method="api_polling",
        source="wca_live",
        source_result="wca-result-1",
        value=390,
        claim="WR",
    )
    subscription = replace(
        api,
        ingestion_method="graphql_subscription",
        observed_at=api.observed_at + timedelta(seconds=1),
    )

    api_row = reconcile_result_observation(api)
    subscription_row = reconcile_result_observation(subscription)

    assert api_row.canonical_result_id == subscription_row.canonical_result_id
    assert CanonicalResult.objects.count() == 1


@pytest.mark.django_db
def test_fallback_identity_is_promoted_when_the_wca_id_arrives_later():
    anonymous = observed(competitor="", source_result="provider-result-1")
    first = reconcile_result_observation(anonymous)
    original_id = first.canonical_result_id
    assert first.canonical_result.identity_key.startswith("source|")

    identified = replace(
        anonymous,
        competitor_wca_id="2020TEST01",
        observed_at=anonymous.observed_at + timedelta(seconds=1),
    )
    second = reconcile_result_observation(identified)
    second.canonical_result.refresh_from_db()

    assert second.canonical_result_id == original_id
    assert second.canonical_result.identity_key == ("wca|TESTOPEN2026|2020TEST01|333|1|single")
    assert CanonicalResult.objects.count() == 1


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
    [
        ("HK", "Hong Kong", 613),
        ("MO", "Macau", 778),
        ("TW", "Taiwan", 701),
        ("CI", "Cote d_Ivoire", 702),
        ("KR", "Korea", 703),
        ("US", "USA", 704),
    ],
)
def test_records_api_display_name_validates_equal_national_record(
    country_code, records_region, average
):
    refresh_wca_record_validations(
        {
            "world_records": {},
            "continental_records": {},
            "national_records": {records_region: {"333": {"average": average}}},
        },
        source_url="https://www.worldcubeassociation.org/api/v0/records",
    )
    result = reconcile_result_observation(
        observed(
            value=average,
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


@pytest.mark.django_db
def test_finalized_backfill_rebuilds_two_facts_without_publishing_notifications():
    round_target = SubscriptionRound.objects.create(
        round_id="backfill-round-1",
        wca_live_competition_id="live-competition-1",
        wca_competition_id="TestOpen2026",
        competition_name="Test Open 2026",
        competition_country_code="ES",
        competition_start_date=date(2026, 8, 8),
        competition_end_date=date(2026, 8, 9),
        event_id="333",
        event_name="3x3x3 Cube",
        round_number=1,
        round_name="Final",
        format_id="a",
        expected_attempts=5,
    )
    observed_at = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    SubscriptionResultState.objects.create(
        round=round_target,
        result_id="backfill-result-1",
        stable_result_identity="backfill-result-1",
        competitor_wca_live_id="person-1",
        competitor_wca_id="2020TEST01",
        competitor_name="Test Cuber",
        country_code="NL",
        attempts=[390, 410, 420, 400, -1],
        best=390,
        average=410,
        single_record_tag="WR",
        average_record_tag="",
        meaningful_hash="backfill-hash",
        normalized_payload={"attempts": [390, 410, 420, 400, -1]},
        first_observed_at=observed_at,
        last_observed_at=observed_at,
        processed_at=observed_at,
    )
    RecordBenchmark.objects.create(
        level="WR", event_id="333", kind="single", region_code="", value=400
    )
    old_observation = reconcile_result_observation(
        observed(
            method="graphql_subscription",
            source="wca_live",
            source_result="old-attempt-result",
            value=395,
            claim="WR",
        )
    )
    old_achievement = Achievement.objects.get(
        result=old_observation.canonical_result,
        type="WR",
    )
    endpoint = NotificationEndpoint()
    endpoint.issue_management_token()
    endpoint.save()
    old_event = NotificationEvent.objects.create(
        notification_type=NotificationType.RECORD_WR,
        deduplication_key="old-attempt-achievement",
        payload={},
        target_url="/",
        occurred_at=observed_at,
        achievement=old_achievement,
    )
    old_delivery = NotificationDelivery.objects.create(
        event=old_event,
        endpoint=endpoint,
        provider=NotificationProvider.WEBPUSH,
        status=NotificationDelivery.Status.RETRY,
    )

    call_command("backfill_finalized_results")
    assert CanonicalResult.objects.filter(pk=old_observation.canonical_result_id).exists()

    call_command("backfill_finalized_results", "--apply")

    assert set(CanonicalResult.objects.values_list("kind", "value")) == {
        ("single", 390),
        ("average", 410),
    }
    assert ResultObservation.objects.count() == 2
    assert not CanonicalResult.objects.exclude(attempt_number=None).exists()
    assert not ResultObservation.objects.exclude(attempt_number=None).exists()
    assert Achievement.objects.filter(type="WR", status="active").exists()
    assert NotificationEvent.objects.count() == 1
    old_event.refresh_from_db()
    old_delivery.refresh_from_db()
    assert old_event.achievement_id is None
    assert old_delivery.status == NotificationDelivery.Status.CANCELLED
