# Weekend record verification

CubingNow runs two intentionally independent observation pipelines:

1. `api_polling` executes WCA Live's `recentRecords` GraphQL query.
2. `graphql_subscription` discovers relevant rounds and diffs the full state delivered by
   WCA Live's `roundUpdated(id: ID!)` subscription.

They share only pure normalization, result-value formatting/comparison, and display utilities.
`RecentRecordObservation` uniqueness includes `ingestion_method`, and raw `SourceObservation`
deduplication is also source-specific. Neither worker reads the other worker's observations.

## Database migration

The `records.0002_record_ingestion_experiment` migration adds:

- `RecentRecordObservation` for normalized, pipeline-specific record observations;
- `SubscriptionRound` for discovered targets and per-round subscription status;
- `SubscriptionResultState` for restart-safe normalized snapshot baselines;
- `IngestionWorkerStatus` for worker health and timestamps;
- an `ingestion_method` dimension on raw source observations and new ingestion-run modes.

Run locally:

```bash
cd backend
uv run python manage.py migrate
```

Or with Docker:

```bash
docker compose run --rm backend python manage.py migrate
```

## Configuration

All defaults live in `config/settings/base.py` and can be overridden with environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WCA_LIVE_API_URL` | `https://live.worldcubeassociation.org/api` | GraphQL HTTP endpoint |
| `WCA_LIVE_WS_URL` | `wss://live.worldcubeassociation.org/socket/websocket` | Phoenix WebSocket endpoint |
| `WCA_WEEKEND_START` | empty | Optional fixed inclusive start-date override |
| `WCA_WEEKEND_END` | empty | Optional fixed inclusive end-date override |
| `WCA_WEEKEND_TIME_ZONE` | `Europe/Amsterdam` | Time zone used for the rolling Wednesday-through-Tuesday window |
| `WCA_COMPETITION_LOOKBACK_DAYS` | `7` | Days subtracted from the start for discovery |
| `WCA_API_POLL_INTERVAL_SECONDS` | `60` | API polling interval |
| `WCA_ROUND_DISCOVERY_INTERVAL_SECONDS` | `900` | Subscription rediscovery interval |
| `WCA_SUBSCRIPTION_CATCHUP_MINUTES` | `60` | First-snapshot record catch-up window |
| `WCA_RETRY_BASE_SECONDS` | `1` | Retry backoff base |
| `WCA_RETRY_MAX_SECONDS` | `60` | Retry backoff ceiling |
| `WCA_RETRY_MAX_ATTEMPTS` | `5` | Attempts within one API poll cycle |
| `CUBINGNOW_LOG_LEVEL` | `INFO` | Worker integration log level |

With no date overrides, the subscription worker calculates the current Wednesday-through-Tuesday
window when it starts. At the end of Tuesday the process exits normally; Render restarts it and
the next process calculates the new window. `WCA_WEEKEND_START` and `WCA_WEEKEND_END` must be set
together when a fixed window is required.

The subscription command's `--start`, `--end`, `--lookback-days`, `--discovery-interval`, and
`--catchup-minutes` arguments override these settings for one worker invocation.

Competition overlap is inclusive:

```text
competition.startDate <= weekend_end
and competition.endDate >= weekend_start
```

WCA Live exposes `from` but no `to`, cursor, or offset on `competitions`. CubingNow therefore
logs `weekend_start - lookback_days`, fetches the complete basic competition list by omitting
`limit`, filters overlap locally, and then fetches events/rounds for each surviving competition.
The two-stage fetch is necessary because the equivalent all-in-one query exceeds WCA Live's
deployed GraphQL complexity limit of 5000.

During the final live validation on 2026-08-05, this configuration returned 64 competitions from
the lookback query, 16 overlapping competitions, and 208 unique rounds, with no detail-query
failures. Treat these as a pre-weekend reference only: periodic rediscovery may legitimately raise
the counts as WCA Live imports more competitions or rounds.

## Start the services

Everything with Docker:

```bash
docker compose up --build database backend frontend api-poller subscription-worker
```

Individual native processes (with PostgreSQL already available):

```bash
# Backend
cd backend
uv run python manage.py migrate
uv run python manage.py runserver

# Frontend, in another terminal
cd frontend
npm install
npm run dev

# API polling worker, in another terminal
cd backend
uv run python manage.py run_wca_live_api_polling

# Subscription worker, in another terminal
cd backend
uv run python manage.py run_wca_live_subscriptions \
  --start 2026-08-06 --end 2026-08-10
```

Omit `--start` and `--end` for the normal rolling production window.

`sync_recent_records` remains the one-off API reconciliation command. `collect_wca_live` remains
as a compatibility alias for the supervised subscription command.

## Protocol and payload

