# CubingNow

CubingNow collects official speedcubing record observations and featured competitor attendance, stores normalized data, and exposes it through a Django REST API to a React frontend.

## Architecture

- `backend/`: Python 3.12, Django 5.2, Django REST Framework
- `backend/integrations/wca_live/`: WCA Live HTTP, Phoenix/Absinthe subscription,
  snapshot diffing, mapping, and source-isolated ingestion code
- `backend/integrations/cubingchina/`: public competition discovery, attendance scraping,
  and independent live-results WebSocket collection
- `frontend/`: Node.js and React with Vite
- PostgreSQL: shared persistent store for the API and collection workers

The React frontend only calls the CubingNow API. External WCA and CubingChina communication stays
inside backend integration boundaries.

## Development with Docker

Install Docker Desktop, then run:

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- API: <http://localhost:8000/api/>
- Admin: <http://localhost:8000/admin/>

PostgreSQL data is stored in the Docker volume `postgres_data`. It is local development data and is not committed or copied to production.

Guest WR/CR/NR Web Push alerts use a durable notification event/delivery queue and a separate
`notification-worker` service. Configure one persistent VAPID key pair before enabling record
publication; see [Guest Web Push operations](docs/web-push.md).

## Development without Docker

Use Python 3.12 or newer and PostgreSQL 14 or newer.

```bash
uv sync --project backend --extra dev
cd backend
uv run python manage.py migrate
uv run python manage.py runserver
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Copy `.env.example` to `.env` and adjust values for your environment. Never commit `.env` or production credentials.

## Data collection processes

The web API and three collectors are separate processes sharing PostgreSQL. Docker Compose starts
an API poller, a GraphQL subscription worker, and a CubingChina live-results worker independently.
Their record observations,
deduplication keys, source payloads, and detection timestamps are isolated by ingestion method.

Run a one-off synchronization manually with:

```bash
python backend/manage.py sync_recent_records
```

Or run the API worker outside Docker with:

```bash
python backend/manage.py run_wca_live_api_polling
```

Run the subscription worker with:

```bash
python backend/manage.py run_wca_live_subscriptions \
  --start 2026-08-06 --end 2026-08-10
```

Run the continuously discovering CubingChina worker with:

```bash
python backend/manage.py run_cubingchina_websocket
```

It discovers official WCA competitions from CubingChina every 15 minutes, opens one read-only
socket per currently active competition, fetches every round sequentially, and reconciles full
snapshots after reconnecting. See [CubingChina live collection](docs/cubingchina-live.md).

Synchronize the public WCA and CubingChina accepted-registration lists for the current
Wednesday-through-Tuesday attendance window with:

```bash
python backend/manage.py sync_weekend_attendance
```

The production Blueprint runs this idempotent command every six hours. Collection happens before
the database transaction: if any selected source page fails, existing accepted attendance is left
unchanged. See [Weekend attendance](docs/weekend-attendance.md) for the source contracts, API,
date semantics, and operating details.

The website reads the three database collections independently. It never owns an upstream
subscription. See [Weekend record verification](docs/weekend-record-verification.md) for the
verified protocol, initial snapshot policy, environment variables, logs, health checks, tests,
and the operating checklist.

Record notification publication is disabled by default to prevent historical imports from
creating alerts. After the notification migration, VAPID configuration, and notification worker
are ready, set `PUSH_RECORD_EVENT_SOURCE=api_polling` (or the one verified source chosen for the
experiment). Event uniqueness uses the existing ingestion-neutral canonical record key, so a
second source cannot create another event or endpoint delivery for the same real record.

## Production data

Production should use a managed PostgreSQL database with automated backups. The hosting platform provides `DATABASE_URL` and other secrets. Deployments install dependencies from the committed manifests, run `python manage.py migrate`, and start separate API, collector, and scheduled reconciliation processes. Local database contents never deploy automatically.

Python dependencies are declared in `backend/pyproject.toml` and resolved exactly in `backend/uv.lock`. JavaScript dependencies are declared in `frontend/package.json` and resolved exactly in `frontend/package-lock.json`. Production installs from these lockfiles rather than copying either local dependency directory.

## Deploying to Render

The root `render.yaml` defines eight Render resources:

- `cubingnow-web`: React static site at `cubingnow.com`
- `cubingnow-api`: Django API at `api.cubingnow.com`
- `cubingnow-api-poller`: continuous WCA Live recent-record poller
- `cubingnow-subscription-worker`: continuous WCA Live round subscription supervisor
- `cubingnow-notification-worker`: queued Web Push delivery worker
- `cubingnow-cubingchina-worker`: continuous CubingChina discovery and live collection
- `cubingnow-weekend-attendance-sync`: six-hourly public registration synchronization
- `cubingnow-db`: PostgreSQL database

After pushing this repository to GitHub, create a new Blueprint in Render and connect the repository. Render reads `render.yaml`, generates the Django secret, connects both Python services to PostgreSQL, and builds the frontend with the production API URL.

The three collectors are paid Starter background workers because Render does not provide free background workers. Before using the free PostgreSQL plan for anything important, review its retention and backup limitations in the Render dashboard.

Add `cubingnow.com` to the static site and `api.cubingnow.com` to the API service, then copy Render's requested DNS records to the DNS provider for `CubingNow.com`. Render provisions HTTPS automatically after domain verification.

## Tests

```bash
cd backend && pytest
cd frontend && npm test
```

Backend notification coverage mocks `pywebpush`; automated tests never contact a real browser push
service. Run `npm run build` to verify that the root service worker, manifest, and PWA icons are
included in the production frontend artifact.
