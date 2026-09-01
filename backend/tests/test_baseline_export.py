import io
import json
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from apps.records import baseline_export
from apps.records.baseline_export import (
    install_baseline,
    parse_public_export,
    refresh_wca_baseline,
)
from apps.records.event_columns import AVERAGE_EVENT_IDS, SINGLE_EVENT_IDS, event_field
from apps.records.models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    LiveRecordsAverage,
    LiveRecordsSingle,
)


def export_sql_zip(*, format_version="v2.0.2", sql: str | None = None) -> bytes:
    sql = sql or """\
CREATE TABLE `continents` (
  `id` varchar(50) NOT NULL,
  `name` varchar(50) NOT NULL,
  `record_name` varchar(3) NOT NULL
);
INSERT INTO `continents` VALUES
('_Europe','Europe','ER'),
('_South America','South America','SAR');
CREATE TABLE `countries` (
  `id` varchar(50) NOT NULL,
  `name` varchar(50) NOT NULL,
  `continent_id` varchar(50) NOT NULL,
  `iso2` varchar(2) DEFAULT NULL
);
INSERT INTO `countries` VALUES
('Netherlands','Netherlands','_Europe','NL'),
('Argentina','Argentina','_South America','AR');
CREATE TABLE `persons` (
  `wca_id` varchar(10) NOT NULL,
  `sub_id` tinyint NOT NULL,
  `name` varchar(80) DEFAULT NULL,
  `country_id` varchar(50) NOT NULL,
  `gender` varchar(1) DEFAULT ''
);
INSERT INTO `persons` VALUES
('2020TEST01',1,'Test Cuber','Netherlands','m'),
('2020TEST01',2,'Test Cuber','Argentina','m'),
('2020TIEE01',1,'Tie Cuber','Netherlands','f');
CREATE TABLE `ranks_average` (
  `person_id` varchar(10) NOT NULL,
  `event_id` varchar(6) NOT NULL,
  `best` int NOT NULL,
  `world_rank` int NOT NULL,
  `continent_rank` int NOT NULL,
  `country_rank` int NOT NULL
);
INSERT INTO `ranks_average` VALUES
('2020TEST01','333',1000,1,1,1),
('2020TEST01','333mbf',970010001,1,1,1);
CREATE TABLE `ranks_single` (
  `person_id` varchar(10) NOT NULL,
  `event_id` varchar(6) NOT NULL,
  `best` int NOT NULL,
  `world_rank` int NOT NULL,
  `continent_rank` int NOT NULL,
  `country_rank` int NOT NULL
);
INSERT INTO `ranks_single` VALUES
('2020TEST01','333',900,1,1,1),
('2020TIEE01','333',900,1,1,1),
('2020TEST01','333mbf',970010001,1,1,1);
CREATE TABLE `results` (
  `id` bigint NOT NULL,
  `competition_id` varchar(32) NOT NULL,
  `event_id` varchar(6) NOT NULL,
  `round_type_id` varchar(1) NOT NULL,
  `pos` smallint NOT NULL,
  `best` int NOT NULL,
  `average` int NOT NULL,
  `person_name` varchar(80) DEFAULT NULL,
  `person_id` varchar(10) NOT NULL,
  `person_country_id` varchar(50) DEFAULT NULL,
  `format_id` varchar(1) NOT NULL,
  `regional_single_record` varchar(3) DEFAULT NULL,
  `regional_average_record` varchar(3) DEFAULT NULL
);
INSERT INTO `results` VALUES
(1,'IncludedOpen2026','333','f',1,900,1000,'Test Cuber','2020TEST01','Netherlands','a','WR','WR'),
(2,'DnfOnlyOpen2026','333','f',1,-1,-1,'Tie Cuber','2020TIEE01','Netherlands','a',NULL,NULL);
"""
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("WCA_export.sql", sql)
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


def test_v2_sql_export_builds_all_record_scopes_and_absorption_from_results():
    build = parse_public_export(export_sql_zip())
    rows = {(row["record_holder"], row["record_type"]): row for row in build.single_rows}

    assert rows[("World", "WR")]["event_333"] == 900
    assert rows[("Europe", "CR")]["event_333"] == 900
    assert rows[("Netherlands", "NR")]["event_333"] == 900
    assert rows[("2020TEST01", "PR")]["event_333"] == 900
    assert rows[("2020TIEE01", "PR")]["event_333"] == 900
    assert ("Argentina", "NR") not in rows
    assert build.absorbed_competition_ids == (
        "DnfOnlyOpen2026",
        "IncludedOpen2026",
    )
    assert not any("event_333mbf" in row for row in build.average_rows)
    assert build.export_generated_at == datetime(2026, 8, 30, 11, 7, 14, tzinfo=UTC)
    assert build.source_version.startswith("v2.0.2:")


