import io
import json
import zipfile
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.db import connection

from apps.records import baseline_export
from apps.records.baseline_export import (
    _copy_table,
    baseline_projection_sql,
    inspect_tsv_archive,
    refresh_wca_baseline,
)
from apps.records.event_columns import AVERAGE_EVENT_IDS, SINGLE_EVENT_IDS, event_field
from apps.records.models import (
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    LiveRecordsAverage,
    LiveRecordsSingle,
)


def _tsv(columns, rows):
    target = io.StringIO(newline="")
    writer = __import__("csv").writer(target, delimiter="\t", lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return target.getvalue()


def export_tsv_zip(*, format_version="v2.0.2", overrides=None) -> bytes:
    tables = {
        "continents": _tsv(
            ["id", "name", "record_name"],
            [["_Europe", "Europe", "ER"], ["_South America", "South America", "SAR"]],
        ),
        "countries": _tsv(
            ["id", "name", "continent_id", "iso2"],
            [
                ["Netherlands", "Netherlands", "_Europe", "NL"],
                ["Argentina", "Argentina", "_South America", "AR"],
            ],
        ),
        "persons": _tsv(
            ["wca_id", "sub_id", "name", "country_id", "gender"],
            [
                ["2020TEST01", "1", "Test Cuber", "Netherlands", "m"],
                ["2020TEST01", "2", "Test Cuber", "Argentina", "m"],
                ["2020TIEE01", "1", "Tie Cuber", "Netherlands", "f"],
            ],
        ),
        "ranks_average": _tsv(
            ["person_id", "event_id", "best", "world_rank", "continent_rank", "country_rank"],
            [["2020TEST01", "333", "1000", "1", "1", "1"]],
        ),
        "ranks_single": _tsv(
            ["person_id", "event_id", "best", "world_rank", "continent_rank", "country_rank"],
            [
                ["2020TEST01", "333", "900", "1", "1", "1"],
                ["2020TIEE01", "333", "900", "1", "1", "1"],
            ],
        ),
        "results": _tsv(
            ["id", "competition_id", "event_id", "person_id"],
            [
                ["1", "IncludedOpen2026", "333", "2020TEST01"],
                ["2", "DnfOnlyOpen2026", "333", "2020TIEE01"],
            ],
        ),
        "events": _tsv(["id", "name", "format"], [["333", "3x3x3 Cube", "time"]]),
    }
    tables.update(overrides or {})
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for table_name, contents in tables.items():
            archive.writestr(f"WCA_export_{table_name}.tsv", contents)
        archive.writestr(
            "metadata.json",
            json.dumps(
                {
                    "export_format_version": format_version,
                    "export_date": "2026-08-30 11:07:14 UTC",
                }
            ),
        )
    return target.getvalue()


def inspect_fixture(archive_bytes=None):
    archive = zipfile.ZipFile(io.BytesIO(archive_bytes or export_tsv_zip()))
    manifest = inspect_tsv_archive(
        archive,
        source_filename="WCA_export_v2.tsv.zip",
        downloaded_at=datetime(2026, 8, 31, tzinfo=UTC),
        content_hash="abc123",
    )
    return archive, manifest


def test_tsv_manifest_keeps_every_export_table_and_validates_projection_inputs():
    archive, manifest = inspect_fixture()
    with archive:
        assert {table.name for table in manifest.tables} == {
            "continents",
            "countries",
            "events",
            "persons",
            "ranks_average",
            "ranks_single",
            "results",
        }
    assert manifest.export_generated_at == datetime(2026, 8, 30, 11, 7, 14, tzinfo=UTC)
    assert manifest.source_version == "v2.0.2:abc123"


@pytest.mark.parametrize("format_version", ["v1.0.0", "v3.0.0", "2.0.2", ""])
def test_only_v2_export_metadata_is_accepted(format_version):
    with pytest.raises(ValueError, match="v2 required"):
        inspect_fixture(export_tsv_zip(format_version=format_version))


def test_required_tsv_columns_are_validated():
    archive_bytes = export_tsv_zip(
        overrides={"ranks_single": _tsv(["person_id", "event_id"], [["2020TEST01", "333"]])}
    )
    with pytest.raises(ValueError, match="missing required columns: best"):
        inspect_fixture(archive_bytes)


def test_unsafe_or_duplicate_tsv_identifiers_are_rejected():
    unsafe = export_tsv_zip(overrides={"bad-name": _tsv(["safe"], [["value"]])})
    with pytest.raises(ValueError, match="Unsafe WCA TSV table name"):
        inspect_fixture(unsafe)

    duplicate = export_tsv_zip(
        overrides={"events": _tsv(["id", "id"], [["333", "duplicate"]])}
    )
    with pytest.raises(ValueError, match="Duplicate WCA TSV columns"):
        inspect_fixture(duplicate)


def test_copy_streams_member_after_using_header_as_raw_text_columns():
    archive, manifest = inspect_fixture()
    table = next(table for table in manifest.tables if table.name == "events")

    class Copy:
        def __init__(self):
            self.chunks = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, chunk):
            self.chunks.append(chunk)

    class Cursor:
        def __init__(self):
            self.statements = []
            self.copier = Copy()

        def execute(self, statement, params=None):
            self.statements.append((statement, params))

        def copy(self, statement):
            self.copy_statement = statement
            return self.copier

        def fetchone(self):
            return [1]

    cursor = Cursor()
    with archive:
        assert _copy_table(cursor, archive, table) == 1
    assert "CREATE TABLE" in cursor.statements[0][0]
    assert '"events"' in cursor.statements[0][0]
    assert "COPY" in cursor.copy_statement
    assert "QUOTE E'\\x01'" in cursor.copy_statement
    assert "NULL 'NULL'" in cursor.copy_statement
    copied = b"".join(cursor.copier.chunks)
    assert not copied.startswith(b"id\tname")
    assert b"3x3x3 Cube" in copied


