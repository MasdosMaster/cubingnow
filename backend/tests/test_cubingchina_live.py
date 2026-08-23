import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.records.models import (
    CanonicalResult,
    CubingChinaCompetitionTarget,
    CubingChinaDiffTable,
    CubingChinaRoundTarget,
    IngestionWorkerStatus,
    RecentRecordObservation,
    ResultObservation,
)
from integrations.attendance_types import SourceCompetition
from integrations.cubingchina.live_client import CubingChinaWebSocketClient
from integrations.cubingchina.live_discovery import (
    discover_live_competitions,
    rolling_discovery_window,
)
from integrations.cubingchina.live_ingestion import (
    process_result_update,
    process_round_snapshot,
)
from integrations.cubingchina.live_parser import parse_live_competition
from integrations.cubingchina.live_schemas import (
    CubingChinaDiscoveryEntry,
    CubingChinaLiveCompetition,
    CubingChinaRoundDescriptor,
)
from integrations.cubingchina.live_snapshots import (
    CubingChinaPayloadError,
    diff_snapshots,
    normalize_record_tag,
    normalize_snapshot,
)
from integrations.cubingchina.live_supervisor import CubingChinaLiveSupervisor
from integrations.wca_live.ingestion import persist_record_candidate
from integrations.wca_live.schemas import RecordCandidate

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name):
    return (FIXTURES / name).read_text()


def fixture_json(name):
    return json.loads(fixture_text(name))


def source_competition():
    return SourceCompetition(
        source="cubingchina",
        source_id="China-Open-2026",
        wca_id="ChinaOpen2026",
        name="China Open 2026",
        start_date=date(2026, 8, 9),
        end_date=date(2026, 8, 9),
        country_code="CN",
    )


def test_parses_live_competition_and_normalizes_round_order():
    live = parse_live_competition(fixture_text("cubingchina_live.html"), source_competition())
    assert live.cubingchina_id == 2468
    assert [(row.event_id, row.round_id, row.round_number) for row in live.rounds] == [
        ("333", "1", 1),
        ("333", "f", 2),
        ("222", "d", 1),
    ]


def test_discovery_keeps_official_competitions_and_loads_live_metadata():
    class FakeClient:
        def get_page(self, path, **_params):
            if path == "/competition":
                return fixture_text("cubingchina_competitions.html")
            if path == "/competition/China-Open-2026":
                return fixture_text("cubingchina_competition_detail.html")
            if path == "/competition/Local-Cube-Festival-2026":
                return fixture_text("cubingchina_non_wca_detail.html")
            if path == "/live/China-Open-2026":
                return fixture_text("cubingchina_live.html")
            raise AssertionError(path)

    entries, metadata = discover_live_competitions(
        FakeClient(), date(2026, 8, 5), lookback_days=1, lookahead_days=5
    )
    assert len(entries) == 1
    assert entries[0].live.cubingchina_id == 2468
    assert metadata["live_competitions_ready"] == 1


def test_discovery_retains_pending_live_pages_for_later_cycles():
    class FakeClient:
        def get_page(self, path, **_params):
            if path == "/competition":
                return fixture_text("cubingchina_competitions.html")
            if path == "/competition/China-Open-2026":
                return fixture_text("cubingchina_competition_detail.html")
            if path == "/competition/Local-Cube-Festival-2026":
                return fixture_text("cubingchina_non_wca_detail.html")
            if path == "/live/China-Open-2026":
                raise OSError("live page is not published")
            raise AssertionError(path)

    entries, metadata = discover_live_competitions(
        FakeClient(), date(2026, 8, 5), lookback_days=1, lookahead_days=5
    )
    assert len(entries) == 1
    assert entries[0].live is None
    assert entries[0].error == "live page is not published"
    assert metadata["pending_live_pages"] == 1


def test_discovery_marks_detail_failures_without_claiming_they_are_official():
    class FakeClient:
        def get_page(self, path, **_params):
            if path == "/competition":
                return fixture_text("cubingchina_competitions.html")
            if path == "/competition/China-Open-2026":
                raise OSError("temporary detail failure")
            if path == "/competition/Local-Cube-Festival-2026":
                return fixture_text("cubingchina_non_wca_detail.html")
            raise AssertionError(path)

    entries, _metadata = discover_live_competitions(
        FakeClient(), date(2026, 8, 5), lookback_days=1, lookahead_days=5
    )
    assert len(entries) == 1
    assert entries[0].detail_verified is False
    assert entries[0].wca_competition_id == ""


