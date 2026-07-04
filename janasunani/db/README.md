# janasunani.db — the OLTP store

System of record for complaints, action history, and exported pipeline outputs.
Engine-swappable via `OLTP_DB_URL` (SQLite locally, Postgres on the CPU box) —
every feature here must work on **both** engines.

## Modules

- `models.py` — SQLAlchemy ORM. `Complaint` / `ActionHistory` model the full
  column set of the source dump in clean snake_case; `PipelinePage` /
  `PipelineDocument` receive the document pipeline's exported outputs. Columns
  are intentionally nullable (except `ticket_no`): historical data is patchy and
  dropping rows on a missing field would lose records.
- `session.py` — async engine + session factory (`create_async_engine` on
  `OLTP_DB_URL`).
- `crud.py` — async CRUD used by ingestion (and later the API): conflict-safe
  inserts, document-status updates, API request tracking.
- `alembic/` — schema migrations. `alembic upgrade head` is part of every
  deploy; upgrade **and** downgrade are verified on both engines.

## The raw→ORM column map lives elsewhere

Source column names (mixed-case, Hungarian-prefixed, occasionally misspelled)
are mapped to ORM fields in exactly one place: the Pydantic `Field` aliases in
[`janasunani/ingestion/schemas.py`](../ingestion/schemas.py). Migration and API
ingestion both validate through those schemas. Don't add a second mapping.

## Engine-portability lessons (learned the hard way, all regression-tested)

- **NUL bytes**: Postgres rejects `0x00` in text (SQLite doesn't). All string
  fields are NUL-stripped by a `mode="before"` validator in the ingestion
  schemas — strip **before** any key lookup, not just before insert.
- **btree entry cap**: Postgres rejects index entries > ~2.7 KB. The
  `action_history` dedup index digests `remark` with `md5(...)` on Postgres
  only, via the `dedup_remark` compiled function in `models.py`
  (`@compiles` dialect branching: `coalesce` on SQLite, `md5(coalesce(...))` on
  Postgres). Alembic revision `09f36c201e97`.
- **Conflict inserts** are dialect-portable through `_dialect_insert`
  (ON CONFLICT DO NOTHING / DO UPDATE per engine).
- **asyncpg is strict** where aiosqlite is lenient (tz-awareness, type
  coercion). If a migration change works on SQLite, run the Postgres-path tests
  (`tests/test_oltp_swap.py`) before calling it done — CI does.

## Dedup semantics

`action_history` rows deduplicate on a **functional unique index** that
coalesces NULL keys, so NULL-keyed duplicates collapse. This is why the
canonical row count is 6,556,171 (not the raw 6,565,323).
