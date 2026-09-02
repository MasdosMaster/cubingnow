"""Stream the official WCA TSV export into PostgreSQL and derive record baselines."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from time import perf_counter

import httpx
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .classification import rebuild_live_records_after_baseline_refresh
from .event_columns import AVERAGE_EVENT_IDS, EVENT_FIELD_BY_ID, SINGLE_EVENT_IDS
from .models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
)

logger = logging.getLogger(__name__)

ACTIVE_EXPORT_SCHEMA = "wca_export"
STAGING_EXPORT_SCHEMA = "wca_export_next"
OLD_EXPORT_SCHEMA = "wca_export_old"
EXPORT_LOCK_NAME = "cubingnow:wca-export-refresh"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_TRUSTED_VALUE = re.compile(r"^[a-z0-9]+$")
_TSV_PREFIXES = ("wca_export_", "wca_exports_")
_COPY_CHUNK_SIZE = 1024 * 1024
_COPY_QUOTE_BYTE = b"\x01"
_DOWNLOAD_LOG_INTERVAL_BYTES = 50 * 1024 * 1024
_REQUIRED_COLUMNS = {
    "continents": ("id", "name"),
    "countries": ("id", "name", "continent_id"),
    "persons": ("wca_id", "sub_id", "country_id"),
    "ranks_average": ("person_id", "event_id", "best"),
    "ranks_single": ("person_id", "event_id", "best"),
    "results": ("competition_id",),
}
_INDEX_COLUMNS = {
    "continents": (("id",),),
    "countries": (("id",), ("continent_id",)),
    "persons": (("wca_id", "sub_id"), ("country_id",)),
    "ranks_average": (("person_id",), ("event_id",)),
    "ranks_single": (("person_id",), ("event_id",)),
    "results": (("competition_id",),),
}


@dataclass(frozen=True)
class ExportTable:
    name: str
    member_name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ExportManifest:
    format_version: str
    export_generated_at: datetime
    downloaded_at: datetime
    source_filename: str
    source_version: str
    tables: tuple[ExportTable, ...]


def _require_v2_version(version: str) -> str:
    parts = version[1:].split(".") if version.startswith("v") else []
    if not parts or parts[0] != "2" or any(not part.isdigit() for part in parts):
        raise ValueError(f"Unsupported WCA public export format {version!r}; v2 required")
    return version


def _parse_export_date(raw_date: str, fallback: datetime) -> datetime:
    if raw_date.endswith(" UTC"):
        raw_date = f"{raw_date[:-4]}+00:00"
    generated_at = parse_datetime(raw_date)
    if generated_at is None:
        return fallback
    if generated_at.tzinfo is None:
        return generated_at.replace(tzinfo=UTC)
    return generated_at


def _table_name(member_name: str) -> str:
    stem = PurePosixPath(member_name).stem.casefold()
    for prefix in _TSV_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    if not _IDENTIFIER.fullmatch(stem):
        raise ValueError(f"Unsafe WCA TSV table name {stem!r}")
    return stem


def _header_columns(header: bytes, *, member_name: str) -> tuple[str, ...]:
    try:
        decoded = header.decode("utf-8-sig").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ValueError(f"WCA TSV header is not UTF-8: {member_name}") from exc
    columns = tuple(next(csv.reader([decoded], delimiter="\t", quotechar='"')))
    if not columns or any(not _IDENTIFIER.fullmatch(column) for column in columns):
        raise ValueError(f"Unsafe or empty WCA TSV header in {member_name}")
    if len(columns) != len(set(columns)):
        raise ValueError(f"Duplicate WCA TSV columns in {member_name}")
    return columns


def inspect_tsv_archive(
    archive: zipfile.ZipFile,
    *,
    source_filename: str,
    downloaded_at: datetime,
    content_hash: str,
) -> ExportManifest:
    metadata_members = [name for name in archive.namelist() if name.endswith("metadata.json")]
    if len(metadata_members) != 1:
        raise ValueError(f"Expected one metadata.json export member, found {len(metadata_members)}")
    with archive.open(metadata_members[0]) as source:
        payload = json.load(source)
    format_version = _require_v2_version(str(payload.get("export_format_version") or ""))
    export_generated_at = _parse_export_date(
        str(payload.get("export_date") or ""), downloaded_at
    )

    tables: list[ExportTable] = []
    seen: set[str] = set()
    for member_name in archive.namelist():
        if not member_name.casefold().endswith(".tsv"):
            continue
        table_name = _table_name(member_name)
        if table_name in seen:
            raise ValueError(f"Duplicate WCA TSV table {table_name!r}")
        with archive.open(member_name) as source:
            columns = _header_columns(source.readline(), member_name=member_name)
        seen.add(table_name)
        tables.append(ExportTable(table_name, member_name, columns))

    by_name = {table.name: table for table in tables}
    missing_tables = sorted(set(_REQUIRED_COLUMNS) - set(by_name))
    if missing_tables:
        raise ValueError("WCA TSV export is missing required tables: " + ", ".join(missing_tables))
    for table_name, required in _REQUIRED_COLUMNS.items():
        missing = sorted(set(required) - set(by_name[table_name].columns))
        if missing:
            raise ValueError(
                f"WCA TSV table {table_name!r} is missing required columns: "
                + ", ".join(missing)
            )

    return ExportManifest(
        format_version=format_version,
        export_generated_at=export_generated_at,
        downloaded_at=downloaded_at,
        source_filename=source_filename,
        source_version=f"{format_version}:{content_hash}",
        tables=tuple(sorted(tables, key=lambda table: table.name)),
    )


def _quote(identifier: str) -> str:
    return connection.ops.quote_name(identifier)


def _qualified(schema: str, table: str) -> str:
    return f"{_quote(schema)}.{_quote(table)}"


def _schema_exists(cursor, schema: str) -> bool:
    cursor.execute("SELECT to_regnamespace(%s) IS NOT NULL", [schema])
    return bool(cursor.fetchone()[0])


def _drop_schema(cursor, schema: str) -> None:
    cursor.execute(f"DROP SCHEMA IF EXISTS {_quote(schema)} CASCADE")


@contextmanager
def _export_refresh_lock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [EXPORT_LOCK_NAME])
        if not cursor.fetchone()[0]:
            raise RuntimeError("Another WCA export refresh is already running")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", [EXPORT_LOCK_NAME])


def _require_postgresql() -> None:
    if connection.vendor != "postgresql":
        raise RuntimeError("The WCA TSV loader requires PostgreSQL COPY support")


def _copy_table(cursor, archive: zipfile.ZipFile, table: ExportTable) -> int:
    qualified = _qualified(STAGING_EXPORT_SCHEMA, table.name)
    definitions = ", ".join(f"{_quote(column)} text" for column in table.columns)
    cursor.execute(f"CREATE TABLE {qualified} ({definitions})")

    column_list = ", ".join(_quote(column) for column in table.columns)
    copy_sql = (
        f"COPY {qualified} ({column_list}) FROM STDIN "
        "WITH (FORMAT CSV, DELIMITER E'\\t', HEADER FALSE, NULL 'NULL', "
        "QUOTE E'\\x01', ESCAPE E'\\x01')"
    )
    with archive.open(table.member_name) as source:
        source.readline()  # The table definition was built from this header.
        with cursor.copy(copy_sql) as copy:
            for chunk in iter(lambda: source.read(_COPY_CHUNK_SIZE), b""):
                if _COPY_QUOTE_BYTE in chunk:
                    raise ValueError(
                        f"WCA TSV table {table.name!r} contains the reserved COPY byte"
                    )
                copy.write(chunk)

    cursor.execute(f"SELECT count(*) FROM {qualified}")
    return int(cursor.fetchone()[0])


def _create_projection_indexes(cursor, manifest: ExportManifest) -> None:
    tables = {table.name: table for table in manifest.tables}
    for table_name, indexes in _INDEX_COLUMNS.items():
        started_at = perf_counter()
        created_count = 0
        logger.info("wca_export_indexes_started table=%s", table_name)
        available = set(tables[table_name].columns)
        for number, columns in enumerate(indexes, start=1):
            if not set(columns) <= available:
                continue
            index_name = f"wca_{table_name}_{number}_idx"
            column_list = ", ".join(_quote(column) for column in columns)
            cursor.execute(
                f"CREATE INDEX {_quote(index_name)} ON "
                f"{_qualified(STAGING_EXPORT_SCHEMA, table_name)} ({column_list})"
            )
            created_count += 1
        cursor.execute(f"ANALYZE {_qualified(STAGING_EXPORT_SCHEMA, table_name)}")
        logger.info(
            "wca_export_indexes_completed table=%s index_count=%s duration_seconds=%.3f",
            table_name,
            created_count,
            perf_counter() - started_at,
        )


def load_tsv_archive_into_staging(
    archive: zipfile.ZipFile, manifest: ExportManifest
) -> dict[str, int]:
    """COPY every TSV member into a raw, text-preserving staging schema."""

    row_counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        logger.info("wca_export_staging_schema_started schema=%s", STAGING_EXPORT_SCHEMA)
        _drop_schema(cursor, STAGING_EXPORT_SCHEMA)
        cursor.execute(f"CREATE SCHEMA {_quote(STAGING_EXPORT_SCHEMA)}")
        for table in manifest.tables:
            started_at = perf_counter()
            member_size = archive.getinfo(table.member_name).file_size
            logger.info(
                "wca_export_table_copy_started table=%s uncompressed_bytes=%s",
                table.name,
                member_size,
            )
            row_count = _copy_table(cursor, archive, table)
            row_counts[table.name] = row_count
            logger.info(
                "wca_export_table_copy_completed table=%s rows=%s duration_seconds=%.3f",
                table.name,
                row_count,
                perf_counter() - started_at,
            )
        for required_table in _REQUIRED_COLUMNS:
            if row_counts[required_table] == 0:
                raise ValueError(f"Required WCA TSV table {required_table!r} is empty")
        logger.info("wca_export_indexing_started")
        _create_projection_indexes(cursor, manifest)
        logger.info("wca_export_indexing_completed")
        cursor.execute(
            f"CREATE TABLE {_qualified(STAGING_EXPORT_SCHEMA, '_metadata')} ("
            "format_version text NOT NULL, export_generated_at timestamptz NOT NULL, "
            "downloaded_at timestamptz NOT NULL, source_filename text NOT NULL, "
            "source_version text NOT NULL, loaded_at timestamptz NOT NULL, "
            "table_row_counts jsonb NOT NULL)"
        )
        cursor.execute(
            f"INSERT INTO {_qualified(STAGING_EXPORT_SCHEMA, '_metadata')} "
            "(format_version, export_generated_at, downloaded_at, source_filename, "
            "source_version, loaded_at, table_row_counts) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                manifest.format_version,
                manifest.export_generated_at,
                manifest.downloaded_at,
                manifest.source_filename,
                manifest.source_version,
                timezone.now(),
                json.dumps(row_counts, sort_keys=True),
            ],
        )
        logger.info(
            "wca_export_staging_schema_completed schema=%s table_count=%s total_rows=%s",
            STAGING_EXPORT_SCHEMA,
            len(row_counts),
            sum(row_counts.values()),
        )
    return row_counts


def _trusted_literal(value: str) -> str:
    if not _TRUSTED_VALUE.fullmatch(value):
        raise ValueError(f"Unsafe trusted SQL value {value!r}")
    return f"'{value}'"


def baseline_projection_sql(model, rank_table: str, event_ids: tuple[str, ...]) -> str:
    """Return the fixed, audited SQL projection from raw WCA ranks to a wide table."""

    target = _quote(model._meta.db_table)
    event_columns = [model._meta.get_field(EVENT_FIELD_BY_ID[event_id]).column for event_id in event_ids]
    insert_columns = ", ".join(
        [_quote("record_holder"), _quote("record_type")]
        + [_quote(column) for column in event_columns]
    )
    aggregates = ",\n        ".join(
        f"MIN(best_value) FILTER (WHERE event_id = {_trusted_literal(event_id)})::integer "
        f"AS {_quote(column)}"
        for event_id, column in zip(event_ids, event_columns, strict=True)
    )
    allowed = ", ".join(_trusted_literal(event_id) for event_id in event_ids)

    return f"""