def test_normalization_is_order_independent_and_validates_pending_round():
    users = fixture_json("cubingchina_users.json")
    rows = fixture_json("cubingchina_round_snapshot.json")
    first = normalize_snapshot(rows, users, 2468, "333", "1")
    second = normalize_snapshot(list(reversed(rows)), users, 2468, "333", "1")
    assert diff_snapshots(first, second).unchanged == ("9001",)
    assert first["9001"].country_code == "NL"
    bad = [{**rows[0], "r": "f"}]
    with pytest.raises(CubingChinaPayloadError, match="unexpected round"):
        normalize_snapshot(bad, users, 2468, "333", "1")


@pytest.mark.parametrize("tag", ["AfR", "AsR", "ER", "NAR", "OcR", "SAR"])
def test_continental_record_tags_are_normalized_to_cr(tag):
    for variant in (tag, tag.lower(), tag.upper(), tag.swapcase()):
        assert normalize_record_tag(variant) == "CR"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("wr", "WR"), ("Cr", "CR"), ("nR", "NR"), ("  AsR  ", "CR"), (None, "")],
)
def test_record_tag_normalization_preserves_generic_levels(tag, expected):
    assert normalize_record_tag(tag) == expected


def create_target():
    competition = CubingChinaCompetitionTarget.objects.create(
        slug="China-Open-2026",
        cubingchina_id=2468,
        wca_competition_id="ChinaOpen2026",
        competition_name="China Open 2026",
        competition_start_date=date(2026, 8, 9),
        competition_end_date=date(2026, 8, 9),
        status=CubingChinaCompetitionTarget.Status.ACTIVE,
    )
    return CubingChinaRoundTarget.objects.create(
        competition=competition,
        event_id="333",
        event_name="3x3x3 Cube",
        round_id="1",
        round_number=1,
        round_name="First round",
        format="a",
    )


@pytest.mark.django_db
def test_snapshot_ingestion_is_independent_idempotent_and_handles_corrections():
    target = create_target()
    users = fixture_json("cubingchina_users.json")
    rows = fixture_json("cubingchina_round_snapshot.json")
    observed_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)

    first = process_round_snapshot(target.pk, rows, users, observed_at=observed_at)
    repeated_at = observed_at + timedelta(minutes=1)
    duplicate = process_round_snapshot(target.pk, rows, users, observed_at=repeated_at)
    assert first["records_detected"] == 2
    assert duplicate["duplicate"] is True
    assert (
        RecentRecordObservation.objects.filter(ingestion_method="cubingchina_websocket").count()
        == 2
    )
    assert not RecentRecordObservation.objects.exclude(
        ingestion_method="cubingchina_websocket"
    ).exists()
    single = RecentRecordObservation.objects.get(kind="single")
    assert single.detected_at == observed_at
    assert single.source_result_id == "9001"
    assert single.wca_live_result_id == ""
    assert single.formatted_result == "3.26"
    assert CubingChinaDiffTable.objects.get().last_observed_at == repeated_at

    correction = {**rows[0], "b": 320, "v": [320, 450, 460, 440, 500], "sr": "NR"}
    process_result_update(
        target.pk,
        correction,
        users,
        "result.update",
        observed_at=datetime(2026, 8, 9, 8, 5, tzinfo=UTC),
    )
    single.refresh_from_db()
    assert single.status == RecentRecordObservation.Status.WITHDRAWN
    replacement = RecentRecordObservation.objects.get(kind="single", record_level="NR")
    assert replacement.raw_result == 320

    removed = {**correction, "sr": ""}
    process_result_update(
        target.pk,
        removed,
        users,
        "result.update",
        observed_at=datetime(2026, 8, 9, 8, 10, tzinfo=UTC),
    )
    replacement.refresh_from_db()
    assert replacement.status == RecentRecordObservation.Status.WITHDRAWN
    assert CubingChinaDiffTable.objects.get().active is True

    process_round_snapshot(
        target.pk,
        [],
        users,
        observed_at=datetime(2026, 8, 9, 8, 15, tzinfo=UTC),
    )
    assert CubingChinaDiffTable.objects.get().active is False


