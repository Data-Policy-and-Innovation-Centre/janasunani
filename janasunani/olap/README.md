# janasunani.olap — the Parquet lake

The read-optimized downstream copy of the OLTP store: one Parquet file per
table in `data/interim/`, DVC-tracked. Analytics, ML training, and the demo's
history browse read **this**, never OLTP.

## Modules

- `materialize.py` (`janasunani-materialize`) — DuckDB attaches the OLTP DB
  directly via its `sqlite`/`postgres` scanner and `COPY`s each table to
  Parquet. No ORM, no hand-rolled export, engine-agnostic off `OLTP_DB_URL`.
  Full scale runs in ~25 s. Currently materializes `complaints`,
  `action_history`, `pages`, `documents`.
- `lake.py` — read helpers: `query(sql)` (DuckDB over the Parquet files) and
  `read(table)` (whole table as a Polars DataFrame).

Everything analytical built *on* the lake — the governed SQL marts and the
findings that read them — lives in
[`janasunani/analytics`](../analytics/README.md), not here. This module stays
the plumbing.

## Why "lake"

It's the cheap, schema-on-read, file-based analytical layer — the OLTP store
stays small and transactional up front, and everything downstream (dataframes,
training sets, history endpoints) reads columnar files. Refreshing it is a
one-command re-materialization, which is also the freshness model: **live
grievances appear in OLTP immediately but reach the lake only on the next
materialize** (nightly/one-off). The demo reads `GET /grievance/{id}` from OLTP
and `GET /history` from the lake by design.

## DVC interaction

`materialize` is a `dvc.yaml` stage whose dep is the **SQLite** OLTP path, so
`dvc repro` only works in the local-SQLite setup. Against Postgres (the CPU
box), run `janasunani-materialize` directly, then `dvc commit` + `dvc push` the
Parquet outs. DuckDB's `postgres` extension `INSTALL`s at runtime — needs
egress on first use.
