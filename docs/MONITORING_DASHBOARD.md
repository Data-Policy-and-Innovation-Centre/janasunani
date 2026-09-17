# Monitoring dashboard

The `/supervisor` page opens on Monitoring and keeps the earlier Intelligence
briefing in a separate tab. Monitoring reads a validated, aggregate-only
release; the API never queries the lake or secured dedup tables at request
time.

## Publish the release

```bash
uv run janasunani-publish-monitoring \
  --citizen-aggregates outputs/monitoring/citizen_aggregates.json
```

The publisher reads only `complaints.parquet`, `action_history.parquet`, and
governed ticket-to-group mappings. It writes:

- `outputs/monitoring/monitoring_dashboard_v1.json` for serving;
- `outputs/monitoring/monitoring_results_v1.csv` for review.

Both outputs are local and ignored by Git. Set
`JANASUNANI_MONITORING_ARTIFACT` to the container-visible JSON path in a
protected deployment.

## Contract

- `GET /supervisor/monitoring/catalog`
- `GET /supervisor/monitoring?scope_id=<allowlisted-id>&period=<allowlisted-period>`

The catalogue exposes stable IDs, labels, parent scopes, definitions, and
published periods. The dashboard returns exactly six panels: aging and
escalation; transfers and loops; end-to-end journey; ATR queue discipline;
demand and duplication; closure and return. Individual panels or metrics can
be explicitly unavailable. No proxy value is silently substituted.

The provider rejects malformed or oversized artifacts, unknown selectors,
extra fields, traversal-shaped queries, and row-level or sensitive keys. The
minimum reportable cell is 10; a whole breakdown is withheld when a positive
cell falls below it.

## Current source limitation

The 30 July 2025 extract contains no `ATR Received` action status. The ATR
panel is therefore unavailable. Free-text `Replied` remarks are not treated as
equivalent. Structured ATR receipt, owner, acknowledgement, disposal, and
notification timestamps are required before that panel can measure queue
discipline.
