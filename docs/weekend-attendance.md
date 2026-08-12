# Weekend attendance

CubingNow’s “Competing this weekend” table is a persisted view of public accepted-registration
lists. It is an attendance observation, not a guarantee that someone will be physically present.

## Window

The attendance window is the most recent Wednesday through the following Tuesday, inclusive. It
is calculated in `ATTENDANCE_WINDOW_TIME_ZONE`, which defaults to `Europe/Amsterdam`. For an
as-of date of 2026-08-05 the window is 2026-08-05 through 2026-08-11. A competition is included
when its date range overlaps this window at either edge.

The WCA Live verification worker uses the same rolling window by default. Its optional
`WCA_WEEKEND_START` and `WCA_WEEKEND_END` overrides are not used by attendance synchronization.

## Sources and synchronization

Run a synchronization with:

```bash
python backend/manage.py sync_weekend_attendance
```

For a controlled backfill or parser check, an as-of date can be supplied:

```bash
python backend/manage.py sync_weekend_attendance --date 2026-08-05
```

The collector uses the same public contracts as the two websites:

- The WCA competition screen loads `/api/v0/competitions`; its public accepted-registration table
  loads `/api/v1/competitions/{competition_id}/registrations` in one response.
- CubingChina redirects to `cubing.com`. Its competition index, competition detail, and competitor
  list are server-rendered HTML tables. Returning competitors link to `/results/person/{wca_id}`;
  first-timers have no such link and are discarded before persistence.

Competition discovery completes before any registration list is requested, and only overlapping
competitions are inspected. WCA IDs identify competitors. WCA competition IDs identify official
competitions; non-WCA CubingChina competitions use a stable `cubingchina:{slug}` source key.

All source reads and parsing complete before a single database transaction. A successful refresh
marks registrations that disappeared as unaccepted. A failed or rate-limited refresh records a
failed `AttendanceSyncRun` but does not modify existing attendance. The Render Blueprint runs the
command every six hours.

## API

`GET /api/competing-this-weekend/` returns one unpaginated, case-insensitively alphabetized list.
Each competitor has an explicit one-based rank and a list of every matching competition. The
backend uses WCA ID as the deterministic tie-breaker.

An optional validated filter reranks the filtered result from one:

```text
GET /api/competing-this-weekend/?continent=Europe
```

The response contains:

- `window`: `start_date`, `end_date`, and timezone;
- `last_successful_sync_at` and `sync_status` (`fresh`, `stale`, or `not_yet_synchronised`);
- `selected_continent`, supported `continents`, and `count`;
- `results`: rank, WCA ID, name, country, continent, and matching competitions.

## Parser maintenance

The WCA JSON shape and CubingChina table headers/profile links are covered by committed fixtures.
The most fragile selectors are CubingChina’s `Competition Name`, `header-username`, and
`header-region` table markers and its `/results/person/` profile links. A source layout change is
treated as a failed synchronization so cached attendance remains intact while the parser is fixed.
