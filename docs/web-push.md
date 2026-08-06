# Guest Web Push operations

CubingNow's phase-one alerts are anonymous, standards-based Web Push subscriptions for world,
continental, and national records. The browser owns a Web Push subscription; Django stores its
endpoint capability, browser-generated encryption values, selected WR/CR/NR preferences, and
delivery status/timestamps. CubingNow does not store an IP address or claim that an endpoint is a
physical person or device.

Production requires HTTPS. Localhost is treated as a secure context by supporting browsers. On
iPhone and iPad, Web Push is available from an installed Home Screen web app; permission must be
requested from the explicit **Enable record alerts** action inside that installed app.

## Generate one persistent VAPID key pair

`pywebpush` 2.3.0 accepts either a PEM private-key path or a base64-encoded DER private key. The
deployment uses an environment-friendly base64url DER value. Generate the pair once in a private
directory after installing the locked backend dependencies:

```bash
umask 077
mkdir -p .local-vapid
cd .local-vapid
../backend/.venv/bin/vapid --gen
../backend/.venv/bin/vapid --applicationServerKey --private-key private_key.pem
openssl ec -in private_key.pem -outform DER \
  | openssl base64 -A \
  | tr '+/' '-_' \
  | tr -d '='
```

The `Application Server Key` is `WEB_PUSH_VAPID_PUBLIC_KEY`. The final base64url line is
`WEB_PUSH_VAPID_PRIVATE_KEY`. Store both in the deployment secret manager, keep the PEM files out
of Git, and back them up securely. Do not regenerate them on restart or deployment. Set
`WEB_PUSH_VAPID_SUBJECT` to an HTTPS contact URL or `mailto:` address.

Required settings:

```dotenv
PUSH_NOTIFICATION_PROVIDER=webpush
PUSH_RECORD_EVENT_SOURCE=disabled
WEB_PUSH_VAPID_PUBLIC_KEY=...
WEB_PUSH_VAPID_PRIVATE_KEY=...
WEB_PUSH_VAPID_SUBJECT=mailto:contact@cubingnow.com
```

Keep `PUSH_RECORD_EVENT_SOURCE=disabled` during migrations, initial imports, and worker setup. Set
it to exactly one live ingestion method (`api_polling`, `graphql_subscription`, or
`cubingchina_websocket`) only when ready. `all` exists for controlled verification tests; do not
use it during the current source-comparison experiment. A database uniqueness constraint on the
ingestion-neutral canonical key remains a second deduplication layer.

Optional worker settings are documented in `.env.example`: poll interval, batch size, finite
attempt count, retry schedule, claim timeout, TTL, request timeout, and worker identifier.

## Run locally

Without Docker:

```bash
uv sync --project backend --extra dev
uv run --project backend python backend/manage.py migrate
uv run --project backend python backend/manage.py runserver
cd frontend && npm ci && npm run dev
```

In another terminal, start delivery:

```bash
uv run --project backend python backend/manage.py run_notification_worker
```

With Docker, copy `.env.example` to `.env`, set the persistent keys, then run:

```bash
docker compose up --build database backend frontend api-poller subscription-worker cubingchina-worker notification-worker
```

The migration is `notifications.0001_initial`. Apply it with:

```bash
uv run --project backend python backend/manage.py migrate
```

## Browser workflow

The page feature-detects the Service Worker, Push Manager, and Notification APIs. It shows the
three preferences before permission is requested and calls `Notification.requestPermission()`
only from the enable-button handler. An existing browser subscription is reused and resynchronized
instead of recreated. Preference PATCHes never recreate it. Turning all preferences off keeps the
endpoint active with no eligible alert types; only **Disable notifications** deactivates it and
calls browser `unsubscribe()`.

If browser unsubscribe succeeds while server cleanup fails, the opaque endpoint UUID and
management token remain in local storage as a pending cleanup operation and are retried next
visit. The raw push URL and encryption keys are never returned by the API. CSRF is required even
for anonymous mutations. The current Render layout hosts the site and API on HTTPS sibling
origins; credentialed CORS and an API-issued CSRF token secure that existing arrangement. A future
same-origin reverse proxy can be adopted without changing the API contract.

