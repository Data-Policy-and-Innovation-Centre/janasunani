# janasunani/serving — the demo API (Phase 10)

FastAPI app the Next.js frontend talks to. The stable module-level app remains
mocked for contract/frontend development, while an opt-in live entry point
mounts the warm Phase 8 processor, real rules routing, and lake history behind
the same endpoints and response shapes.

## Run it

```bash
uv run --extra serving janasunani-api
uv run --extra demo janasunani-api-live
```

Both serve `http://127.0.0.1:8000` by default, with OpenAPI at `/docs`. The
live command's warm processor, strict fail-closed startup, and preflight live in
[`janasunani/inference`](../inference/README.md).
The live command requires the DVC-mirrored categorizer and page-type artifacts
under `models/` (override with `JANASUNANI_MODELS_DIR`) and fails startup rather
than substituting the mock if an artifact, dependency, or model load is missing.
It uses `DatabaseResultStore` only when `OLTP_DB_URL` is explicitly set;
otherwise submitted synthetic results stay in memory. Run the
`live_grievances` Alembic migration before enabling OLTP persistence.

`janasunani-api`'s `/history` defaults to `MockHistory` -- it never serves
real citizen data unless you explicitly set `JANASUNANI_REAL_HISTORY=1`, which
opts it into the lake-backed `LakeHistory`. `janasunani-api-live` always uses
`LakeHistory`, regardless of that flag -- it's a deliberate real-data run.
There is still no auth/redaction on `/history`, so the real-history opt-in
(and the live server generally) is for trusted/local demo runs only.

`JANASUNANI_API_HOST` / `JANASUNANI_API_PORT` / `JANASUNANI_CORS_ORIGINS`
(comma-separated; default `*` — pin to the frontend origin at deploy).

## The contract

| Endpoint | In | Out |
|---|---|---|
| `POST /grievance` (201) | multipart: `text` **xor** `file`, optional `district` | `GrievanceResult` |
| `GET /grievance/{id}` | — | the submitted `GrievanceResult` (404 if unknown) |
| `GET /history` | `q`, `district`, `category`, `limit` (≤100), `offset` | `HistoryPage` (lake column names) |
| `GET /supervisor` | — | `SupervisorDashboard`: recorded aggregate artifacts or explicit unavailable states |
| `GET /health` | — | `{status, processor}` — `mock` or `pipeline` |

**`schemas.py` is the contract.** Field names mirror what already exists —
pipeline artifact DB (`extracted_text`/`redacted_text`/`ocr_model`),
`PIISpan` (entity/start/end over the *extracted* text), lake columns for
history rows — so wire-up is plumbing, not renaming. Changing a field is an
API break; the frontend is built against exactly these shapes, and
`tests/test_serving_api.py` pins them (those tests must pass unchanged after
wire-up).

## Supervisor aggregate artifacts

The supervisor endpoint reads only aggregate artifacts from an explicitly
configured directory. It never queries the lake, grievance text, contact
details, or citizen identifiers at request time. With no configured directory
or no valid artifact, every panel says why it is unavailable instead of
rendering illustrative values.

Set JANASUNANI_SUPERVISOR_FINDINGS_DIR only inside the protected deployment to
the container-visible findings directory. The current seam accepts exactly one
validated closure headline CSV, under either the legacy
closure_finding_summary.csv name or the one-finding
closure_recording_no_action.csv name. It checks the aggregate-only schema and
the reported counts and percentages before returning it, and labels its file
and write timestamp without calling that timestamp source-data freshness.

Duplicate-adjusted workload and the worked spike remain unavailable until their
own validated capability artifacts exist. In particular, the manual
confirmed-duplicates finding is a baseline insight, not a replacement for the
MinHash-backed workload or the three-count spike. Production proxy auth protects
this endpoint with the rest of the API.

## Mock and live modes

| Seam | Default `janasunani-api` | Opt-in `janasunani-api-live` |
|---|---|---|
| processor | `MockGrievanceProcessor` — deterministic canned values; **toy regex "redaction", NOT Presidio** | `PipelineGrievanceProcessor`: pytesseract, page-type gating, Presidio, MuRIL, BART, `DEFAULT_ROUTER`; models warmed once |
| history | `MockHistory`, unless `JANASUNANI_REAL_HISTORY=1` opts into the Parquet lake via `LakeHistory` | Parquet lake via `LakeHistory`, always |
| result store | in-process dict | in-process dict unless explicit `OLTP_DB_URL`, then `live_grievances` via `DatabaseResultStore` |

The live processor returns HTTP 422 for invalid combinations and unsafe or
unusable documents (unsupported/corrupt, blank OCR, quality rejection,
truncation, or no grievance-bearing pages). Unexpected model/runtime failures
remain server errors. DeepSeek and MLflow runtime resolution are not part of
this in-process live command.

Only synthetic demo submissions are allowed until the PII gold evaluation gate
passes. The mock must never serve real citizen submissions.
