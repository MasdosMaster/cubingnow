"""Build baseline/live record tables from the WCA v2 SQL export."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import BinaryIO

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .classification import rebuild_live_records_after_baseline_refresh
from .event_columns import AVERAGE_EVENT_IDS, EVENT_FIELD_BY_ID, SINGLE_EVENT_IDS
from .models import (
    BaselineMetadata,
    BaselineRecordsAverage,
    BaselineRecordsSingle,
    RecordLevel,
)


@dataclass(frozen=True)
class BaselineBuild:
    single_rows: tuple[dict, ...]
    average_rows: tuple[dict, ...]
    absorbed_competition_ids: tuple[str, ...]
    export_generated_at: datetime
    source_filename: str
    source_version: str


_SQL_REQUIRED_COLUMNS = {
    "continents": ("id", "name"),
    "countries": ("id", "name", "continent_id"),
    "persons": ("wca_id", "sub_id", "country_id"),
    "ranks_average": ("person_id", "event_id", "best"),
    "ranks_single": ("person_id", "event_id", "best"),
    "results": ("competition_id",),
}

_MYSQL_ESCAPES = {
    ord("0"): b"\0",
    ord("b"): b"\b",
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("Z"): b"\x1a",
}


def _require_v2_version(version: str) -> str:
    parts = version[1:].split(".") if version.startswith("v") else []
    if not parts or parts[0] != "2" or any(not part.isdigit() for part in parts):
        raise ValueError(f"Unsupported WCA public export format {version!r}; v2 required")
    return version


def _metadata(archive: zipfile.ZipFile, fallback: datetime) -> tuple[str, datetime]:
    names = [name for name in archive.namelist() if name.endswith("metadata.json")]
    if len(names) != 1:
        raise ValueError(f"Expected one metadata.json export member, found {len(names)}")
    with archive.open(names[0]) as source:
        payload = json.load(source)

    version = _require_v2_version(str(payload.get("export_format_version") or ""))
    raw_date = str(payload.get("export_date") or "")
    if raw_date.endswith(" UTC"):
        raw_date = f"{raw_date[:-4]}+00:00"
    generated_at = parse_datetime(raw_date)
    if generated_at is None:
        generated_at = fallback
    elif generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    return version, generated_at


def _sql_member_name(archive: zipfile.ZipFile) -> str:
    names = [name for name in archive.namelist() if name.casefold().endswith(".sql")]
    if len(names) != 1:
        raise ValueError(f"Expected one SQL export member, found {len(names)}")
    return names[0]


def _identifier_after(raw: bytes, prefix: bytes) -> str | None:
    if not raw.startswith(prefix):
        return None
    end = raw.find(b"`", len(prefix))
    if end < 0:
        raise ValueError("Malformed identifier in WCA SQL export")
    return raw[len(prefix) : end].decode("ascii")


def _quoted_value_end(raw: bytes, start: int) -> int:
    end = raw.find(b"'", start)
    while end >= 0:
        slash_count = 0
        cursor = end - 1
        while cursor >= start and raw[cursor] == 92:
            slash_count += 1
            cursor -= 1
        if slash_count % 2 == 0:
            return end
        end = raw.find(b"'", end + 1)
    raise ValueError("Unterminated string in WCA SQL export")


def _decode_mysql_string(raw: bytes) -> str:
    if b"\\" not in raw:
        return raw.decode("utf-8")
    decoded = bytearray()
    cursor = 0
    while cursor < len(raw):
        character = raw[cursor]
        if character != 92:
            decoded.append(character)
            cursor += 1
            continue
        cursor += 1
        if cursor >= len(raw):
            raise ValueError("Truncated escape in WCA SQL export")
        escaped = raw[cursor]
        decoded.extend(_MYSQL_ESCAPES.get(escaped, bytes((escaped,))))
        cursor += 1
    return decoded.decode("utf-8")


def _parse_sql_row(
    raw: bytes, selections: tuple[tuple[int, str], ...]
) -> dict[str, str | None]:
    """Parse selected fields from one MariaDB VALUES tuple without executing SQL."""

    if not raw.startswith(b"(") or not selections:
        raise ValueError("Malformed VALUES row in WCA SQL export")
    last_index = selections[-1][0]
    selection_index = 0
    cursor = 1
    field_index = 0
    values: dict[str, str | None] = {}

    while field_index <= last_index:
        while cursor < len(raw) and raw[cursor] in b" \t":
            cursor += 1
        if cursor >= len(raw):
            raise ValueError("Truncated VALUES row in WCA SQL export")

        column = (
            selections[selection_index][1]
            if selections[selection_index][0] == field_index
            else None
        )
        value: str | None = None
        if raw[cursor] == 39:
            start = cursor + 1
            end = _quoted_value_end(raw, start)
            if column is not None:
                value = _decode_mysql_string(raw[start:end])
            cursor = end + 1
        else:
            start = cursor
            comma = raw.find(b",", start)
            closing = raw.find(b")", start)
            candidates = [position for position in (comma, closing) if position >= 0]
            if not candidates:
                raise ValueError("Truncated VALUES row in WCA SQL export")
            cursor = min(candidates)
            if column is not None:
                token = raw[start:cursor].strip()
                value = None if token == b"NULL" else token.decode("ascii")

        if column is not None:
            values[column] = value
            selection_index += 1
        if field_index == last_index:
            break
        while cursor < len(raw) and raw[cursor] in b" \t":
            cursor += 1
        if cursor >= len(raw) or raw[cursor] != ord(","):
            raise ValueError("Malformed field separator in WCA SQL export")
        cursor += 1
        field_index += 1

    return values


def _sql_rows(source: BinaryIO) -> Iterator[tuple[str, dict[str, str | None]]]:
    schemas: dict[str, list[str]] = {}
    creating: str | None = None
    insert: tuple[str, tuple[tuple[int, str], ...]] | None = None

    for raw in source:
        if insert is not None and raw.startswith(b"("):
            table, selections = insert
            yield table, _parse_sql_row(raw, selections)
            continue

        table = _identifier_after(raw, b"CREATE TABLE `")
        if table is not None:
            creating = table if table in _SQL_REQUIRED_COLUMNS else None
            insert = None
            if creating is not None:
                schemas[creating] = []
            continue

        if creating is not None and raw.startswith(b"  `"):
            column = _identifier_after(raw, b"  `")
            if column is not None:
                schemas[creating].append(column)
            continue

        table = _identifier_after(raw, b"INSERT INTO `")
        if table is not None:
            creating = None
            insert = None
            required = _SQL_REQUIRED_COLUMNS.get(table)
            if required is None:
                continue
            columns = schemas.get(table)
            if columns is None:
                raise ValueError(f"Missing schema for WCA SQL table {table!r}")
            missing = [column for column in required if column not in columns]
            if missing:
                raise ValueError(
                    f"WCA SQL table {table!r} is missing required columns: "
                    f"{', '.join(missing)}"
                )
            insert = (
                table,
                tuple(sorted((columns.index(column), column) for column in required)),
            )
            continue

    missing_tables = sorted(set(_SQL_REQUIRED_COLUMNS) - set(schemas))
    if missing_tables:
        raise ValueError(
            "WCA SQL export is missing required tables: " + ", ".join(missing_tables)
        )


def _parse_public_export_archive(
    archive: zipfile.ZipFile,
    *,
    source_filename: str,
    downloaded_at: datetime,
    content_hash: str,
) -> BaselineBuild:
    format_version, export_generated_at = _metadata(archive, downloaded_at)
    continents: dict[str, str] = {}
    countries: dict[str, tuple[str, str]] = {}
    people: dict[str, str] = {}
    absorbed: set[str] = set()
    single_values: dict[tuple[str, str], dict] = {}
    average_values: dict[tuple[str, str], dict] = {}

    def assign(
        values: dict[tuple[str, str], dict],
        holder: str,
        level: str,
        event_id: str,
        value: int,
    ) -> None:
        if not holder:
            return
        target = values.setdefault(
            (holder, level),
            {"record_holder": holder, "record_type": level},
        )
        field = EVENT_FIELD_BY_ID[event_id]
        incumbent = target.get(field)
        if incumbent is None or value < incumbent:
            target[field] = value

    with archive.open(_sql_member_name(archive)) as source:
        for table, row in _sql_rows(source):
            if table == "continents":
                continent_id = row["id"] or ""
                if continent_id:
                    continents[continent_id] = row["name"] or continent_id.lstrip("_")
                continue
            if table == "countries":
                country_id = row["id"] or ""
                if country_id:
                    countries[country_id] = (
                        row["name"] or country_id,
                        row["continent_id"] or "",
                    )
                continue
            if table == "persons":
                person_id = row["wca_id"] or ""
                if person_id and row["sub_id"] == "1":
                    people[person_id] = row["country_id"] or ""
                continue
            if table == "results":
                competition_id = row["competition_id"] or ""
                if competition_id:
                    absorbed.add(competition_id)
                continue

            event_id = row["event_id"] or ""
            allowed_events = (
                SINGLE_EVENT_IDS if table == "ranks_single" else AVERAGE_EVENT_IDS
            )
            try:
                value = int(row["best"] or 0)
            except ValueError:
                continue
            if event_id not in allowed_events or value <= 0:
                continue

            person_id = (row["person_id"] or "").upper()
            country_id = people.get(person_id, "")
            country_name, continent_id = countries.get(country_id, (country_id, ""))
            continent_name = continents.get(continent_id, continent_id.lstrip("_"))
            values = single_values if table == "ranks_single" else average_values
            assign(values, "World", RecordLevel.WORLD, event_id, value)
            assign(values, continent_name, RecordLevel.CONTINENTAL, event_id, value)
            assign(values, country_name, RecordLevel.NATIONAL, event_id, value)
            assign(values, person_id, RecordLevel.PERSONAL, event_id, value)

    return BaselineBuild(
        single_rows=tuple(single_values[key] for key in sorted(single_values)),
        average_rows=tuple(average_values[key] for key in sorted(average_values)),
        absorbed_competition_ids=tuple(sorted(absorbed)),
        export_generated_at=export_generated_at,
        source_filename=source_filename,
        source_version=f"{format_version}:{content_hash}",
    )


def parse_public_export(
    archive_bytes: bytes,
    *,
    source_filename: str = "WCA_export.sql.zip",
    downloaded_at: datetime | None = None,
) -> BaselineBuild:
    """Validate and aggregate an official WCA v2 SQL ZIP."""

    downloaded_at = downloaded_at or timezone.now()
    content_hash = hashlib.sha256(archive_bytes).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Downloaded WCA public export is not a valid ZIP archive") from exc
    with archive:
        return _parse_public_export_archive(
            archive,
            source_filename=source_filename,
            downloaded_at=downloaded_at,
            content_hash=content_hash,
        )


def parse_public_export_file(
    archive_path: str,
    *,
    source_filename: str,
    downloaded_at: datetime | None = None,
) -> BaselineBuild:
    downloaded_at = downloaded_at or timezone.now()
    digest = hashlib.sha256()
    with open(archive_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("Downloaded WCA public export is not a valid ZIP archive") from exc
    with archive:
        return _parse_public_export_archive(
            archive,
            source_filename=source_filename,
            downloaded_at=downloaded_at,
            content_hash=digest.hexdigest(),
        )


@transaction.atomic
def install_baseline(build: BaselineBuild, *, downloaded_at: datetime | None = None):
    """Atomically replace baseline/live state; any replay failure rolls it all back."""

    downloaded_at = downloaded_at or timezone.now()
    BaselineRecordsSingle.objects.all().delete()
    BaselineRecordsAverage.objects.all().delete()
    for rows, model in (
        (build.single_rows, BaselineRecordsSingle),
        (build.average_rows, BaselineRecordsAverage),
    ):
        for offset in range(0, len(rows), 5000):
            model.objects.bulk_create(
                [model(**row) for row in rows[offset : offset + 5000]],
                batch_size=1000,
            )
    BaselineMetadata.objects.filter(is_active=True).update(is_active=False)
    metadata = BaselineMetadata.objects.create(
        export_generated_at=build.export_generated_at,
        downloaded_at=downloaded_at,
        source_filename=build.source_filename,
        source_version=build.source_version,
        rebuilt_at=timezone.now(),
        absorbed_competition_ids=list(build.absorbed_competition_ids),
        is_active=True,
    )
    rebuild_live_records_after_baseline_refresh()
    return metadata


def refresh_wca_baseline(*, url: str | None = None, timeout: float = 120.0):
    """Download, validate, and atomically install the latest WCA v2 SQL export."""

    url = url or settings.WCA_PUBLIC_EXPORT_URL
    downloaded_at = timezone.now()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        archive_url = url
        if "/api/" in url:
            discovery = client.get(url)
            discovery.raise_for_status()
            payload = discovery.json()
            _require_v2_version(str(payload.get("export_version") or ""))
            archive_url = payload.get("sql_url") or ""
            if not archive_url:
                raise ValueError("WCA export discovery response did not contain sql_url")
        with client.stream("GET", archive_url) as response:
            response.raise_for_status()
            filename = response.url.path.rsplit("/", 1)[-1] or "WCA_export.sql.zip"
            disposition = response.headers.get("content-disposition", "")
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip().strip('"')
            export_downloaded_at = (
                parsedate_to_datetime(response.headers["last-modified"])
                if response.headers.get("last-modified")
                else downloaded_at
            )
            with tempfile.NamedTemporaryFile(suffix=".sql.zip") as target:
                for chunk in response.iter_bytes():
                    target.write(chunk)
                target.flush()
                build = parse_public_export_file(
                    target.name,
                    source_filename=filename,
                    downloaded_at=export_downloaded_at,
                )
    return install_baseline(build, downloaded_at=downloaded_at)