The root service worker always displays a notification for a push event, falls back safely for a
missing/malformed payload, and permits click navigation only to a relative same-origin path. It
focuses/navigates an existing CubingNow window where practical, otherwise it opens one.

## Test a real delivery

After enabling alerts in a browser, copy its public endpoint UUID from Django admin (never copy the
raw subscription URL). Queue a synthetic event through the normal event/delivery path:

```bash
uv run --project backend python backend/manage.py send_test_notification \
  --endpoint 00000000-0000-0000-0000-000000000000 --level WR
```

The running worker sends it. A development-only bulk test requires both explicit flags:

```bash
uv run --project backend python backend/manage.py send_test_notification \
  --all-active --allow-bulk --level WR
```

Run one queued batch and exit with:

```bash
uv run --project backend python backend/manage.py run_notification_worker --once
```

Docker equivalents use `docker compose exec backend python manage.py ...`.

## Inspect and operate

Django admin lists endpoint UUIDs, active state, preferences, events, and deliveries without
listing raw Web Push URLs or keys. Filter `NotificationDelivery` by `pending`, `retry`, or
`permanently_failed` to inspect queue health and sanitized error codes. The shell equivalents are:

```python
from apps.notifications.models import NotificationDelivery, NotificationEndpoint
NotificationEndpoint.objects.filter(active=True).count()
NotificationDelivery.objects.filter(status="pending").count()
NotificationDelivery.objects.filter(status="permanently_failed").values(
    "id", "endpoint_id", "last_error_code", "last_error_message"
)
```

HTTP 404/410 deactivates the endpoint and permanently fails its outstanding rows. HTTP 429 honors
`Retry-After`; 5xx, timeouts, and temporary network errors use bounded exponential-style retry
delays with jitter. VAPID/configuration failures retry finitely but do not deactivate endpoints.
Workers claim rows with a short PostgreSQL `select_for_update(skip_locked=True)` transaction,
release the transaction before the HTTP request, and reclaim stale processing rows after the
claim timeout. Sent rows are never eligible again.
On SIGTERM/SIGINT, the worker finishes the in-flight HTTP request, immediately releases any
unattempted claimed rows, and exits without waiting for another poll.

## Deduplication, corrections, and extension points

Record persistence registers publication with `transaction.on_commit()`. The durable event key is
`record:<RecentRecordObservation.canonical_key>` and never includes the ingestion method.
`get_or_create`, unique event keys, and unique `(event, endpoint)` deliveries protect restarts and
cross-pipeline duplicates. A corrected result can update an unsent event payload; after any send,
the correction is counted and logged without emitting a second "new record" alert.

`attach_endpoint_to_user(endpoint, user)` is the future account-claim hook. Preference resolution
is user-level explicit preference, then endpoint-level preference, then the safe type default.
Entitlement checks live in `policies.py`, not endpoint or provider models. A future
`OneSignalProvider` can implement the `PushProvider` protocol and be selected in the provider
factory; record publication, preferences, entitlements, and deliveries remain unchanged.

## Manual acceptance checklist

1. Configure the persistent keys, migrate, and start API, frontend, record workers, and notification worker.
2. Open the site in a supported desktop browser and choose WR/CR/NR before clicking enable.
3. Confirm permission appears only after the click and one active endpoint exists without login.
4. Queue a test WR, observe one pending row, delivery success, a visible notification, and safe click navigation.
5. Disable CR, queue a CR and confirm no delivery; queue a WR and confirm one delivery.
6. Feed one canonical result through both WCA paths and confirm one event and one endpoint delivery.
7. Restart the worker and confirm sent delivery is not repeated.
8. Disable notifications and confirm the endpoint becomes inactive.
9. Search logs for a known test endpoint/key fragment and confirm no subscription capabilities or private key appear.

For iPhone/iPad, use Safari's Share → Add to Home Screen, open CubingNow from its Home Screen icon,
then repeat the enable/test/click/disable sequence. Automated tests cover feature states and worker
logic, but a real Apple device and Apple push service are still required to validate platform UI,
Focus modes, and delivery timing. The checked-in icon is a temporary implementation derived from
the existing four-tile brand mark and should be replaced when final brand artwork is available.

Run all automated checks without real push sends:

```bash
cd backend && .venv/bin/pytest && .venv/bin/ruff check .
cd frontend && npm test && npm run lint && npm run build
```