@pytest.mark.django_db
def test_positive_cubingchina_average_is_not_canonical_until_all_attempts_are_entered():
    target = create_target()
    users = fixture_json("cubingchina_users.json")
    unfinished = {
        **fixture_json("cubingchina_round_snapshot.json")[0],
        "v": [326, 450, 460, 440],
        "a": 450,
    }

    stats = process_round_snapshot(target.pk, [unfinished], users)

    assert stats["classification_scopes_queued"] == 0
    assert CubingChinaDiffTable.objects.get().attempts == [326, 450, 460, 440]
    assert not ResultObservation.objects.exists()
    assert not CanonicalResult.objects.exists()

    finalized = {**unfinished, "v": [326, 450, 460, 440, -1], "a": 450}
    stats = process_result_update(
        target.pk,
        finalized,
        users,
        "result.update",
    )

    assert stats["classification_scopes_queued"] == 2
    assert set(CanonicalResult.objects.values_list("kind", flat=True)) == {
        "single",
        "average",
    }


@pytest.mark.django_db
def test_finalized_dnf_average_is_preserved_as_a_non_classifiable_fact():
    target = create_target()
    row = {
        **fixture_json("cubingchina_round_snapshot.json")[0],
        "v": [-1, -1, -1, -1, -1],
        "b": -1,
        "a": -1,
        "sr": "",
        "ar": "",
    }

    process_round_snapshot(target.pk, [row], fixture_json("cubingchina_users.json"))

    assert set(CanonicalResult.objects.values_list("kind", "value")) == {
        ("single", -1),
        ("average", -1),
    }


@pytest.mark.django_db
def test_cubingchina_failed_cutoff_finalizes_without_trusting_average_field():
    target = create_target()
    target.cutoff = 5
    target.save(update_fields=["cutoff", "updated_at"])
    row = {
        **fixture_json("cubingchina_round_snapshot.json")[0],
        "v": [600, -1, 0, 0, 0],
        "b": 600,
        "a": 450,
        "sr": "",
        "ar": "WR",
    }

    process_round_snapshot(target.pk, [row], fixture_json("cubingchina_users.json"))

    assert list(CanonicalResult.objects.values_list("kind", "value")) == [("single", 600)]


@pytest.mark.django_db
def test_unknown_record_tags_are_persisted_as_state_but_not_observations():
    target = create_target()
    row = {**fixture_json("cubingchina_round_snapshot.json")[0], "sr": "PR", "ar": ""}
    process_round_snapshot(target.pk, [row], fixture_json("cubingchina_users.json"))
    assert CubingChinaDiffTable.objects.get().single_record_tag == "PR"
    assert RecentRecordObservation.objects.count() == 0


@pytest.mark.django_db
def test_asian_record_tag_creates_continental_observation():
    target = create_target()
    row = {
        **fixture_json("cubingchina_round_snapshot.json")[0],
        "sr": "AsR",
        "ar": "",
    }
    result = process_round_snapshot(target.pk, [row], fixture_json("cubingchina_users.json"))
    observation = RecentRecordObservation.objects.get()
    assert result["records_detected"] == 1
    assert observation.kind == RecentRecordObservation.Kind.SINGLE
    assert observation.record_level == RecentRecordObservation.Level.CONTINENTAL


@pytest.mark.django_db
def test_api_exposes_cubingchina_source_and_health():
    target = create_target()
    process_round_snapshot(
        target.pk,
        fixture_json("cubingchina_round_snapshot.json"),
        fixture_json("cubingchina_users.json"),
    )
    response = APIClient().get("/api/recent-records/?source=cubingchina_websocket")
    assert response.status_code == 200
    assert {row["ingestion_method"] for row in response.json()["results"]} == {
        "cubingchina_websocket"
    }
    assert response.json()["results"][0]["source_name"] == "CubingChina Live"

    status = APIClient().get("/api/ingestion-status/").json()["cubingchina_websocket"]
    assert status["target_competition_count"] == 1
    assert status["target_round_count"] == 1