WITH primary_people AS (
    SELECT upper(p.wca_id) AS person_id,
           COALESCE(NULLIF(c.name, ''), c.id, '') AS country_name,
           COALESCE(NULLIF(continent.name, ''), ltrim(c.continent_id, '_'), '') AS continent_name
      FROM {_qualified(ACTIVE_EXPORT_SCHEMA, 'persons')} p
 LEFT JOIN {_qualified(ACTIVE_EXPORT_SCHEMA, 'countries')} c ON c.id = p.country_id
 LEFT JOIN {_qualified(ACTIVE_EXPORT_SCHEMA, 'continents')} continent
        ON continent.id = c.continent_id
     WHERE p.sub_id = '1'
),
parsed_ranks AS (
    SELECT upper(r.person_id) AS person_id,
           r.event_id,
           CASE WHEN r.best ~ '^[0-9]+$' THEN r.best::bigint END AS best_value,
           COALESCE(person.country_name, '') AS country_name,
           COALESCE(person.continent_name, '') AS continent_name
      FROM {_qualified(ACTIVE_EXPORT_SCHEMA, rank_table)} r
 LEFT JOIN primary_people person ON person.person_id = upper(r.person_id)
     WHERE r.event_id IN ({allowed})
),
valid_ranks AS (
    SELECT * FROM parsed_ranks WHERE best_value > 0 AND best_value <= 2147483647
),
record_scopes AS (
    SELECT 'World'::text AS record_holder, 'WR'::text AS record_type, event_id, best_value
      FROM valid_ranks
    UNION ALL
    SELECT continent_name, 'CR', event_id, best_value
      FROM valid_ranks WHERE continent_name <> ''
    UNION ALL
    SELECT country_name, 'NR', event_id, best_value
      FROM valid_ranks WHERE country_name <> ''
    UNION ALL
    SELECT person_id, 'PR', event_id, best_value
      FROM valid_ranks WHERE person_id <> ''
)
INSERT INTO {target} ({insert_columns})
SELECT record_holder,
       record_type,
       {aggregates}
  FROM record_scopes
 GROUP BY record_holder, record_type
