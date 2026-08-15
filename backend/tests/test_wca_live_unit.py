import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.records.models import (
    CanonicalResult,
    ClassificationScopeWork,
    RecentRecordObservation,
    ResultObservation,
    SourceObservation,
    SubscriptionResultState,
    SubscriptionRound,
)
from integrations.wca_live import subscription_ingestion
from integrations.wca_live.discovery import (
    competition_lookback,
    competition_overlaps,
    filter_overlapping_competitions,
    flatten_competition_rounds,
)
from integrations.wca_live.ingestion import persist_record_candidate
from integrations.wca_live.result_values import (
    comparison_key,
    decode_multi_blind,
    format_result,
    is_better,
)
from integrations.wca_live.schemas import RecordCandidate
from integrations.wca_live.snapshots import diff_snapshots, normalize_round_snapshot
from integrations.wca_live.subscription_ingestion import process_round_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "round_snapshot_initial.json"


def snapshot():
    return json.loads(FIXTURE.read_text())


def new_result(result_id, *, best=700, average=710, single_tag=None, average_tag=None):
    return {
        "id": result_id,
        "ranking": 2,
        "attempts": [{"result": best}, {"result": average}, {"result": average + 10}],
        "best": best,
        "average": average,
        "singleRecordTag": single_tag,
        "averageRecordTag": average_tag,
        "enteredAt": "2026-08-05T11:55:00Z",
        "person": {
            "id": f"person-{result_id}",
            "wcaId": "2021TEST02",
            "name": "New Cuber",
            "country": {"iso2": "US"},
        },
    }


def create_round():
    return SubscriptionRound.objects.create(
        round_id="round-1",
        wca_live_competition_id="competition-1",
        wca_competition_id="WeekendOpen2026",
        competition_name="Weekend Open 2026",
        competition_start_date=date(2026, 8, 5),
        competition_end_date=date(2026, 8, 7),
        event_id="333",
        event_name="3x3x3 Cube",
        round_number=1,
        round_name="First Round",
        format_id="m",
        format_sort_by="average",
        expected_attempts=3,
    )


def candidate(method_time=None):
    observed = method_time or timezone.now()
    return RecordCandidate(
        stable_result_identity="shared-result",
        wca_live_record_id="recent-record-1",
        wca_live_result_id="shared-result",
        wca_live_competition_id="competition-1",
        wca_competition_id="WeekendOpen2026",
        competition_name="Weekend Open 2026",
        competition_start_date=date(2026, 8, 5),
        competition_end_date=date(2026, 8, 7),
        round_id="round-1",
        round_name="Final",
        event_id="333",
        event_name="3x3x3 Cube",
        competitor_name="Test Cuber",
        competitor_wca_id="2020TEST01",
        competitor_wca_live_id="person-1",
        country_code="NL",
        kind="single",
        raw_result=326,
        record_level="WR",
        source_url="https://live.worldcubeassociation.org/competitions/competition-1/rounds/round-1",
        source_update_timestamp=observed,
        observed_at=observed,
        attempts=(326, 410, 430),
        final_best=326,
        final_average=410,
        expected_attempts=3,
    )


def test_competition_weekend_overlap_is_inclusive():
    start, end = date(2026, 8, 6), date(2026, 8, 10)
    assert competition_overlaps({"startDate": "2026-08-01", "endDate": "2026-08-06"}, start, end)
    assert competition_overlaps({"startDate": "2026-08-10", "endDate": "2026-08-12"}, start, end)
    assert not competition_overlaps(
        {"startDate": "2026-08-01", "endDate": "2026-08-05"}, start, end
    )


def test_lookback_includes_competitions_starting_before_weekend():
    assert competition_lookback(date(2026, 8, 6), 7) == date(2026, 7, 30)
    competitions = [
        {"id": "before", "startDate": "2026-08-05", "endDate": "2026-08-07"},
        {"id": "old", "startDate": "2026-07-20", "endDate": "2026-07-21"},
    ]
    assert [
        item["id"]
        for item in filter_overlapping_competitions(
            competitions, date(2026, 8, 6), date(2026, 8, 10)
        )
    ] == ["before"]