@pytest.mark.parametrize("format_version", ["v1.0.0", "v3.0.0", "2.0.2", ""])
def test_only_v2_export_metadata_is_accepted(format_version):
    with pytest.raises(ValueError, match="v2 required"):
        parse_public_export(export_sql_zip(format_version=format_version))


def test_tsv_archive_is_rejected_even_when_its_metadata_says_v2():
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("WCA_export_results.tsv", "competition_id\nExample2026\n")
        archive.writestr(
            "metadata.json",
            json.dumps(
                {
                    "export_format_version": "v2.0.2",
                    "export_date": "2026-08-30 11:07:14 UTC",
                }
            ),
        )

    with pytest.raises(ValueError, match="Expected one SQL export member"):
        parse_public_export(target.getvalue())


def test_required_sql_schema_is_validated():
    sql = """\
CREATE TABLE `continents` (`id` varchar(50));
CREATE TABLE `countries` (`id` varchar(50));
CREATE TABLE `persons` (`wca_id` varchar(10));
CREATE TABLE `ranks_average` (`person_id` varchar(10));
CREATE TABLE `ranks_single` (`person_id` varchar(10));
CREATE TABLE `results` (`id` bigint);
INSERT INTO `results` VALUES
(1);
"""
    with pytest.raises(ValueError, match="missing required columns"):
        parse_public_export(export_sql_zip(sql=sql))


@pytest.mark.django_db
def test_refresh_discovers_and_streams_only_the_current_v2_sql_zip(monkeypatch):
    archive_bytes = export_sql_zip()
    calls = []

    class Response:
        headers = {}
        url = SimpleNamespace(path="/results/WCA_export_v2_test.sql.zip")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "export_version": "v2.0.2",
                "sql_url": "https://exports.example.test/current.sql.zip",
                "tsv_url": "https://exports.example.test/must-not-be-used.tsv.zip",
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

    monkeypatch.setattr(baseline_export.httpx, "Client", Client)
    metadata = refresh_wca_baseline(
        url="https://www.worldcubeassociation.org/api/v0/export/public"
    )

    assert ("get", "https://www.worldcubeassociation.org/api/v0/export/public") in calls
    assert (
        "stream",
        "GET",
        "https://exports.example.test/current.sql.zip",
    ) in calls
    assert not any("tsv" in str(call) for call in calls)
    assert metadata.source_filename == "WCA_export_v2_test.sql.zip"
    assert BaselineRecordsSingle.objects.get(
        record_holder="World", record_type="WR"
    ).event_333 == 900


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
def test_install_seeds_live_tables_and_identifies_one_active_export():
    build = parse_public_export(
        export_sql_zip(), downloaded_at=datetime(2026, 8, 25, tzinfo=UTC)
    )
    metadata = install_baseline(build)

    assert BaselineMetadata.objects.get(is_active=True) == metadata
    assert BaselineRecordsSingle.objects.get(
        record_holder="World", record_type="WR"
    ).event_333 == 900
    assert BaselineRecordsAverage.objects.get(
        record_holder="World", record_type="WR"
    ).event_333 == 1000
    assert LiveRecordsSingle.objects.get(
        record_holder="World", record_type="WR"
    ).event_333 == 900


@pytest.mark.django_db
def test_failed_atomic_install_preserves_previous_baseline(monkeypatch):
    BaselineRecordsSingle.objects.create(
        record_holder="World", record_type="WR", event_333=1234
    )
    build = parse_public_export(export_sql_zip())

    def fail_rebuild(*args, **kwargs):
        raise RuntimeError("replay failed")

    monkeypatch.setattr(
        baseline_export, "rebuild_live_records_after_baseline_refresh", fail_rebuild
    )
    with pytest.raises(RuntimeError, match="replay failed"):
        install_baseline(build)

    assert BaselineRecordsSingle.objects.get(
        record_holder="World", record_type="WR"
    ).event_333 == 1234
