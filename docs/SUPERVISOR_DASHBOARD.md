# Supervisor dashboard

The `/supervisor` page has two tabs.

- **Live**: the cases still open, how long they have been open, and each one's
  action history.
- **Disposed cases**: how long disposal took and which stage the time went to,
  broken down by department, category and route.

It replaced the ten-panel monitoring view in September 2026. That view is kept at
the git tag `stable/2026-09-28`.

## Publish the release

```bash
uv run janasunani-publish-dashboard            # reads data/interim/, writes outputs/dashboard/
```

The publisher (`janasunani/analytics/dashboard.py`) reads only `complaints.parquet`
and `action_history.parquet`. It writes five files:

| File | What it holds |
|---|---|
| `meta.json` | the snapshot date, the entry offices, the filing years |
| `status_counts.parquet` | filings by entry office, year and status |
| `open_cases.parquet` | one row per open case |
| `open_actions.parquet` | every recorded action on those cases |
| `disposed_phases.parquet` | one row per disposed case whose five stages tile its time to close |

The files contain ticket numbers and office codes, so they are ignored by Git.
The deployed site is restricted to people cleared to see raw complaints. No
citizen text, contact detail or officer name is read.

On the box, publish into the repository's `outputs/dashboard/`.
`deploy/docker-compose.yml` mounts that folder read-only into the API as
`JANASUNANI_DASHBOARD_DIR`. Until a release is there, every `/dashboard/*`
endpoint answers 503 and the page says so.

## Definitions

- **Live is a snapshot.** The lake is refreshed by re-materialisation, not fed
  live, so the tab says what date it is live as of. That date is the latest
  filing, action or resolution in the extract. Days open are counted to it.
- **Open** means the status is neither Disposed nor Discard. This is the same
  rule as `grievance_base.outcome = 'Open'`.
- **Office** is the entry office: the desk that first received the case
  (`complaints.office`, grouped as in `grievance_base.entry_route`). Statewide
  means every office.
- **Awaiting assignment** means no second office has ever acted on the case, so it
  is still with the desk it entered at. A case with no recorded action counts.
  The queue lists these first, then the longest open.
- **The timeline** lists each recorded action with the office that took it. The
  record names who acted, not who a case was forwarded to. So the last step
  ("waiting here") is the office that acted last, which may be the one that
  forwarded it.
- **Scope.** The entry office and the year filed sit at the top of the page and
  carry through everything below it: the status tree, both tabs and every
  drill-down describe the same cases. The page opens statewide on all years.
- **Year filed** is the July-June year a case was filed in, or all years. A
  year the extract does not cover end to end is marked "part year".
- **Cases by status** is every case filed in scope, split into open, disposed
  and discarded (the CA&GR note's rule, `grievance_base.outcome`), which add up
  to the total. Disposed shows how many were disposed with benefit, the one
  outcome recording that something reached the citizen. Open opens the Live
  tab, and its count is exactly the Live total. Disposed opens the Disposed tab,
  which covers only the disposed cases whose stages tile, so its count is
  smaller.
- **Disposed** covers cases disposed by the snapshot date. A recent year looks
  faster than an older one partly because its slow cases are still open: FY
  2021-22 averages 210 days and FY 2024-25 63, but most of the difference is
  which cases have closed yet. Compare years with that in mind.
- **Stages** are the five spans from `janasunani/analytics/journey.py`:
  registration, first assignment, field action, review, closure. They are the
  same spans as in the bottleneck notes, with the same colours. They are means,
  so they add up to the average time. The dashboard shows only the average:
  a second number beside it confused officers. Cases whose recorded dates
  run past their closing date do not tile, and are left out; the SSEPD note
  §2.5 shows how many.
- **Routes** are the sequence of roles that held a case, with consecutive
  repeats collapsed. The largest routes get a row each until they cover 95% of
  cases, up to ten rows, and only if they have at least 10 cases. The rest fold
  into one "Other routes" row, which always comes last. Within a category,
  routes fragment: in the FY 2024-25 release about a fifth of a typical
  category's cases travel routes fewer than 10 cases took, and "Other" is
  usually slower than the named routes.
- **Sorting.** Departments, categories and routes sort by volume, slowest or
  fastest.

## Contract

| Endpoint | Returns |
|---|---|
| `GET /dashboard/meta` | snapshot date, offices, filing years |
| `GET /dashboard/status?office=&year=` | cases filed, and open, disposed (with benefit) and discarded |
| `GET /dashboard/live?office=` | open total and counts for 0-30, 31-60 and 61+ days |
| `GET /dashboard/live/queue?bucket=&office=&awaiting=all\|only\|hide&category=&dept=&limit=&offset=` | the queue for one age band, with filter counts |
| `GET /dashboard/ticket/{ticket_no}/timeline` | an open case's actions, the current one flagged |
| `GET /dashboard/disposed?level=overall\|dept\|category&order=volume\|slowest\|fastest&office=&year=&dept=` | average days and stage means |
| `GET /dashboard/disposed/routes?office=&dept=&category=&order=` | the named routes in that order, then "Other routes" |

The models are `Dashboard*` in `janasunani/serving/schemas.py`, mirrored in
`frontend/lib/dashboard.ts`. `tests/test_dashboard.py` pins them over a fixture
lake.