"""


def _absorbed_competition_ids(cursor) -> list[str]:
    cursor.execute(
        f"SELECT COALESCE(jsonb_agg(competition_id ORDER BY competition_id), '[]'::jsonb) "
        f"FROM (SELECT DISTINCT competition_id "
        f"FROM {_qualified(ACTIVE_EXPORT_SCHEMA, 'results')} "
        "WHERE competition_id IS NOT NULL AND competition_id <> '') ids"
    )
    value = cursor.fetchone()[0]
    return json.loads(value) if isinstance(value, str) else list(value)


def activate_staged_export(manifest: ExportManifest):
    """Atomically swap schemas, replace baselines, and rebuild live projections."""

    activation_started_at = perf_counter()
    logger.info("wca_export_activation_started source_version=%s", manifest.source_version)
    with connection.cursor() as cursor:
        _drop_schema(cursor, OLD_EXPORT_SCHEMA)

    with transaction.atomic():
        with connection.cursor() as cursor:
            if not _schema_exists(cursor, STAGING_EXPORT_SCHEMA):
                raise RuntimeError("The staged WCA export schema is missing")
            if _schema_exists(cursor, ACTIVE_EXPORT_SCHEMA):
                cursor.execute(
                    f"ALTER SCHEMA {_quote(ACTIVE_EXPORT_SCHEMA)} RENAME TO {_quote(OLD_EXPORT_SCHEMA)}"
                )
            cursor.execute(
                f"ALTER SCHEMA {_quote(STAGING_EXPORT_SCHEMA)} RENAME TO {_quote(ACTIVE_EXPORT_SCHEMA)}"
            )

            cursor.execute(f"DELETE FROM {_quote(BaselineRecordsSingle._meta.db_table)}")
            cursor.execute(
                baseline_projection_sql(BaselineRecordsSingle, "ranks_single", SINGLE_EVENT_IDS)
            )
            single_count = cursor.rowcount
            logger.info(
                "wca_export_baseline_projection_completed kind=single rows=%s",
                single_count,
            )
            cursor.execute(f"DELETE FROM {_quote(BaselineRecordsAverage._meta.db_table)}")
            cursor.execute(
                baseline_projection_sql(
                    BaselineRecordsAverage, "ranks_average", AVERAGE_EVENT_IDS
                )
            )
            average_count = cursor.rowcount
            logger.info(
                "wca_export_baseline_projection_completed kind=average rows=%s",
                average_count,
            )
            logger.info("wca_export_absorbed_competitions_started")
            absorbed_competition_ids = _absorbed_competition_ids(cursor)
            logger.info(
                "wca_export_absorbed_competitions_completed count=%s",
                len(absorbed_competition_ids),
            )

        BaselineMetadata.objects.filter(is_active=True).update(is_active=False)
        metadata = BaselineMetadata.objects.create(
            export_generated_at=manifest.export_generated_at,
            downloaded_at=manifest.downloaded_at,
            source_filename=manifest.source_filename,
            source_version=manifest.source_version,
            rebuilt_at=timezone.now(),
            absorbed_competition_ids=absorbed_competition_ids,
            is_active=True,
        )
        logger.info("wca_export_live_projection_rebuild_started")
        rebuild_live_records_after_baseline_refresh()
        logger.info("wca_export_live_projection_rebuild_completed")

        with connection.cursor() as cursor:
            _drop_schema(cursor, OLD_EXPORT_SCHEMA)
    logger.info(
        "wca_export_activation_completed source_version=%s duration_seconds=%.3f",
        manifest.source_version,
        perf_counter() - activation_started_at,
    )
    return metadata


def _download_export(target, *, url: str, timeout: float) -> tuple[str, datetime, str]:
    downloaded_at = timezone.now()
    digest = hashlib.sha256()
    started_at = perf_counter()
    downloaded_bytes = 0
    next_progress_log = _DOWNLOAD_LOG_INTERVAL_BYTES
    logger.info("wca_export_download_discovery_started url=%s", url)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        archive_url = url
        if "/api/" in url:
            discovery = client.get(url)
            discovery.raise_for_status()
            payload = discovery.json()
            _require_v2_version(str(payload.get("export_version") or ""))
            archive_url = str(payload.get("tsv_url") or "")
            if not archive_url:
                raise ValueError("WCA export discovery response did not contain tsv_url")
        logger.info("wca_export_download_started archive_url=%s", archive_url)
        with client.stream("GET", archive_url) as response:
            response.raise_for_status()
            expected_bytes = int(response.headers.get("content-length") or 0)
            filename = response.url.path.rsplit("/", 1)[-1] or "WCA_export.tsv.zip"
            disposition = response.headers.get("content-disposition", "")
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip().strip('"')
            if response.headers.get("last-modified"):
                downloaded_at = parsedate_to_datetime(response.headers["last-modified"])
            for chunk in response.iter_bytes():
                digest.update(chunk)
                target.write(chunk)
                downloaded_bytes += len(chunk)
                if downloaded_bytes >= next_progress_log:
                    logger.info(
                        "wca_export_download_progress downloaded_bytes=%s expected_bytes=%s",
                        downloaded_bytes,
                        expected_bytes,
                    )
                    next_progress_log = downloaded_bytes + _DOWNLOAD_LOG_INTERVAL_BYTES
    target.flush()
    logger.info(
        "wca_export_download_completed filename=%s downloaded_bytes=%s duration_seconds=%.3f",
        filename,
        downloaded_bytes,
        perf_counter() - started_at,
    )
    return filename, downloaded_at, digest.hexdigest()


def refresh_wca_baseline(*, url: str | None = None, timeout: float = 120.0):
    """Load the full official TSV snapshot and atomically refresh derived baselines."""

    _require_postgresql()
    url = url or settings.WCA_PUBLIC_EXPORT_URL
    refresh_started_at = perf_counter()
    logger.info("wca_export_refresh_started url=%s", url)
    with tempfile.NamedTemporaryFile(suffix=".tsv.zip") as target:
        filename, downloaded_at, content_hash = _download_export(
            target, url=url, timeout=timeout
        )
        logger.info("wca_export_archive_inspection_started filename=%s", filename)
        with zipfile.ZipFile(target.name) as archive:
            manifest = inspect_tsv_archive(
                archive,
                source_filename=filename,
                downloaded_at=downloaded_at,
                content_hash=content_hash,
            )
            logger.info(
                "wca_export_archive_inspection_completed format_version=%s table_count=%s",
                manifest.format_version,
                len(manifest.tables),
            )
            with _export_refresh_lock():
                try:
                    load_tsv_archive_into_staging(archive, manifest)
                    metadata = activate_staged_export(manifest)
                except Exception:
                    logger.exception("wca_export_refresh_failed cleaning_staging_schema=true")
                    with connection.cursor() as cursor:
                        _drop_schema(cursor, STAGING_EXPORT_SCHEMA)
                    raise
    logger.info(
        "wca_export_refresh_completed source_version=%s duration_seconds=%.3f",
        metadata.source_version,
        perf_counter() - refresh_started_at,
    )
    return metadata
