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

Both outputs are local and ignored by Git. On the box, publish into the
repository's `outputs/monitoring/`: `deploy/docker-compose.yml` mounts it
read-only into the API and sets `JANASUNANI_MONITORING_ARTIFACT` to the JSON
inside it. Until a release is there, the Monitoring screen reports it as
unavailable.

## Contract

- `GET /supervisor/monitoring/catalog`
- `GET /supervisor/monitoring?scope_id=<allowlisted-id>&period=<allowlisted-period>`

The catalogue exposes stable IDs, labels, parent scopes, definitions, and
published periods. The dashboard returns every governed panel, once each:
case flow; aging and escalation; transfers and loops; end-to-end journey; ATR queue
discipline; demand and duplication; closure and return; discard reasons and
timing; what the records hold; by district and office. The list is `MonitoringPanelId` in `janasunani/serving/schemas.py`,
mirrored by `PANEL_IDS` in `frontend/lib/monitoring.ts`. Individual panels or metrics can
be explicitly unavailable. No proxy value is silently substituted.
Every recorded metric carries `basis`: `direct` when it counts what the record
contains, `proxy` when it stands in for what its label names (closure wording
for closure quality, a duplicate group for a problem). The list lives in
`PROXY_METRICS` in `janasunani/analytics/monitoring.py`.

The provider rejects malformed or oversized artifacts, unknown selectors,
extra fields, traversal-shaped queries, and row-level or sensitive keys. The
minimum reportable cell is 10; a whole breakdown is withheld when a positive
cell falls below it.

## ATRs and review

The extract has no `ATR Received` status, but the portal records the steps
that matter under other names (checked against the CM Grievance Cell screens,
11 Aug 2026, and the extract's own status counts):

- **The workflow** is `complaints.all_esc_user`, a chain of users stored field
  office first, exactly as the "Define Workflow" list shows it
  (`BDO --> Collector --> CMO`). The first node acts; each later node receives
  the ATR in turn; the last is the office that assigned it.
- **De jure review**: a chain of three or more offices puts at least one office
  between the field and the assigning office, and that office reviews the ATR
  before it goes on. `BDO --> Collector` needs no review;
  `BDO --> Collector --> CMO` does, at the Collector. There is no review flag.
- **`Replied`** is the ATR moving up to the next node.
- **`Reopen` after a `Replied`, before any disposal,** is a reviewer sending
  the ATR back, usually with one of the portal's fixed revert remarks ("Please
  furnish the final ATR", "Required more clarification", ...). After a
  disposal, only a Reopen with one of those remarks counts as a send-back;
  other wording is the case being reopened, often by the citizen. The closure
  panel's `reopened` metric counts both kinds; the ATR panel separates them.

"Required review happened" and "Closed without the required review" are
inferred from the order of events (a second office replied, or a reviewer
sent it back, before closure) and are tagged proxy. The chain is the current
one; an earlier workflow is not kept.

Discard timing is only "before any transfer" or "after a transfer". The
extract has no event for when an officer started work or when an earlier
ticket was found, so the note's other two timings (§2.2) cannot be measured.
Reasons are the eight governed templates in
`janasunani/analytics/findings/discards.py`. Other wording is not read as a
reason, and the "Discards with a recognised reason" metric shows how much of
the discard status those templates explain.

"What the records hold" lists the concept note's §6 fields. A field the
extract holds is the share of FY filings that carry it, with a note when only
part of the field exists. A field it cannot hold is published as unavailable
with a reason that starts "Not recorded" and names the measure recording it
would allow; the frontend keys its "Not recorded" badge on that prefix, so a
figure withheld for a small cell is never shown as a gap in the record. The
unrecordable list is `UNRECORDED_FIELDS` in `janasunani/analytics/monitoring.py`.

"By district and office" is published for department scopes only; other
scopes carry it as unavailable. It holds two `tables` (a recorded-panel field,
`null` elsewhere): open and FY workload by district, and open cases by the
office on each case's latest action. Each rate sits beside the count it is a
rate of. The 12 largest rows are shown and the rest fold into an "Other" row;
count and rate cells under 10 are withheld per cell. Suppression is per cell
only, so a withheld "Other" cell can be differenced from the department
total; add complementary suppression before these tables leave the internal
dashboard.

"Case flow" (panel `flow`, shown first) is the FY cohort as one pipeline:
filed, not discarded, repeats removed, given a workflow, report submitted,
reviewed where required, closed. Each stage keeps only what passed the one
before, so the drop at each step is what left the path there. "Not discarded"
is officer discards; the record has no automated spam filter. "Repeats
removed" keeps one filing per validated identity group and is unavailable,
not skipped silently, until that grouping exists; the later stages then still
count repeats. Stages after the workflow reuse the ATR panel's per-case table
(`atr_cases`), so the two cannot disagree.