@pytest.mark.django_db
def test_provider_neutral_identity_matches_wca_live_and_cubingchina():
    target = create_target()
    observed_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    process_round_snapshot(
        target.pk,
        fixture_json("cubingchina_round_snapshot.json"),
        fixture_json("cubingchina_users.json"),
        observed_at=observed_at,
    )
    persist_record_candidate(
        RecordCandidate(
            stable_result_identity="wca-live-result-1",
            wca_live_record_id="recent-record-1",
            wca_live_result_id="wca-live-result-1",
            wca_live_competition_id="live-competition-1",
            wca_competition_id="ChinaOpen2026",
            competition_name="China Open 2026",
            competition_start_date=date(2026, 8, 9),
            competition_end_date=date(2026, 8, 9),
            round_id="live-round-1",
            round_number=1,
            round_name="First round",
            event_id="333",
            event_name="3x3x3 Cube",
            competitor_name="Test Cuber",
            competitor_wca_id="2020TEST01",
            competitor_wca_live_id="live-person-1",
            country_code="NL",
            kind="single",
            raw_result=326,
            record_level="WR",
            source_url="https://live.worldcubeassociation.org/competitions/live-competition-1",
            source_update_timestamp=observed_at,
            observed_at=observed_at,
        ),
        RecentRecordObservation.IngestionMethod.API_POLLING,
        {"source": "wca-live"},
    )
    response = APIClient().get("/api/recent-records/?source=cubingchina_websocket")
    single = next(row for row in response.json()["results"] if row["kind"] == "single")
    assert single["matched_in_other_pipeline"] is True
    assert single["matching_pipelines"] == ["api_polling"]


def test_websocket_client_uses_verified_read_only_frames():
    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.received = asyncio.Queue()
            self.closed = False

        async def send(self, value):
            self.sent.append(json.loads(value))

        async def recv(self):
            return await self.received.get()

        async def close(self):
            self.closed = True

    async def scenario():
        socket = FakeSocket()

        async def connect_factory(*_args, **_kwargs):
            return socket

        client = CubingChinaWebSocketClient(
            "wss://example.test/ws",
            keepalive_seconds=3600,
            connect_factory=connect_factory,
        )
        await client.connect()
        await client.select_competition(2468)
        await client.request_rounds()
        await client.request_results("333", "1")
        await socket.received.put(json.dumps({"code": 200, "type": "result.all", "data": []}))
        assert (await client.next_message())["type"] == "result.all"
        diagnostics = client.websocket_diagnostics
        assert diagnostics["message_queue_size"] == 0
        assert diagnostics["peak_message_queue_size"] == 1
        assert diagnostics["counters"]["frames_received"] == 1
        assert diagnostics["counters"]["messages_queued"] == 1
        assert diagnostics["counters"]["messages_dequeued"] == 1
        assert socket.sent == [
            {"type": "competition", "competitionId": 2468},
            {"type": "result", "action": "rounds"},
            {
                "type": "result",
                "action": "fetch",
                "params": {
                    "event": "333",
                    "round": "1",
                    "filter": "all",
                    "combine": False,
                },
            },
        ]
        await client.close()
        assert socket.closed

    asyncio.run(scenario())


def test_websocket_keepalive_sends_the_json_ping_string():
    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(json.loads(value))

        async def close(self):
            pass

    async def scenario():
        socket = FakeSocket()

        async def connect_factory(*_args, **_kwargs):
            return socket

        client = CubingChinaWebSocketClient(
            "wss://example.test/ws",
            keepalive_seconds=1,
            connect_factory=connect_factory,
        )
        await client.connect()
        await asyncio.sleep(1.05)
        await client.close()
        assert "ping" in socket.sent

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_collector_reconnects_resubscribes_and_refetches_snapshots():
    round_target = create_target()
    clients = []
    supervisor = None

    class FakeClient:
        def __init__(self, stop_after_snapshot):
            self.stop_after_snapshot = stop_after_snapshot
            self.messages = [
                {"type": "users", "data": fixture_json("cubingchina_users.json")},
                {"type": "result.all", "data": []},
            ]
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def select_competition(self, competition_id):
            self.calls.append(("competition", competition_id))

        async def request_rounds(self):
            self.calls.append(("rounds",))

        async def request_results(self, event_id, round_id):
            self.calls.append(("fetch", event_id, round_id))

        async def next_message(self):
            if self.messages:
                message = self.messages.pop(0)
                if message["type"] == "result.all" and self.stop_after_snapshot:
                    supervisor.stop()
                return message
            raise ConnectionError("simulated disconnect")

    def client_factory(*_args, **_kwargs):
        client = FakeClient(stop_after_snapshot=len(clients) == 1)
        clients.append(client)
        return client

    supervisor = CubingChinaLiveSupervisor(
        "https://example.test",
        "wss://example.test/ws",
        retry_base_seconds=0,
        retry_max_seconds=0,
        websocket_client_factory=client_factory,
    )

    async def scenario():
        try:
            await supervisor._collector_loop(round_target.competition_id)
        finally:
            await supervisor._db(connections.close_all)

    asyncio.run(scenario())

    assert len(clients) == 2
    assert [client.calls for client in clients] == [
        [("competition", 2468), ("rounds",), ("fetch", "333", "1")],
        [("competition", 2468), ("rounds",), ("fetch", "333", "1")],
    ]


