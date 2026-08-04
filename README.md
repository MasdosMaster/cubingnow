# CubingNow

CubingNow collects official speedcubing record observations and featured competitor attendance, stores normalized data, and exposes it through a Django REST API to a React frontend.

## Architecture

- `backend/`: Python 3.12, Django 5.2, Django REST Framework
- `backend/integrations/wca/`: WCA HTTP, GraphQL, subscription, mapping, and ingestion code
- `frontend/`: Node.js and React with Vite
- PostgreSQL: shared persistent store for the API and collection workers

The React frontend only calls the CubingNow API. External WCA communication stays in the backend integration boundary.

## Development with Docker

Install Docker Desktop, then run:

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- API: <http://localhost:8000/api/>
- Admin: <http://localhost:8000/admin/>

PostgreSQL data is stored in the Docker volume `postgres_data`. It is local development data and is not committed or copied to production.

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

The web API and data collector are separate processes sharing PostgreSQL. Docker Compose starts both automatically. The collector polls WCA Live once per minute and stores new observations idempotently.

Run a one-off synchronization manually with:

```bash
python backend/manage.py sync_recent_records
```

Or keep it running outside Docker with:

```bash
python backend/manage.py sync_recent_records --watch --interval 60
```

WCA Live's public GraphQL schema exposes `recentRecords`, which is the MVP source. Its `roundUpdated` subscription requires an individual round id, so subscriptions are reserved for a later optimization and are not required for reliable record collection.

## Production data

Production should use a managed PostgreSQL database with automated backups. The hosting platform provides `DATABASE_URL` and other secrets. Deployments install dependencies from the committed manifests, run `python manage.py migrate`, and start separate API, collector, and scheduled reconciliation processes. Local database contents never deploy automatically.

Python dependencies are declared in `backend/pyproject.toml` and resolved exactly in `backend/uv.lock`. JavaScript dependencies are declared in `frontend/package.json` and resolved exactly in `frontend/package-lock.json`. Production installs from these lockfiles rather than copying either local dependency directory.

## Deploying to Render

The root `render.yaml` defines four Render resources:

- `cubingnow-web`: React static site at `cubingnow.com`
- `cubingnow-api`: Django API at `api.cubingnow.com`
- `cubingnow-collector`: continuous WCA record collector
- `cubingnow-db`: PostgreSQL database

After pushing this repository to GitHub, create a new Blueprint in Render and connect the repository. Render reads `render.yaml`, generates the Django secret, connects both Python services to PostgreSQL, and builds the frontend with the production API URL.

The collector is a paid Starter background worker because Render does not provide free background workers. Before using the free PostgreSQL plan for anything important, review its retention and backup limitations in the Render dashboard.

Add `cubingnow.com` to the static site and `api.cubingnow.com` to the API service, then copy Render's requested DNS records to the DNS provider for `CubingNow.com`. Render provisions HTTPS automatically after domain verification.

## Tests

```bash
cd backend && pytest
cd frontend && npm test
```