def test_flattens_competitions_events_and_rounds():
    competition = {
        "id": "competition-1",
        "wcaId": "WeekendOpen2026",
        "name": "Weekend Open",
        "startDate": "2026-08-05",
        "endDate": "2026-08-07",
        "competitionEvents": [
            {
                "event": {"id": "333", "name": "3x3x3 Cube"},
                "rounds": [
                    {
                        "id": "round-1",
                        "number": 1,
                        "name": "First Round",
                        "format": {
                            "id": "a",
                            "numberOfAttempts": 5,
                            "sortBy": "average",
                        },
                        "cutoff": {"attemptResult": 1000, "numberOfAttempts": 2},
                    },
                    {
                        "id": "round-2",
                        "number": 2,
                        "name": "Final",
                        "format": {
                            "id": "m",
                            "numberOfAttempts": 3,
                            "sortBy": "average",
                        },
                        "cutoff": None,
                    },
                ],
            }
        ],
    }
    targets = flatten_competition_rounds(competition)
    assert [target.round_id for target in targets] == ["round-1", "round-2"]
    assert all(target.event_id == "333" for target in targets)
    assert targets[0].expected_attempts == 5
    assert targets[0].cutoff_attempts == 2
    assert targets[0].cutoff_value == 1000


def test_snapshot_normalization_is_stable_and_timezone_aware():
    result = normalize_round_snapshot(snapshot())["result-1"]
    assert result.attempts == (600, 620, 610)
    assert result.entered_at.tzinfo is not None
    assert result.payload.get("ranking") is None


def test_detects_new_results_and_corrections_but_not_reordering():
    initial_payload = snapshot()
    initial = normalize_round_snapshot(initial_payload)
    second_payload = copy.deepcopy(initial_payload)
    second_payload["results"].append(new_result("result-2"))
    second = normalize_round_snapshot(second_payload)
    added = diff_snapshots(initial, second)
    assert added.additions == ("result-2",)

    corrected_payload = copy.deepcopy(second_payload)
    corrected_payload["results"][1]["attempts"][0]["result"] = 680
    corrected_payload["results"][1]["best"] = 680
    corrected = normalize_round_snapshot(corrected_payload)
    assert diff_snapshots(second, corrected).changes == ("result-2",)

    reordered_payload = copy.deepcopy(second_payload)
    reordered_payload["results"].reverse()
    reordered = normalize_round_snapshot(reordered_payload)
    reordered_diff = diff_snapshots(second, reordered)
    assert not reordered_diff.additions
    assert not reordered_diff.changes
    assert diff_snapshots(second, initial).removals == ("result-2",)


def test_wca_value_comparison_and_formatting():
    assert is_better("333", 325, 326)
    assert not is_better("333", -1, 326)
    assert is_better("333", 326, -1)
    assert comparison_key("333", -1) > comparison_key("333", 326)
    assert format_result("333", "single", 326) == "3.26"
    assert format_result("333", "single", -1) == "DNF"
    assert format_result("333", "single", -2) == "DNS"
    assert format_result("333", "single", 0) == ""


def test_multi_blind_encoding_and_ordering():
    encoded = 910_360_002  # 10/12 in one hour: 8 points, two misses.
    decoded = decode_multi_blind(encoded)
    assert (decoded.solved, decoded.attempted, decoded.centiseconds) == (10, 12, 360_000)
    assert format_result("333mbf", "single", encoded) == "10/12 1:00:00"
    assert is_better("333mbf", encoded - 1, encoded)


def test_fewest_moves_single_and_mean():
    assert format_result("333fm", "single", 25) == "25"
    assert format_result("333fm", "average", 2533) == "25.33"
    assert is_better("333fm", 2500, 2533)


@pytest.mark.django_db
def test_same_record_is_unique_inside_each_pipeline_but_separate_between_pipelines():
    item = candidate()
    api_first, api_created = persist_record_candidate(
        item, RecentRecordObservation.IngestionMethod.API_POLLING, {"source": "api"}
    )
    api_second, api_created_again = persist_record_candidate(
        item, RecentRecordObservation.IngestionMethod.API_POLLING, {"source": "api"}
    )
    subscription, subscription_created = persist_record_candidate(
        item,
        RecentRecordObservation.IngestionMethod.GRAPHQL_SUBSCRIPTION,
        {"source": "subscription"},
    )
    assert api_first.pk == api_second.pk
    assert api_created and not api_created_again and subscription_created
    assert subscription.pk != api_first.pk
    assert RecentRecordObservation.objects.count() == 2


