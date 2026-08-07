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
| `GET /health` | — | `{status, processor}` — `mock` or `pipeline` |

**`schemas.py` is the contract.** Field names mirror what already exists —
pipeline artifact DB (`extracted_text`/`redacted_text`/`ocr_model`),
`PIISpan` (entity/start/end over the *extracted* text), lake columns for
history rows, and Phase 14's advisory triage states. `RoutingResult` carries
support and concentration when `method="learned"`; other routing methods
cannot claim empirical evidence. `TriageResult` keeps resubmissions, campaigns,
and low-signal review separate, including an explicit scorer abstention. Its
`duplicate_review` distinguishes unindexed, unavailable, and abstained
lookups from a verified no-match, so absent evidence is never presented as a
negative finding. None of these fields rejects a grievance. Changing a field is an API break; the
frontend is built against exactly these shapes, and `tests/test_serving_api.py`
plus `tests/test_serving_triage_contract.py` pin them.

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

The mock emits deterministic illustrative resubmission and campaign states,
but always abstains from low-signal review so a fixture cannot look like live
evidence.
The live processor calls its advisory triage seam only after PII redaction.
Until the Phase 14 matcher is wired, it returns
`duplicate_review.decision="not_indexed"`. Low-signal review records only the
existing repetition-collapse observation and returns `spam.decision="abstained"`;
no numeric score or review flag is enabled before redacted human-adjudicated
validation. If an eventual provider is unavailable, the submission proceeds
with explicit unavailable/abstained states.

Only synthetic demo submissions are allowed until the PII gold evaluation gate
passes. The mock must never serve real citizen submissions.
