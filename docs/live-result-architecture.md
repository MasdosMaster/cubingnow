# Live-result architecture

CubingNow's production record flow is:

```text
provider payload
  -> SourceObservation (immutable raw evidence)
  -> provider diff table and finalization gate
  -> ResultObservation (current normalized provider evidence)
  -> CanonicalResult (mutable canonical head)
  -> CanonicalResultRevision (immutable classifier snapshot)
  -> ClassificationWork (one durable item per revision)
  -> incremental classifier or targeted timeline repair
  -> LiveRecordsSingle / LiveRecordsAverage
  -> ProcessedResult (revision-level read model)
  -> ProcessedResultRecordLevel (WR / CR / NR / PR)
  -> same-round and same-day adjudication
  -> `/api/records/` and the notification outbox
```

The WCA Live API, WCA Live GraphQL subscription, and CubingChina WebSocket
`RecentRecordObservation` rows remain source-specific diagnostics. They are not a
classification, API, or notification source of truth.

## Evidence, finalization, and identity

`SourceObservation` retains immutable provider frames. `WCALiveDiffTable` and
`CubingChinaDiffTable` retain the latest provider row, including attempts, while a
row is unfinished or finalized. The finalization gate emits at most one best single
and one official average `ResultObservation` per competitor and round. DNF/DNS count
as entered attempts; zero does not. A failed cutoff can finalize a single without an
average.

`CanonicalResult` is the mutable current fact. Its provider-neutral identity is WCA
competition, competitor WCA ID, event, logical round number, and result kind, with a
source-scoped fallback while that identity is unavailable. It has no queue/action
field and never represents an individual attempt.

Every classifier-relevant creation or change writes an immutable
`CanonicalResultRevision` in the same transaction. The snapshot contains all fields
needed for classification and replay, including entry/observation time, validation,
scope metadata, WCA-supplied competition timezone, and competition-local date. A
duplicate provider frame may update raw evidence timestamps but does not create a
revision or work item. Initial, corrected, retracted, and reinstated facts use the
revision actions `active`, `corrected`, `retracted`, and `active` respectively.

Stored WCA-record API snapshots remain an upstream validation source for otherwise
untrusted CubingChina evidence. A validation-status change creates another immutable
canonical revision and work item. Provider WR/CR/NR tags are audit fields only: they
neither accept nor reject a result and are never classifier input.

For a single-venue WCA competition, the venue's IANA timezone is retained. For a
multi-venue competition, discovery loads the public WCIF schedule and maps the
event/round activity to its venue timezone. A round spanning conflicting venue
timezones, a missing activity, or a failed WCIF lookup remains explicitly unresolved
rather than receiving a guessed timezone. Same-day adjudication is skipped when no
reproducible local date is available. API-polling observations reuse the resolved
stored round target when WCA Live's recent-record payload is itself ambiguous.

## Baseline and live record tables

`BaselineRecordsSingle` and `BaselineRecordsAverage` are wide tables built from the
authoritative ranking and result tables in the WCA Public Results Export v2 TSV
archive. The complete raw export is retained as text-preserving tables in the
`wca_export` PostgreSQL schema. A row in a baseline table is a
record holder/scope (`World`, continent name, country name, or WCA ID) and record
type (WR, CR, NR, or PR); fixed nullable columns hold WCA integer values for every
supported event. Single includes the complete configured event set. Average excludes
multi-blind (`333mbf` and historical `333mbo`). Code accesses columns only through
the audited event-ID mapping.

`BaselineMetadata` identifies the one active export by generation/download/rebuild
time, filename, content hash, and the competitions with actual result rows in that
export. A competition appearing only in the export's competition table is not
considered absorbed.

`LiveRecordsSingle` and `LiveRecordsAverage` have the same schemas. A baseline
refresh first copies baseline state into them, then replays current valid canonical
heads from competitions whose results are not absorbed by the active export. All
events, including encoded multi-blind, compare lower-is-better. Equality is a tie;
only a strict improvement advances a live cell. Provider WR/CR/NR tags remain audit
data and never enter this calculation.

The deployment runs `refresh_wca_public_export` every Tuesday at 16:00 UTC. Download
starts from WCA's public-export discovery API, requires a v2 export, follows only its
`tsv_url`, and streams the large ZIP to a temporary file. Every TSV member is streamed
with PostgreSQL `COPY` into `wca_export_next`; no complete table is materialized in
Python memory. Raw columns remain text so the source snapshot is preserved without
inventing an application-owned copy of the WCA schema. Required projection headers,
non-empty tables, the v2 format version, row counts, and content hash are validated.
PostgreSQL then derives the baseline tables from ranks, people, countries, continents,
and results. Renaming `wca_export_next` to `wca_export`, switching active metadata,
seeding live cells, and replaying unabsorbed live competitions share one transaction.
A failed download, load, calculation, or replay therefore preserves the previous raw
snapshot and baseline. An advisory lock also prevents overlapping refresh processes.

