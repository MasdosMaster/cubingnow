# Live-result architecture

CubingNow's production flow is now:

```text
provider payload
  -> immutable SourceObservation
  -> provider normalization / snapshot diff
  -> ResultObservation
  -> fact reconciliation -> CanonicalResult
  -> mark affected event/kind scope dirty (once per ingestion transaction)
  -> durable classification worker
       -> stored WCA validation, in bulk
       -> deterministic benchmark replay, in memory
       -> bulk database diff
  -> Achievement (WR / CR / NR / PR)
  -> QualificationDecision
       -> public /api/records/ projection
       -> notification outbox and provider adapter
```

The WCA Live API, WCA Live GraphQL subscription, and CubingChina WebSocket
`RecentRecordObservation` rows remain source-specific debug projections. They are not
the public or notification source of truth.

## Evidence and identity

`SourceObservation` retains immutable provider frames. `ResultObservation` is the
current normalized source slot and keeps source claim, entry time, observation time,
raw-frame link, value, attempt position, revision, and retraction state.

`CanonicalResult` has no record-level field. Its identity uses WCA competition,
competitor, event, round number, result kind, and attempt position when those fields
are available. Database-lockable identity scopes serialize genuinely competing
claims to the same result identity. Classification does not hold this lock while
ingestion is receiving other results. A value correction updates the same source
slot and canonical result revision.

WCA Live's result-level `enteredAt` is preserved separately from CubingNow's local
`observed_at`. CubingChina does not expose an equivalent timestamp, so its entry time
remains null.

The WCA Live recent-record endpoint does not expose an attempt number. Reconciliation
therefore first uses the shared WCA Live result ID, then the provider-neutral natural
scope and value. If identical values make the attempt intrinsically ambiguous, the
source evidence stays inspectable and the match is deterministic; a future richer
upstream ID can strengthen this without changing downstream classification.

## Trust, classification, and policy

WCA Live API and subscription record tags are trusted evidence. CubingChina record
tags are always retained but never treated as authoritative. A CubingChina value can
still be mathematically classified from an explicit `RecordBenchmark` even when its
tag is absent or wrong.

Source-only CubingChina results remain `pending / untrusted_source_only`. Their
mathematical achievements are retained internally, but qualification hides them from
the homepage and blocks notifications until trusted evidence confirms the result.
Trusted WCA source disagreements move the canonical result to `rejected` and withdraw
active qualifications.

Classification replays results chronologically over historical baselines instead of
mutating a singleton "current record" value. Corrections and retractions therefore
recompute later results correctly. `PersonalBestBaseline` uses the same replay model
for PR progression.

A result owns any number of `Achievement` rows. Homepage precedence is WR, CR, NR,
then PR; lower achievements remain stored. `QualificationDecision` separately records
homepage and notification eligibility and the reason for each decision.

## Fast ingestion and batched classification

Ingestion commits facts only. A full provider snapshot is normalized and compared
with the last stored state; unchanged competitor rows are neither reconciled nor
saved again. Changed state rows are persisted with one upsert. Attempts remain
separate canonical facts because two record-breaking attempts by one competitor are
distinct results, but they no longer trigger separate classification passes.

Each ingestion transaction collects the affected `(event_id, kind)` pairs and
increments each pair's durable `ClassificationScopeWork` version once. The first
request receives a short debounce; later arrivals advance the version without
continually postponing the work. Claims have leases, so a process crash makes the
scope reclaimable. If facts arrive during a classification pass, the version check
rolls that pass back and immediately retries the newer version. Stale classification
and notification state is therefore not published.

The classification worker loads a scope with a fixed number of queries, computes all
desired achievements and qualification decisions in memory, and persists creates,
updates, withdrawals, and policy decisions with bulk operations. Official WCA record
validation reads the latest stored `WCARecordSnapshot` and is also applied in bulk;
there is no external REST request per incoming result. The external records endpoint
is refreshed by its independent periodic process.

The WebSocket message queue remains unbounded. Its size is exposed in worker
diagnostics so throughput can be measured directly; the design relies on processing
faster than sustained arrival rather than discarding or coalescing provider frames.

## Notification idempotency

Only newly policy-approved canonical achievements enter notification publication. The
notification event key is based on canonical result identity plus achievement type,
and delivery uniqueness remains `(event, endpoint)`. API/WS overlap, repeated
snapshots, reconnects, transaction retries, and worker retries therefore converge on
one event and one intended delivery.

## Operations and deferred work

Record and personal-best baselines can be maintained in Django admin. After changing
them, run:

```bash
python manage.py reclassify_live_results
```

Deferred work is deliberately limited to external-data and product concerns:

- automated official WR/CR/NR and competitor PB baseline synchronization;
- WCA OAuth identity, followed cubers, and account/plan entitlement rules;
- PR notification preferences (PR classification and homepage projection exist);
- correction-notification product behavior for already-sent alerts;
- a polished disagreement/quarantine UI beyond the retained data and admin views;
- removal of the unused legacy `Result`/`Record` tables after the canonical backfill
  has been observed in production and rollback is no longer needed.

The production blueprint runs `run_classification_worker` as its own worker. The
ingestion-status endpoint exposes pending/claimed/failed scope counts, oldest fact
lag, and recent classification duration. `ClassificationScopeWork` is also visible
in Django admin for diagnosis and recovery.