@pytest.mark.django_db
def test_duplicate_snapshot_and_restart_recovery_are_idempotent():
    create_round()
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    initial = snapshot()
    process_round_snapshot("round-1", initial, catchup_minutes=60, observed_at=observed_at)
    assert SubscriptionResultState.objects.count() == 1
    assert RecentRecordObservation.objects.count() == 0
    assert SourceObservation.objects.count() == 1

    duplicate = process_round_snapshot(
        "round-1", initial, catchup_minutes=60, observed_at=observed_at
    )
    assert duplicate["duplicate"]
    assert SubscriptionResultState.objects.count() == 1
    assert SourceObservation.objects.count() == 1

    restarted_payload = copy.deepcopy(initial)
    restarted_payload["results"][0]["ranking"] = 9
    result = process_round_snapshot(
        "round-1", restarted_payload, catchup_minutes=60, observed_at=observed_at
    )
    assert result["additions"] == 0
    assert result["changes"] == 0
    assert RecentRecordObservation.objects.count() == 0


@pytest.mark.django_db
def test_snapshot_marks_only_the_finalized_kind_whose_value_changed(
    monkeypatch,
):
    create_round()
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    initial = snapshot()
    first = process_round_snapshot("round-1", initial, catchup_minutes=300, observed_at=observed_at)
    assert first["classification_scopes_queued"] == 2
    assert set(ClassificationScopeWork.objects.values_list("kind", "requested_version")) == {
        ("single", 1),
        ("average", 1),
    }

    changed = copy.deepcopy(initial)
    changed["results"][0]["attempts"].append({"result": 630})
    changed["results"][0]["average"] = 615
    reconciled = []
    original_reconcile = subscription_ingestion.reconcile_result_observation

    def count_reconcile(row, **kwargs):
        reconciled.append(row.observation_key)
        return original_reconcile(row, **kwargs)

    monkeypatch.setattr(
        subscription_ingestion,
        "reconcile_result_observation",
        count_reconcile,
    )
    second = process_round_snapshot(
        "round-1",
        changed,
        catchup_minutes=300,
        observed_at=observed_at + timedelta(seconds=1),
    )

    assert second["changes"] == 1
    assert second["classification_scopes_queued"] == 1
    assert len(reconciled) == 1
    assert set(ClassificationScopeWork.objects.values_list("kind", "requested_version")) == {
        ("single", 1),
        ("average", 2),
    }


@pytest.mark.django_db
def test_four_of_five_attempts_stay_only_in_provider_state_until_finalized():
    target = create_round()
    target.format_id = "a"
    target.expected_attempts = 5
    target.save(update_fields=["format_id", "expected_attempts", "updated_at"])
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    unfinished = snapshot()
    unfinished["results"][0]["attempts"] = [
        {"result": 600},
        {"result": 620},
        {"result": 610},
        {"result": 590},
    ]
    unfinished["results"][0]["best"] = 590
    unfinished["results"][0]["average"] = 605

    stats = process_round_snapshot(
        "round-1", unfinished, catchup_minutes=300, observed_at=observed_at
    )

    assert stats["classification_scopes_queued"] == 0
    assert SubscriptionResultState.objects.get().attempts == [600, 620, 610, 590]
    assert not ResultObservation.objects.exists()
    assert not CanonicalResult.objects.exists()

    finalized = copy.deepcopy(unfinished)
    finalized["results"][0]["attempts"].append({"result": -1})
    finalized["results"][0]["average"] = 610
    stats = process_round_snapshot(
        "round-1",
        finalized,
        catchup_minutes=300,
        observed_at=observed_at + timedelta(seconds=1),
    )

    assert stats["classification_scopes_queued"] == 2
    assert set(ResultObservation.objects.values_list("kind", flat=True)) == {
        "single",
        "average",
    }
    assert CanonicalResult.objects.count() == 2


@pytest.mark.django_db
def test_failed_cutoff_finalizes_one_single_and_no_average():
    target = create_round()
    target.format_id = "a"
    target.expected_attempts = 5
    target.cutoff_attempts = 2
    target.cutoff_value = 500
    target.save(
        update_fields=[
            "format_id",
            "expected_attempts",
            "cutoff_attempts",
            "cutoff_value",
            "updated_at",
        ]
    )
    payload = snapshot()
    payload["results"][0]["attempts"] = [{"result": 600}, {"result": -1}]
    payload["results"][0]["best"] = 600
    payload["results"][0]["average"] = 0

    stats = process_round_snapshot(
        "round-1",
        payload,
        catchup_minutes=300,
        observed_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )

    assert stats["classification_scopes_queued"] == 1
    assert list(CanonicalResult.objects.values_list("kind", "value")) == [("single", 600)]