def test_copy_treats_double_quotes_as_plain_tsv_data():
    archive_bytes = export_tsv_zip(
        overrides={"events": _tsv(["id", "name", "format"], [["333", 'A stray " quote', "time"]])}
    )
    archive, manifest = inspect_fixture(archive_bytes)
    table = next(table for table in manifest.tables if table.name == "events")

    class Copy:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, chunk):
            self.chunk = chunk

    class Cursor:
        def execute(self, statement, params=None):
            pass

        def copy(self, statement):
            self.copy_statement = statement
            return Copy()

        def fetchone(self):
            return [1]

    cursor = Cursor()
    with archive:
        _copy_table(cursor, archive, table)
    assert "QUOTE E'\\x01'" in cursor.copy_statement


def test_projection_sql_builds_all_scopes_and_only_safe_fixed_event_columns():
    single_sql = baseline_projection_sql(
        BaselineRecordsSingle, "ranks_single", SINGLE_EVENT_IDS
    )
    average_sql = baseline_projection_sql(
        BaselineRecordsAverage, "ranks_average", AVERAGE_EVENT_IDS
    )

    assert '"wca_export"."ranks_single"' in single_sql
    assert "'World'::text AS record_holder" in single_sql
    assert "SELECT continent_name, 'CR'" in single_sql
    assert "SELECT country_name, 'NR'" in single_sql
    assert "SELECT person_id, 'PR'" in single_sql
    assert 'AS "333mbf"' in single_sql
    assert 'AS "333mbf"' not in average_sql


def test_refresh_discovers_and_streams_the_current_v2_tsv_zip(monkeypatch):
    archive_bytes = export_tsv_zip()
    calls = []

    class Response:
        headers = {}
        url = SimpleNamespace(path="/results/WCA_export_v2_test.tsv.zip")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "export_version": "v2.0.2",
                "sql_url": "https://exports.example.test/must-not-be-used.sql.zip",
                "tsv_url": "https://exports.example.test/current.tsv.zip",
            }

        def iter_bytes(self):
            yield archive_bytes[:100]
            yield archive_bytes[100:]

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            calls.append(("get", url))
            return Response()

        def stream(self, method, url):
            calls.append(("stream", method, url))
            return Response()

    loaded = {}

    def load(archive, manifest):
        loaded["tables"] = {table.name for table in manifest.tables}
        return {table.name: 1 for table in manifest.tables}

    def activate(manifest):
        return SimpleNamespace(source_filename=manifest.source_filename)

    monkeypatch.setattr(baseline_export.httpx, "Client", Client)
    monkeypatch.setattr(baseline_export, "_require_postgresql", lambda: None)
    monkeypatch.setattr(baseline_export, "_export_refresh_lock", nullcontext)
    monkeypatch.setattr(baseline_export, "load_tsv_archive_into_staging", load)
    monkeypatch.setattr(baseline_export, "activate_staged_export", activate)

    metadata = refresh_wca_baseline(
        url="https://www.worldcubeassociation.org/api/v0/export/public"
    )

    assert ("get", "https://www.worldcubeassociation.org/api/v0/export/public") in calls
    assert ("stream", "GET", "https://exports.example.test/current.tsv.zip") in calls
    assert not any("sql" in str(call) for call in calls)
    assert "events" in loaded["tables"]
    assert metadata.source_filename == "WCA_export_v2_test.tsv.zip"


def test_non_postgresql_refresh_fails_before_downloading():
    if connection.vendor == "postgresql":
        pytest.skip("Test environment uses PostgreSQL")
    with pytest.raises(RuntimeError, match="requires PostgreSQL COPY"):
        refresh_wca_baseline(url="https://example.test/export.tsv.zip")


def test_wide_tables_expose_the_complete_fixed_safe_event_schema():
    for model in (BaselineRecordsSingle, LiveRecordsSingle):
        assert {
            model._meta.get_field(event_field(event_id, "single")).db_column
            for event_id in SINGLE_EVENT_IDS
        } == set(SINGLE_EVENT_IDS)
    for model in (BaselineRecordsAverage, LiveRecordsAverage):
        assert {
            model._meta.get_field(event_field(event_id, "average")).db_column
            for event_id in AVERAGE_EVENT_IDS
        } == set(AVERAGE_EVENT_IDS)
    assert "333mbf" not in AVERAGE_EVENT_IDS
    assert "333mbo" not in AVERAGE_EVENT_IDS
    with pytest.raises(ValueError, match="Unsupported WCA event"):
        event_field('333"; DROP TABLE records; --', "single")


@pytest.mark.django_db
def test_live_baseline_seed_is_batched_on_non_postgresql_databases():
    BaselineRecordsSingle.objects.create(
        record_holder="World", record_type="WR", event_333=900
    )
    from apps.records.classification import seed_live_records_from_baseline

    seed_live_records_from_baseline()

    assert LiveRecordsSingle.objects.get(record_holder="World", record_type="WR").event_333 == 900
