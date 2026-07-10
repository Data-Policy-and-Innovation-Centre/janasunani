# Phase 8C — Processor and Live API Wire-up

## Executor handoff

Delegate this plan with:

> Use the `executor` subagent to implement `docs/plans/PHASE-8C.md` exactly.

The `executor` agent is the single implementation owner. The parent agent may
review the diff and run verification, but must not duplicate implementation.
Start from current `main` on branch `feat/phase8-processor`. Phase 8A
(summarizer wrapper) and Phase 8B (OCR wrapper) are already merged; do not redo
them.

## Goal

Complete Phase 8 by adding an opt-in real inference server behind the existing
FastAPI contract. Keep the module-level API mock and leave the frozen
`GrievanceResult` success schema and frontend types unchanged.

The live path uses in-process pytesseract, locally DVC-mirrored models, public
BART, Presidio, and the existing rules router. DeepSeek and MLflow runtime
resolution remain follow-up work.

## Implementation

### Warm processor

- Add `PipelineGrievanceProcessor` in `janasunani/inference/service.py`,
  implementing the existing `GrievanceProcessor` protocol. Support dependency
  injection for tests; production construction warms the page-type,
  summarizer, and categorizer models once, followed by Presidio.
- Process typed text as:
  `extract → redact/detect PII → language/category → summarize → route`.
- Process documents through the merged `ocr_document()` using the real page
  renderer, pytesseract extractor, and page-type predictor.
- Reject corrupt, unsupported, blank, quality-rejected, and `truncated=True`
  documents with a typed inference-input error.
- Preserve all OCR text in the extraction and redaction response.
- Feed only non-empty class-1 pages (`Letter`, `Form/Application`, `Text Only`)
  to categorization and summarization. Reject documents with no surviving
  grievance-bearing page; never fall back to IDs, bills, or miscellaneous
  pages.
- Build API PII entities from `detect_pii_spans()`, dropping only the internal
  score. Categorize English-compatible text with MuRIL; otherwise return
  `category="Uncategorized"`. Route through `DEFAULT_ROUTER`, yielding
  `rules` or `fallback`, never `mock`.

### Strict construction and serving

- Add strict `build_processor(models_dir=None)` behavior:
  - Resolve `JANASUNANI_MODELS_DIR`, defaulting to `models/`.
  - Require local categorizer and page-type DVC artifacts.
  - Use the existing public BART summarizer source.
  - Fail startup clearly if dependencies or artifacts are missing. Never
    silently substitute `MockGrievanceProcessor`.
- Add `janasunani/inference/serve.py` and the `janasunani-api-live` entry point.
- Set the macOS `OMP_NUM_THREADS=1` guard before heavy imports.
- Build the app with the real processor and `LakeHistory`.
- Use `DatabaseResultStore` only when `OLTP_DB_URL` is explicitly set;
  otherwise use `InMemoryResultStore`.
- Convert typed inference-input failures to HTTP 422. Unexpected model or
  runtime failures remain server errors.
- Leave `janasunani.serving.api:app` unchanged and mock by default.
- Do not add a combined self-referential `demo` extra. Run the live server
  with:

  ```bash
  uv run --extra serving --extra pipeline-core --extra categorizer janasunani-api-live
  ```

### Documentation

- Update `docs/ROADMAP.md` and the serving documentation to mark Phase 8A and
  8B complete, describe 8C accurately, and remove the stale “Phase 8 not
  started” language.

## Public interfaces

- New `PipelineGrievanceProcessor.process(...) -> GrievanceResult`.
- New strict `build_processor(models_dir=None)`.
- New `janasunani-api-live` CLI.
- No changes to endpoint paths, success schemas, frontend types, or the
  default mock API.
- Keep `OcrResult.truncated` internal. Reject incomplete documents instead of
  adding an API field.

## Tests and acceptance

- Add dependency-injected processor tests covering:
  - Typed-text extraction, PII mapping, redacted classifier input, summary,
    language, and real routing.
  - PDF extraction metadata and class-1 page gating.
  - Proof that irrelevant pages never reach categorization or summarization.
  - Rejection of invalid input combinations, blank OCR, corrupt files,
    truncated documents, and documents without relevant pages.
  - Non-English `Uncategorized` behavior and routing fallback.
  - Model components constructed once and reused.
- Add live-builder tests proving missing artifacts fail closed and explicit
  `OLTP_DB_URL` controls persistence.
- Keep `tests/test_serving_api.py` unchanged and green.
- Add an opt-in real-model smoke test guarded by
  `JANASUNANI_RUN_MODEL_SMOKE` and local model availability.
- Run the full gates:

  ```bash
  uv run --extra serving --extra pipeline-core --extra categorizer pytest
  uv run ruff check .
  ```

- Manually start the live CLI and submit one synthetic English text and one
  synthetic PDF. Both must return non-empty real inference results with
  `routing.method != "mock"` and render unchanged in the existing frontend.

## Constraints and assumptions

- DeepSeek remains out of scope because it requires a separate process.
- MLflow remains provenance and registration infrastructure; Phase 8C loads
  local DVC artifacts directly.
- The first real-model demo targets English. Unsupported languages safely
  return `Uncategorized`.
- Only synthetic demo submissions are allowed until the PII gold evaluation
  gate passes.
- Database persistence requires the `live_grievances` Alembic migration before
  starting with `OLTP_DB_URL`.
- Follow all repository `AGENTS.md` safety rules, especially the prohibition on
  inspecting `data/` and on running tests against production Postgres.
