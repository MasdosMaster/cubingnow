# CubingChina live collection

CubingNow runs `cubingchina_websocket` as an independent recent-record observation pipeline. It
uses only CubingChina's public competition pages and read-only live-results WebSocket messages.
The React application reads persisted observations from CubingNow's API and never connects to
CubingChina directly.

## Discovery lifecycle

The worker runs discovery immediately and repeats it every 15 minutes by default. Each pass reads
the public competition index, filters a rolling one-day lookback and seven-day lookahead, resolves
official WCA IDs from competition detail pages, and parses the live page's `data-c` and
`data-events` attributes. A competition without a ready live page remains pending and is retried.

Collectors begin as soon as a relevant official competition exposes usable live metadata, even
if its start date is still inside the lookahead window. They remain eligible through the end date
plus a 720-minute grace period, after which their targets are retired without deleting observations.
This avoids local-time ambiguity at the start of a competition. Transient source failures never
clear previously persisted state.

## Socket protocol

The endpoint is `wss://cubing.com/ws`. One socket is used per active competition because the
official client treats the competition selection as connection-level state. On every connection
or reconnect CubingNow:

1. sends `{"type":"competition","competitionId":...}` and receives the `users` roster;
2. sends `{"type":"result","action":"rounds"}`;
3. sequentially requests every event/round using `filter: "all"` and `combine: false`;
4. persists full snapshots before continuing with `result.new` and `result.update` messages.

Sequential requests are required because `result.all` has no request identifier. Reconnects
repeat every snapshot so missed updates are healed from authoritative state. The connection sends
the JSON string `"ping"` after an idle interval and accepts `"pong"` responses. No data-entry,
round-management, or chat action is implemented.

## Detection semantics

Every `result.v` attempt is retained in `CubingChinaResultState`, including unfinished
rows. Format `a` requires five nonzero attempt positions and format `m` requires three;
DNF/DNS count as entered and zero does not. A competitor who fails the format-specific
cutoff phase is finalized with a single and no average. CubingNow never treats `a` alone
as proof of completion, so a positive or `-1` average cannot cross into canonical facts
while required attempts are still missing.

Once structurally finalized, CubingNow creates at most one source-specific single claim
for `b` and one average claim for `a`, then reconciles them with other providers. Later
corrections revise the same round-and-kind identities.

CubingChina exposes record tags as `sr` (single) and `ar` (average). Continental tags (`AfR`,
`AsR`, `ER`, `NAR`, `OcR`, and `SAR`) are normalized to `CR`; `WR`, `CR`, and `NR` produce
observations. CubingChina does not expose an entry timestamp, so `detected_at` is the
first moment this pipeline observed the record, not the solve time. The first-ever round snapshot
evaluates all current tags; persisted state prevents restart or reconnect floods. Corrections
retain the original detection time, and removed tags are marked withdrawn. Recent-record API
responses show active observations by default; corrected history remains available with
`?status=withdrawn`.

CubingChina record tags are retained only as Source Claims. Before a CubingChina achievement can
appear on the homepage or enter the notification queue, CubingNow compares the encoded result with
the matching world, WCA-continent, or WCA-country value from `/api/v0/records`. Both a better value
and equality validate the level, because the official endpoint may already contain the newly
ratified result. The full normalized WCA snapshot and a per-result/per-level Record Validation are
stored for audit. This validation confirms record qualification; it does not authenticate the
underlying solve. Effective live benchmark replay still prevents a later, slower result from being
classified against an obsolete pre-competition baseline.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CUBINGCHINA_BASE_URL` | `https://cubing.com` | Public HTTP source |
| `CUBINGCHINA_WS_URL` | `wss://cubing.com/ws` | Live-results socket |
| `CUBINGCHINA_DISCOVERY_INTERVAL_SECONDS` | `900` | Rediscovery interval |
| `CUBINGCHINA_LOOKBACK_DAYS` | `1` | Rolling discovery lookback |
| `CUBINGCHINA_LOOKAHEAD_DAYS` | `7` | Rolling discovery lookahead |
| `CUBINGCHINA_COMPLETION_GRACE_MINUTES` | `720` | Post-competition collection grace |
| `CUBINGCHINA_MAX_CONNECTIONS` | `10` | Concurrent competition sockets |
| `CUBINGCHINA_RETRY_BASE_SECONDS` | `1` | Reconnect backoff base |
| `CUBINGCHINA_RETRY_MAX_SECONDS` | `60` | Reconnect backoff ceiling |
| `CUBINGCHINA_KEEPALIVE_SECONDS` | `55` | Idle interval before JSON ping |
| `WORKER_TELEMETRY_INTERVAL_SECONDS` | `5` | Queue-diagnostic persistence interval |
| `WCA_PUBLIC_BASE_URL` | `https://www.worldcubeassociation.org` | Official records API origin |

Run locally with:

```bash
cd backend
uv run python manage.py run_cubingchina_websocket
uv run python manage.py refresh_wca_record_validations
```

Inspect observations and health with:

```bash
curl 'http://localhost:8000/api/recent-records/?source=cubingchina_websocket'
curl 'http://localhost:8000/api/ingestion-status/'
```

The health payload reports worker state and heartbeat, connected/target/pending competition
counts, target round count, connection/message/discovery/snapshot timestamps, observation count,
and the latest error. `metadata.competitions` includes each active target's connection state,
queue depth, high-water mark, frame counters, last message/snapshot times, and its own error so one
broken competition remains visible without making healthy collectors appear failed. The same
information is visualized at `https://cubingnow.com/debug`.