def test_rolling_window_is_dynamic_not_hardcoded():
    assert rolling_discovery_window(date(2030, 1, 10), 1, 7) == (
        date(2030, 1, 9),
        date(2030, 1, 17),
    )


def test_completion_grace_keeps_competition_collectable_for_720_minutes():
    competition = source_competition()
    entry = CubingChinaDiscoveryEntry(
        slug=competition.source_id,
        wca_competition_id=competition.wca_id,
        competition_name=competition.name,
        competition_start_date=competition.start_date,
        competition_end_date=competition.end_date,
    )
    supervisor = CubingChinaLiveSupervisor(
        "https://example.test",
        "wss://example.test/ws",
        completion_grace_minutes=720,
    )
    grace_ends = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

    assert supervisor._entry_is_collectable(entry, grace_ends) is True
    assert supervisor._entry_is_collectable(entry, grace_ends + timedelta(seconds=1)) is False


def test_competition_socket_concurrency_is_bounded():
    supervisor = CubingChinaLiveSupervisor(
        "https://example.test", "wss://example.test/ws", max_connections=2
    )

    async def scenario():
        release = asyncio.Event()
        two_entered = asyncio.Event()
        active = 0
        maximum = 0

        async def contender():
            nonlocal active, maximum
            async with supervisor._semaphore:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    two_entered.set()
                await release.wait()
                active -= 1

        tasks = [asyncio.create_task(contender()) for _ in range(3)]
        await asyncio.wait_for(two_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == 2
        assert maximum == 2
        assert sum(task.done() for task in tasks) == 0
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())


@pytest.mark.django_db
def test_discovery_persistence_adds_new_targets_and_preserves_ready_state_on_failure():
    today = datetime.now(tz=UTC).date()

    def entry(slug, source_id):
        source_round = CubingChinaRoundDescriptor(
            event_id="333",
            event_name="3x3x3 Cube",
            round_id="1",
            round_number=1,
            round_name="First round",
            format="a",
            cutoff=0,
            time_limit=180,
            status=0,
        )
        return CubingChinaDiscoveryEntry(
            slug=slug,
            wca_competition_id=slug.replace("-", ""),
            competition_name=slug,
            competition_start_date=today,
            competition_end_date=today,
            live=CubingChinaLiveCompetition(
                slug=slug,
                cubingchina_id=source_id,
                wca_competition_id=slug.replace("-", ""),
                competition_name=slug,
                competition_start_date=today,
                competition_end_date=today,
                rounds=(source_round,),
            ),
        )

    IngestionWorkerStatus.objects.create(ingestion_method="cubingchina_websocket")
    supervisor = CubingChinaLiveSupervisor(
        "https://example.test", "wss://example.test/ws", max_connections=2
    )
    first = entry("First-Open-2030", 1001)
    second = entry("Second-Open-2030", 1002)
    assert supervisor._persist_discovery([first], {}) == {
        CubingChinaCompetitionTarget.objects.get(slug=first.slug).pk
    }
    assert len(supervisor._persist_discovery([first, second], {})) == 2
    assert CubingChinaCompetitionTarget.objects.count() == 2
    assert CubingChinaRoundTarget.objects.count() == 2
    assert supervisor._semaphore._value == 2

    pending = CubingChinaDiscoveryEntry(
        slug=first.slug,
        wca_competition_id=first.wca_competition_id,
        competition_name=first.competition_name,
        competition_start_date=today,
        competition_end_date=today,
        error="temporary live-page failure",
    )
    ids = supervisor._persist_discovery([pending, second], {})
    persisted = CubingChinaCompetitionTarget.objects.get(slug=first.slug)
    assert persisted.cubingchina_id == 1001
    assert persisted.pk in ids

    supervisor._persist_discovery([pending], {})
    retired = CubingChinaCompetitionTarget.objects.get(slug=second.slug)
    assert retired.status == CubingChinaCompetitionTarget.Status.RETIRED
    assert retired.active is False
