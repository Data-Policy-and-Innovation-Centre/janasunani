# janasunani/inference — the warm live processor (Phase 8)

Single-grievance, in-process inference behind the **frozen serving contract**.
Where `pipeline/` runs the models in batch through the artifact DB one page-row
at a time, this package orchestrates **one uploaded grievance at a time** with
no DB in the middle, warms every model **once**, and returns the exact
`GrievanceResult` shape `serving/` already promises — so the frontend consumes
real output with zero changes.

It is deliberately **import-light**: nothing here pulls torch/transformers at
import time, so the default mock API (`janasunani-api`) still runs model-free.
Heavy dependencies are loaded lazily inside `build_processor`, and the
single-item wrappers take their OCR/model calls as **injected callables** — so
`ocr.py`/`service.py` import and unit-test cleanly with no tesseract, poppler,
or ML libraries installed.

## Modules

| File | What it is |
|---|---|
| `service.py` | `PipelineGrievanceProcessor` (the `GrievanceProcessor` seam), `build_processor()` (strict warm start), `preflight()` (weight-free readiness) |
| `ocr.py` | `ocr_document()` — one document's raw bytes → per-page text; all rendering/OCR/page-type injected |
| `serve.py` | opt-in entry points: `main` (`janasunani-api-live`), `preflight_main` (`janasunani-demo-preflight`), `create_live_app` |

## Run it

```bash
# fast, weight-free readiness check (models + OCR binaries + which store)
uv run --extra demo janasunani-demo-preflight

# warm start the live API (fails closed if anything is missing)
uv run --extra demo janasunani-api-live
```

Serves `http://127.0.0.1:8000` (`JANASUNANI_API_HOST`/`JANASUNANI_API_PORT`),
OpenAPI at `/docs`. The `Makefile` wraps this end-to-end: `make preflight`,
`make api`, `make up` (API + frontend), `make down`. Full runbook in
[`docs/DEMO.md`](../../docs/DEMO.md); the API contract lives in
[`janasunani/serving/README.md`](../serving/README.md).

## What `process()` does

One typed grievance **xor** one uploaded document per call (validated up front —
providing both, neither, blank text, or an unsupported suffix is a client
error). Then:

1. **Extraction** — text path: `extracted_text = text`, `source="text"`.
   Document path: `ocr_document()` renders + OCRs each page (cap
   `DEFAULT_MAX_PAGES = 50`; a longer doc comes back `truncated=True` and is
   rejected rather than silently partial), `source="document"`,
   `ocr_model="pytesseract"`, `pages=N`.
2. **Page-type gating (document path only)** — only grievance-bearing pages
   (page-type class 1, via `PAGE_TYPE_CLASS_BY_LABEL`) feed the models;
   identification/bill/misc pages are dropped so they never reach redaction or
   classification.
3. **Redaction** — Presidio `redact_text` + `detect_pii_spans` over the
   extracted text; spans map `PIISpan(entity,start,end,score)` →
   `PIIEntity(entity,start,end)`. On the document path each selected page is
   redacted **before** being joined for the models.
4. **Advisory triage** — runs only over the **redacted** text. The bounded
   provider is the default; an optional checksummed binary actionability model
   can ask for officer review. Neither rejects or changes routing. Content-free
   abuse suppresses generated category/summary output instead of inventing an
   answer.
5. **Classification & summary** — run over the **redacted** text (PII never
   reaches the models). `langdetect` sets `language`; the same English gate the
   categorizer uses decides whether MuRIL + BART run, or the non-English
   fallback (`category="Uncategorized"`, a fixed "summary unavailable" string)
   applies — BART is only warmed/validated for the English demo target and
   would otherwise hallucinate over unsupported input.
6. **Routing** — the selected provider returns `RoutingResult` straight into
   the frozen schema. Default routing tries the empirical crosswalk before
   mappings and generic fallback. `JANASUNANI_ROUTER=incidence` opts into a
   checksummed empirical-Bayes artifact and safely falls through when it cannot
   provide evidence. Both learned paths describe historical destination, not
   correct jurisdiction or outcomes.

