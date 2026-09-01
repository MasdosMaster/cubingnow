# Live-result architecture

CubingNow's production flow is now:

```text
provider payload
  -> immutable SourceObservation
  -> normalized Provider Result State (may be unfinished; retains all attempts)
  -> round-finalization gate
       -> unfinished: stop here
       -> finalized: at most one single and one average ResultObservation
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

`SourceObservation` retains immutable provider frames. `WCALiveDiffTable` and
`CubingChinaDiffTable` retain the latest normalized provider row, including every
attempt, while that row is unfinished or finalized. Intermediate best and average
changes do not create facts, achievements, dirty classification work, or notifications.

`ResultObservation` is a provider's current finalized round-level claim. It keeps the
source claim, entry time, observation time, raw-frame link, final best or official
average, revision, and retraction state. One provider result can produce at most one
single observation and one average observation.

`CanonicalResult` has no record-level field and never represents an attempt. Its
natural identity is WCA competition, competitor WCA ID, event, logical round number,
and result kind. A source-scoped fallback is used while a WCA identity is missing and
is promoted or merged when the natural identity becomes available. Values are not
used as identity. Database-lockable identity scopes serialize competing claims to
the same fact. A correction updates the same observation and canonical revision.

WCA Live's result-level `enteredAt` is preserved separately from CubingNow's local
`observed_at`. CubingChina does not expose an equivalent timestamp, so its entry time
remains null.

The WCA Live API and subscription paths reconcile by finalized result identity and
kind. Identical attempt values are irrelevant because only the final best is a fact.

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

Ingestion always commits raw evidence and provider state first. A full provider
snapshot is normalized and compared with the last stored state; unchanged competitor
rows are neither reconciled nor saved again. A changed unfinished row ends there. A
newly finalized or corrected row reconciles only its final best and official average.

WCA Live completion uses the round format's expected attempt count and strict-cutoff
metadata. CubingChina completion uses round format `a` (five attempts) or `m` (three)
plus its cutoff. DNF and DNS count as entered; zero does not. A failed cutoff is final
without an average. A nonzero average alone never proves completion. A DNF average
(`-1`) is retained only after structural completion.

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
Both WebSocket collectors publish queue depth, high-water marks, frame and byte
counters, and enqueue/dequeue totals every few seconds. CubingChina publishes the
same telemetry per competition socket as well as an aggregate.

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

The attempt-level-to-finalized transition is intentionally operational rather than a
destructive data migration. Pause ingestion and classification workers, then run:

```bash
python manage.py refresh_wca_live_round_targets
python manage.py backfill_finalized_results
python manage.py backfill_finalized_results --apply
```

Migration `records.0012_retire_superseded_attempt_results` is the non-destructive
production recovery boundary. It cancels every non-terminal delivery that predates
the cutover and withdraws a legacy attempt/aggregate projection when the matching
finalized result already exists. The public projection and classifier apply the same
preference dynamically, so a partial or rolling deploy cannot show both generations.
Notification publication also resolves an old attempt-level event for the same
finalized value as historical and never creates replacement deliveries for it.

The backfill preserves raw observations, provider states, baselines, and sent
notification events; it rebuilds derived observations, canonical facts, validations,
achievements, and qualifications. Reclassification suppresses notification
publication, and pending/retry/processing deliveries tied to obsolete achievements
are cancelled. In recovery mode all other non-terminal deliveries are cancelled too,
so nothing queued before the identity rebuild can be released afterward. The legacy
nullable `attempt_number` columns remain temporarily for
rollback safety and always receive null from the final-only pipeline.

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

The read-only operations dashboard is served at `/debug`. It polls the ingestion
status endpoint, keeps a short queue history in the browser for rate and trend
visualization, and includes WebSocket, classification, notification-outbox, and
reconciliation health. It exposes summaries only: no provider payloads, Web Push
subscription data, credentials, process identifiers, or traceback bodies.
