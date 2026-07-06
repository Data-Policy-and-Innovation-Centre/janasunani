# janasunani/serving — the demo API (Phase 10)

FastAPI app the Next.js frontend talks to. Built **skeleton-first** (roadmap
Week 3): the full endpoint surface with a **mocked processor** ships before
Phases 8–9, so the frontend gets weeks of iteration against a stable contract
instead of a cramped tail. The wire-up later swaps the mock for the real warm
inference + routing behind the same seams — endpoints and shapes unchanged.

## Run it

```bash
uv run --extra serving janasunani-api      # http://127.0.0.1:8000, docs at /docs
```

`JANASUNANI_API_HOST` / `JANASUNANI_API_PORT` / `JANASUNANI_CORS_ORIGINS`
(comma-separated; default `*` — pin to the frontend origin at deploy).

## The contract

| Endpoint | In | Out |
|---|---|---|
| `POST /grievance` (201) | multipart: `text` **xor** `file`, optional `district` | `GrievanceResult` |
| `GET /grievance/{id}` | — | the submitted `GrievanceResult` (404 if unknown) |
| `GET /history` | `q`, `district`, `category`, `limit` (≤100), `offset` | `HistoryPage` (lake column names) |
| `GET /health` | — | `{status, processor}` — `processor: "mock"` until wire-up |

**`schemas.py` is the contract.** Field names mirror what already exists —
pipeline artifact DB (`extracted_text`/`redacted_text`/`ocr_model`),
`PIISpan` (entity/start/end over the *extracted* text), lake columns for
history rows — so wire-up is plumbing, not renaming. Changing a field is an
API break; the frontend is built against exactly these shapes, and
`tests/test_serving_api.py` pins them (those tests must pass unchanged after
wire-up).

## What's mock vs real (as of the skeleton)

| Seam | Skeleton | Wire-up (Phases 8–9) |
|---|---|---|
| `processor.GrievanceProcessor` | `MockGrievanceProcessor` — deterministic canned values; **toy regex "redaction", NOT Presidio** | warm `GrievanceProcessor` (models loaded once; text skips OCR; `ocr_engine` per env) + hybrid router |
| `history.HistoryProvider` | `MockHistory` — 120 seeded fake rows, real filter semantics | Parquet lake via `olap/lake.py` (historical only — freshness decision in ROADMAP open items) |
| result store | in-process dict (dies with the process) | `live_grievances` OLTP table via `crud.py` |

The mock must never serve real citizen submissions — it exists so the
frontend and API tests are model-free.