## Strict warm start (`build_processor`) + `preflight`

`build_processor` is **fail-closed**: it hard-requires the locally mirrored
model artifacts and the OCR system binaries, then imports and warms page-type →
summarizer → categorizer → Presidio → OCR/router. Any missing artifact, absent
binary, failed local BART load, or Presidio init error **aborts startup** —
it never substitutes the mock.

Models resolve in the same order used by preflight: component-specific operator
override → active immutable release manifest → local DVC mirror. Runtime imports
no MLflow client and performs no model-network lookup. Remote model IDs require
the explicit `JANASUNANI_ALLOW_REMOTE_MODELS=1` development opt-in.

`preflight()` reports the **same** requirements without loading a single weight
(milliseconds, not minutes), so an operator can confirm a box is demo-ready
before the multi-minute warm start. The mandatory-artifact list
(`_required_model_files`) is a **single source of truth** shared by both, so the
fast check can never disagree with what real startup loads. Requirements:

| Component | Artifacts (any-of where listed) |
|---|---|
| categorizer | `config.json`; `model.safetensors` \| `pytorch_model.bin`; `tokenizer.json` \| `vocab.txt`; `label_encoder_…pkl` |
| page-type | `config.json`; `model.safetensors` \| `pytorch_model.bin`; `preprocessor_config.json` |
| summarizer | `config.json`; `model.safetensors` \| `pytorch_model.bin`; tokenizer vocabulary/config |
| OCR binaries | `tesseract` (resolved as the backend does — `TESSERACT_CMD`/PATH/bundled), `pdfinfo` + `pdftoppm` (poppler) |

Models root: `JANASUNANI_MODELS_DIR`, else the package `MODELS_DIR`. The Odia
(`ori`) traineddata is **not** auto-checked (tesseract's `--list-langs` output
isn't portable enough) — confirm it manually post-install.

## Store selection & history

`create_live_app` always uses lake-backed `LakeHistory` for `/history` (a
deliberate real-data run), and selects the result store via the same `Settings`
layer the rest of the app uses:

| `OLTP_DB_URL` | Result store |
|---|---|
| explicitly set (≠ the built-in default) | `DatabaseResultStore` → persists to `live_grievances` (run the Alembic migration first) |
| unset / default | `InMemoryResultStore` — results lost on restart |

`preflight_main` reports which store `main` would pick **without printing the
URL** (it can carry a DB password).

## Error mapping

`process()` raises `InferenceInputError` (→ **HTTP 422**) for input the client
can fix: wrong field combination, blank/unsupported/corrupt or empty document,
blank OCR output, OCR quality rejection, truncation past the page cap, or a
document with no grievance-bearing pages. A page-type **model** bug caught
mid-OCR is wrapped (`_PageTypeModelError`) so it is never mistaken for a corrupt
document — genuine model/runtime failures propagate as **5xx**.

Preflight also reports advisory model-release, router, triage, lake and OLTP
checks. Missing/shadowed release state is a warning normally and a failure under
`--strict`; no operator override path is disclosed.

## macOS guards (`serve.py`)

On `darwin`, before any inference import: `OMP_NUM_THREADS=1` (torch/xgboost/
spaCy OpenMP collision in one arm64 process) and `HF_HUB_DISABLE_XET=1` (a guard
for the explicit remote-model development mode). The Linux CPU box is untouched.

## Tests

- `tests/test_inference_service.py` — real-code-path field assembly, PII
  mapping, page-type gating, routing, error mapping (heavy internals injected).
- `tests/test_inference_ocr.py` — `ocr_document` join/pages/labels, truncation,
  quality rejection, temp-file cleanup (fake render/extract fns).
- `tests/test_inference_live_builder.py` — `build_processor`/`preflight` drift
  guards: both consume the same required-file list; a partial mirror fails closed.
- `tests/test_inference_model_smoke.py` — opt-in real end-to-end (guarded by
  extras + models present).
