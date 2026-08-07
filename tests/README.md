# tests/ — policy and footguns

**Policy: every feature ships with tests that exercise the real code path**
(real async engine, moto for S3, respx for HTTP, real Presidio analyzer, real
SQLite artifact DBs) — not mocks of our own code. Gate before "done":

```bash
uv run --extra serving --extra pipeline-core pytest
uv run --extra pii pytest tests/test_pii_extra_contract.py tests/test_pii_redaction.py tests/test_redact_grievance.py
uv run ruff check .
```

## Where tests run

- **Locally / CI: yes.** CI (`pipeline.yml`) provides a throwaway Postgres
  service on `127.0.0.1:5433` so the Postgres-path tests (`test_oltp_swap.py`)
  run instead of skipping; it installs **no heavy extras**, so tests needing
  presidio/torch skip there and MUST be run locally before merging.
- **On the CPU box: NEVER against the prod container.** Fixtures create and
  **drop tables**. If tests must run on the box, point them at a throwaway
  `postgres:16` on `127.0.0.1:5433`, never the production `janasunani-oltp`
  container on 5432.

## Footguns encoded here

- `conftest.py` pins `OMP_NUM_THREADS=1` **on macOS only**: importing spaCy
  (blis) before xgboost initializes OpenMP in a way that segfaults arm64 Macs.
  Production stage order loads xgboost first and is unaffected — don't "fix"
  this by reordering production imports.
- CI has no ML extras, so anything a test imports at collection time must be
  import-light. Helpers for tests live in light modules
  (`janasunani/pipeline/ticket.py`, `ocr_quality.py`) — never import through
  `janasunani/pipeline/stages/<x>/__init__.py` in a test.
- Postgres-path tests read `TEST_OLTP_PG_URL` (default
  `postgres:pass@127.0.0.1:5433/janasunani`) and skip when unreachable —
  "green locally" without a local Postgres means those paths ran nowhere.
  Check CI.
- `tests/` is not a package — no relative imports between test files.