@pytest.mark.django_db
def test_correction_to_unfinished_retracts_then_reuses_finalized_rows():
    target = create_round()
    target.format_id = "a"
    target.expected_attempts = 5
    target.save(update_fields=["format_id", "expected_attempts", "updated_at"])
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    finalized = snapshot()
    finalized["results"][0]["attempts"] = [
        {"result": 600},
        {"result": 620},
        {"result": 610},
        {"result": 590},
        {"result": 630},
    ]
    finalized["results"][0]["best"] = 590
    finalized["results"][0]["average"] = 610
    process_round_snapshot(
        "round-1", finalized, catchup_minutes=300, observed_at=observed_at
    )
    original_ids = dict(CanonicalResult.objects.values_list("kind", "pk"))

    unfinished = copy.deepcopy(finalized)
    unfinished["results"][0]["attempts"].pop()
    stats = process_round_snapshot(
        "round-1",
        unfinished,
        catchup_minutes=300,
        observed_at=observed_at + timedelta(seconds=1),
    )
    assert stats["classification_scopes_queued"] == 2
    assert set(CanonicalResult.objects.values_list("status", flat=True)) == {"retracted"}

    corrected = copy.deepcopy(finalized)
    corrected["results"][0]["attempts"][0]["result"] = 580
    corrected["results"][0]["best"] = 580
    stats = process_round_snapshot(
        "round-1",
        corrected,
        catchup_minutes=300,
        observed_at=observed_at + timedelta(seconds=2),
    )
    assert stats["classification_scopes_queued"] == 2
    assert dict(CanonicalResult.objects.values_list("kind", "pk")) == original_ids
    single = CanonicalResult.objects.get(kind="single")
    assert single.value == 580
    assert single.revision == 2


@pytest.mark.django_db
def test_record_snapshot_sequence_and_correction():
    create_round()
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    first = snapshot()
    process_round_snapshot("round-1", first, catchup_minutes=60, observed_at=observed_at)

    second = copy.deepcopy(first)
    second["results"].append(new_result("result-2"))
    stats = process_round_snapshot("round-1", second, observed_at=observed_at)
    assert stats["additions"] == 1 and stats["records_detected"] == 0

    third = copy.deepcopy(second)
    third["results"].append(new_result("result-3", best=480, single_tag="WR"))
    stats = process_round_snapshot("round-1", third, observed_at=observed_at)
    assert stats["records_detected"] == 1
    record = RecentRecordObservation.objects.get()
    assert record.raw_result == 480
    assert record.detected_at.tzinfo is not None

    repeated = process_round_snapshot("round-1", third, observed_at=observed_at)
    assert repeated["duplicate"]
    assert RecentRecordObservation.objects.count() == 1

    fifth = copy.deepcopy(third)
    fifth["results"][2]["attempts"][0]["result"] = 475
    fifth["results"][2]["best"] = 475
    stats = process_round_snapshot("round-1", fifth, observed_at=observed_at)
    assert stats["changes"] == 1
    assert RecentRecordObservation.objects.count() == 1
    record.refresh_from_db()
    assert record.raw_result == 475
    original_detected_at = record.detected_at

    withdrawn = copy.deepcopy(fifth)
    withdrawn["results"][2]["singleRecordTag"] = None
    process_round_snapshot("round-1", withdrawn, observed_at=observed_at)
    record.refresh_from_db()
    assert record.status == RecentRecordObservation.Status.WITHDRAWN
    assert record.detected_at == original_detected_at


@pytest.mark.django_db
def test_initial_snapshot_catches_up_only_recent_unprocessed_records():
    create_round()
    observed_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    initial = snapshot()
    initial["results"].append(new_result("result-recent", best=479, single_tag="WR"))
    stats = process_round_snapshot("round-1", initial, catchup_minutes=60, observed_at=observed_at)
    assert stats["initial_snapshot"]
    assert stats["records_detected"] == 1
    assert RecentRecordObservation.objects.get().stable_result_identity == "result-recent"