The contract was verified against WCA Live's deployed introspection schema and the official
[`thewca/wca-live`](https://github.com/thewca/wca-live) source.

- GraphQL HTTP: `https://live.worldcubeassociation.org/api`
- WebSocket: `wss://live.worldcubeassociation.org/socket/websocket?vsn=2.0.0`
- Protocol: Phoenix v2 array frames with an Absinthe control channel, not
  `graphql-transport-ws`
- Join/document topic: `__absinthe__:control`
- Update topic: the per-document subscription ID returned by WCA Live
- Join event: `phx_join`
- Document event: `doc`
- Update event: `subscription:data`
- Unsubscribe event: `unsubscribe`
- Heartbeat: topic `phoenix`, event `heartbeat`

The subscription operation is:

```graphql
subscription CubeRecordRoundUpdated($id: ID!) {
  roundUpdated(id: $id) {
    id
    results {
      id
      ranking
      attempts { result }
      best
      average
      singleRecordTag
      averageRecordTag
      enteredAt
      person { id wcaId name country { iso2 } }
    }
  }
}
```

Client frames have the shape:

```json
["join-ref", "ref", "__absinthe__:control", "doc", {
  "query": "...",
  "variables": {"id": "ROUND_ID"}
}]
```

The acknowledgement is a `phx_reply` whose payload contains:

```json
{"status": "ok", "response": {"subscriptionId": "..."}}
```

Round updates have the payload shape:

```json
{
  "subscriptionId": "...",
  "result": {"data": {"roundUpdated": {"id": "...", "results": []}}}
}
```

WCA Live accepts multiple `doc` subscription operations over one connection. The supervisor
uses one socket, resubscribes every active round after reconnecting, and adds newly discovered
rounds without duplicating existing subscriptions.

## Snapshot and correction policy

`SubscriptionResultState` is the recovery source. An in-memory mapping is only a transport
optimization. Result identity is the WCA Live result ID within its persisted round. Meaningful
state includes attempts, best, average, record tags, competitor identity, and `enteredAt`.
Ranking, advancing flags, JSON ordering, and row ordering are ignored.

The first full state received for a round is persisted as its baseline. Rows whose timezone-aware
`enteredAt` falls inside the configured catch-up window are evaluated once; older rows establish
state without generating a historical flood. Subsequent additions and meaningful corrections are
always evaluated. Repeated snapshots, reordering, restarts, and reconnects do not create new
observations. Removed rows are persisted as inactive and their active subscription observations
are marked withdrawn.

Record classification comes from WCA Live's authoritative `singleRecordTag` and
`averageRecordTag` values (`WR`, `CR`, or `NR`). CubingNow does not pretend its receipt time is the
solve time: `detected_at` is the first time that pipeline recognized the record. Timed results,
DNF/DNS/zero, multi-blind encoding, and Fewest Moves singles/means use event-aware utilities.

## Health, logs, and inspection

Health/status:

```bash
curl http://localhost:8000/api/ingestion-status/
```

The subscription worker publishes protocol diagnostics under
`graphql_subscription.metadata.websocket`. Its counters distinguish acknowledged documents and
heartbeat replies from actual `subscription:data` frames. `last_unexpected_frame` records only the
frame envelope and payload keys, not the full result payload. Unexpected topics/events, unknown
subscription IDs, malformed frames, and uncorrelated replies are also written to the worker log.

Independent record collections:

```bash
curl 'http://localhost:8000/api/recent-records/?source=api_polling'
curl 'http://localhost:8000/api/recent-records/?source=graphql_subscription'
curl 'http://localhost:8000/api/recent-records/comparison/'
```

Docker logs:

```bash
docker compose logs -f api-poller
docker compose logs -f subscription-worker
```

The Django admin exposes record observations, raw observations, round targets, persisted result
states, ingestion runs, and worker statuses at `http://localhost:8000/admin/`.

Export all observations after the weekend:

```bash
cd backend
uv run python manage.py dumpdata records.RecentRecordObservation --indent 2 \
  > weekend-record-observations.json
```

## Tests

```bash
cd backend
uv run pytest
uv run ruff check .
uv run python manage.py check --settings=config.settings.test
uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test

cd frontend
npm test
npm run lint
npm run build
```

The tests cover inclusive overlap/lookback, target flattening, normalization, additions,
corrections, removals, reordering, duplicate snapshots, restart recovery, timed/DNF/DNS/FMC/
multi-blind values, source-specific uniqueness, timezone-aware detection, independent API
endpoints, Phoenix multiplexing, and reconnect/resubscription behavior with a mocked socket.

## Known limitations

- WCA Live publishes `roundUpdated` only when a subscribed round changes; it does not send a
  snapshot merely because CubingNow subscribed. The catch-up policy is applied to the first full
  state actually received.
- WCA Live's `recentRecords` resolver keeps only the best recent record when the same type is
  broken repeatedly. A polling interval can therefore miss a transient record; that is part of
  the experiment rather than something the subscription pipeline backfills.
- The `competitions` schema has no true pagination. CubingNow omits `limit`, which is the only
  schema-supported complete query, and records the fetched count in worker metadata.
- Classification relies on WCA Live's source record tags. If upstream record data or tags are
  delayed or corrected, CubingNow records that observed behavior rather than inventing a tag.
- The subscription worker retires the configured targets once the UTC calendar date is later
  than `weekend_end`.

## Manual weekend checklist

1. Start PostgreSQL, backend, frontend, `api-poller`, and `subscription-worker` before the range.
2. Open `/api/ingestion-status/`; confirm API status is `running` and `last_successful_poll_at`
   advances.
3. Confirm subscription status is `running`, `connected` is true, and `last_connection_at` exists.
4. Compare `metadata.competitions_overlapping`, `metadata.rounds_discovered`, and
   `subscription_rounds.subscribed`.
5. Leave the worker running through a rediscovery interval and confirm newly imported rounds raise
   both discovered and subscribed counts.
6. Open `http://localhost:5173`; confirm both independently loading tables are visible.
7. Wait for a record observation in either table.
8. Confirm its `ingestion_method`, raw source observation, and detection timestamp in the admin or
   source-specific API.
9. Compare the canonical key, competitor, event, result, competition, round, and record level.
10. Read `detection_time_difference_seconds` or the comparison endpoint for API versus
    subscription detection time.
11. Refresh the page and restart each worker; confirm observation counts do not grow for unchanged
    source state.
12. Interrupt/restart the subscription worker; confirm reconnect/resubscribe logs and no duplicate
    observations.
13. Note unmatched canonical keys from `/api/recent-records/comparison/`; do not backfill either
    pipeline from the other.
14. Export `RecentRecordObservation` with `dumpdata` and retain the separate ingestion methods for
    analysis.