## Incremental classification and work ordering

`ClassificationWork` is the only classifier queue. It is unique per immutable
revision and has pending, processing, completed, failed, and stale states plus lease,
attempt, and error data. PostgreSQL `select_for_update(skip_locked)` permits workers
to classify unrelated canonical results concurrently. A claim is excluded while an
earlier revision of the same canonical result is unsettled, so revisions are always
handled in order. Ingestion only commits evidence/revision/work and never waits for
classification.

The common path reads four live cells (WR world, CR continent, NR country, and PR WCA
ID), writes one `ProcessedResult` and four `ProcessedResultRecordLevel` children, and
advances only strict improvements. Even a result with no record classification gets
a `ProcessedResult`.

Immediately before committing live cells, classification briefly locks the canonical
head and verifies that its revision still matches the immutable input. A stale
revision may receive a historical processed projection, but cannot leave live state
or publish a normal notification.

## Targeted repair

Corrections, retractions, reinstatements, and chronologically late arrivals share one
generic `repair_timeline(record_level, event, kind, scope, replay_from)` mechanism.
It starts from the appropriate export baseline, selects only current valid,
unabsorbed canonical heads in that exact world/continent/country/person timeline,
sorts by `(classification_at, canonical_result_id, canonical_revision)`, and
recomputes mathematical outcomes and the final live cell. A scope-changing correction
repairs the union of old and new WR/CR/NR/PR scopes. Repairs are atomic and never
return to an event/kind-wide production replay.

`ProcessedResult` retains every available canonical revision. A correction or
retraction marks the superseded projection invalid with a reason and replacement
link, but preserves the record outcomes it historically received. Invalid historical
revisions never advance live cells.

## Mathematical classification and adjudication

Each record level stores its mathematical outcome (`none`, `broken`, or `tied`)
separately from recognition (`recognized`, `superseded_same_round`, or
`superseded_same_day`). Same-round groups use competition, event, round, kind, and
level scope. Same-day groups use competition-local calendar date, event, kind, and
scope and can span competitions. Equal best results remain recognized shared ties.
Same-round supersession takes precedence over same-day supersession.

A legitimately recognized record broken in a later round or competition remains
recognized historical fact with `currently_holds=false`, a ceased-holding reason,
and a link to the later breaker. Re-adjudicating the entire affected round/day group
makes reinstatement after a correction or retraction emerge from current truth rather
than a special case.

## API and notifications

`/api/records/` keeps its path and now serializes `ProcessedResultRecordLevel`. It
exposes canonical/revision linkage, mathematical outcome, recognition, tie/current
holder state, ceased/superseded links, validity, timezone/local date, and the prior
display fields. The default category feed retains the existing highest verified,
recognized level per result; `include_history=true` exposes classified internal and
historical level rows. Ordinary no-record processed rows remain stored but are
omitted from the public record feed.

Notification publication selects only the highest recognized WR/CR/NR child for a
processed revision, preserving the existing no-PR policy and audience logic. Its
deduplication key contains canonical ID, revision, and selected level. Retries,
repairs, and repeated publication therefore converge on one event. Corrective
notifications are intentionally deferred; the retained invalid history supports
them later.

## Recovery and operations

`rebuild_classification_from_scratch()` is the deliberately simple correctness
oracle. It discards derived processed rows, reseeds live tables from the active
baseline, and processes available immutable revisions in deterministic
`(classification_at, canonical_result_id, revision)` order. It is exposed through:

```bash
python manage.py reclassify_live_results --suppress-notifications
```

The migration backfills exactly one immutable snapshot/work item for each existing
canonical head; it does not invent missing historical revisions or translate old
achievements/qualification decisions. `Achievement`, `QualificationDecision`,
`RecordBenchmark`, `PersonalBestBaseline`, and the old scope-dirty work model are
removed. `CanonicalResultRevision` and `ProcessedResult` have no age-based cleanup
policy.

At initial cutover, run `refresh_wca_public_export` after migrations and before
starting the revision worker. This creates the first active baseline, seeds live
state, and replays unabsorbed canonical heads. Subsequent refreshes are automatic.

The ingestion-status endpoint reports pending/processing/failed revision work. The
production blueprint runs the classification worker continuously and the WCA export
refresh cron every Tuesday at 16:00 UTC.
