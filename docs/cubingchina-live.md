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
plus a 12-hour grace period, after which their targets are retired without deleting observations.
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

CubingChina exposes record tags as `sr` (single) and `ar` (average). Only `WR`, `CR`, and `NR`
produce observations. CubingChina does not expose an entry timestamp, so `detected_at` is the
first moment this pipeline observed the record, not the solve time. The first-ever round snapshot
evaluates all current tags; persisted state prevents restart or reconnect floods. Corrections
retain the original detection time, and removed tags are marked withdrawn.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CUBINGCHINA_BASE_URL` | `https://cubing.com` | Public HTTP source |
| `CUBINGCHINA_WS_URL` | `wss://cubing.com/ws` | Live-results socket |
| `CUBINGCHINA_DISCOVERY_INTERVAL_SECONDS` | `900` | Rediscovery interval |
| `CUBINGCHINA_LOOKBACK_DAYS` | `1` | Rolling discovery lookback |
| `CUBINGCHINA_LOOKAHEAD_DAYS` | `7` | Rolling discovery lookahead |
| `CUBINGCHINA_COMPLETION_GRACE_HOURS` | `12` | Post-competition collection grace |
| `CUBINGCHINA_MAX_CONNECTIONS` | `10` | Concurrent competition sockets |
| `CUBINGCHINA_RETRY_BASE_SECONDS` | `1` | Reconnect backoff base |
| `CUBINGCHINA_RETRY_MAX_SECONDS` | `60` | Reconnect backoff ceiling |
| `CUBINGCHINA_KEEPALIVE_SECONDS` | `55` | Idle interval before JSON ping |

Run locally with:

```bash
cd backend
uv run python manage.py run_cubingchina_websocket
```

Inspect observations and health with:

```bash
curl 'http://localhost:8000/api/recent-records/?source=cubingchina_websocket'
curl 'http://localhost:8000/api/ingestion-status/'
```

The health payload reports worker state and heartbeat, connected/target/pending competition
counts, target round count, connection/message/discovery/snapshot timestamps, observation count,
and the latest error. `metadata.competitions` includes each active target's connection state,
last message/snapshot times, and its own error so one broken competition remains visible without
making healthy collectors appear failed.
