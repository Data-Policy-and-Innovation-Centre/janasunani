# janasunani.pipeline — the document-processing pipeline

The DSI document pipeline, refolded. Takes scanned complaint documents and
produces per-page extracted/redacted text and per-document summaries and
categories, in its **own SQLite artifact DB** — deliberately separate from the
OLTP store (see "Artifact DB", below).

## Running it

```bash
uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages format_classifier ocr_extraction

uv run --extra pii janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages pii_tagger

uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages page_type_classifier summarizer

uv run --extra categorizer janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db data/processed/pipeline.sqlite \
  --models models \
  --stages categorizer
```

`--stages` exists because of the dependency split (below). Other key flags:
`--ocr-engine {pytesseract,deepseek}`, `--filter-language`,
`--worker-id/--num-workers` (cross-machine sharding), `--file-list` (curated
subsets/resume).

## The six stages (`STAGE_ORDER` in `pipeline.py`)

| Stage | Writes | Notes |
|---|---|---|
| `format_classifier` | `pages` rows (discovery + page split + format label) | XGBoost/OpenCV; pickle at `models/format_classifier/`. Must run first — it populates `pages`. |
| `ocr_extraction` | `pages.extracted_text` | Backends: `pytesseract` (CPU; needs the `tesseract` binary + `tesseract-lang` for Odia `ori`) or `deepseek` (GPU-only, fails fast without CUDA). Resumable: only NULL-text pages, known-bad pages skipped. |
| `pii_tagger` | `pages.redacted_text` | **Presidio-based rebuild** (the legacy CRF weights died with the DSI Box). Custom Indian recognizers (mobile/Aadhaar/PAN), typed tokens `[NAME]`/`[PHONE]`/…, whole-page analysis (no 512-token window), covers mixed `English, Odia` pages, Indic-digit normalization. Fully in-process. Untuned against gold: Phase 13 scores it per entity and per language. |
| `page_type_classifier` | `pages.page_type` | ViT from an operator override, pinned release, or DVC mirror (`models/page_type_classifier/`). Public HF IDs require explicit development opt-in. The **signal/noise gate**: letters/forms are substance, IDs/bills are noise. |
| `summarizer` | document summaries | Locally pinned BART (`facebook/bart-large-cnn` family). Public HF loading requires explicit development opt-in. Only summarizes target page types — gated on the page-type stage. |
| `categorizer` | `documents.grievance_category` | MuRIL from the DVC mirror (`models/categorizer/`). Feature = grievance text + joined **redacted** page text, mirroring training. English-only. |

Stages run in canonical order regardless of the order given to `--stages`.

**Future full Phase 14 design:** a seventh stage, `spam_duplicate`, inserted between
`pii_tagger` and `page_type_classifier`. It reads **redacted** text, stripping or
down-weighting the typed tokens first (every phone becomes the same `[PHONE]`, so
leaving them in inflates similarity between unrelated documents), writes
`spam_score` / `duplicate_group_id` /
`duplicate_kind`, and gates the summarizer and categorizer the way page-type
already gates the summarizer. It is the first stage needing corpus-level state (a
MinHash/LSH index built from the lake), which the per-run artifact DB does not
provide. See [ROADMAP.md](../../docs/ROADMAP.md) §5.2.

The reduced August implementation deliberately does **not** add that batch
stage. The live serving path runs `spam-v1.1-bounded` only after redaction and
emits a numeric, auditable review/abstention signal. Its narrow content-free
guard skips automated category, summary and route selection in favor of manual
grievance-cell intake; broader low-signal findings remain advisory and continue
through the normal path. An optional checksummed actionability artifact can add
a separate advisory result. The serving seam accepts either the original
five-class reason objective or `artifact_format=2`'s binary
`actionable_vs_officer_review` objective. The current binary development
artifact can request review without inventing a reason label, but it is a
viewed, frontier-adjudicated candidate with no `out_of_scope` support and has
not been promoted in a reviewed release. Neither signal changes submission
status or auto-rejects.

## The dependency split (why `--stages` and lazy imports exist)

DeepSeek OCR pins `transformers==4.46.3`; everything else needs `>=4.57`. The
extras (`pipeline-core` / `pii` / `ocr-deepseek` / `categorizer`) are declared
**mutually conflicting** where their model stacks clash in
`[tool.uv].conflicts`, so an environment holds one side only. `pii` carries
Presidio, spaCy, the English model, and a numpy 2.x floor; it deliberately does
not inherit the legacy `numpy<2` pin of the non-PII pipeline stages. Stages
import their heavy deps **inside** their run functions, and a `--stages` subset imports only
what it runs. A run containing `pii_tagger` needs `--extra pii` in a separate
invocation against the same artifact DB; all other CPU stages use
`--extra pipeline-core`. Deploy pattern: one
`uv run --extra X` env per extra, sequential invocations against the same
artifact DB (`scripts/gpu_smoke.sh` is the working example).

**CI corollary:** CI installs no extras. Any module a test imports at collection
time must be import-light. That's why `ticket.py`, `ocr_quality.py`, and
`pii_eval.py`'s dependencies sit at the light `janasunani.pipeline` level —
and why nothing test-imported may route through `stages/<x>/__init__.py`
(those pull the heavy stacks). We've been bitten (cv2, PIL); keep the pattern.

## Artifact DB → OLTP → lake

`db.py` owns the pipeline SQLite schema (`pages`, `documents`,
`unreadable_pages`). It is per-run working state: resumable (stages select
their own pending work), cheap to throw away, safe to run anywhere. Outputs
reach the OLTP store via `janasunani-export-pipeline` (`export.py`): streaming
batched upserts (ON CONFLICT DO UPDATE), idempotent, re-export refreshes rows.
`janasunani-materialize` then carries them into the Parquet lake. Do **not**
rewrite the pipeline onto the ORM — the split is a decision, not an accident.

## Quality guards and evaluation

- `ocr_quality.py` — repetition-collapse guard for DeepSeek output (its
  signature failure: generation loops and one phrase fills the page). Detector:
  **repeated-trigram share > 0.5** (generalizes the DSI report's top-trigram
  rule, which only catches single-word loops), 20-word floor. Collapsed pages
  store nothing and land in `unreadable_pages`.
- `pii_eval.py` (`janasunani-evaluate-pii`) — scores gold-labeled JSONL
  (external to the repo — never commit citizen text) against the **production
  analyzer**. The pass/fail gate is untyped **COVERAGE** overlap recall vs the
  legacy baseline **0.8056**; typed per-entity metrics are diagnostics.
- `ticket.py` — ticket-number parsing from document paths. Only correct when
  the format classifier's `--input` points exactly at the `documents/` root —
  read its docstring before touching backfill inputs.

## Regression anchor

The `pipeline-sample` DVC stage runs format classification, pytesseract OCR,
and PII over the 2-file DVC-tracked sample into a versioned SQLite. Its
sequential pipeline-core and PII invocations are a cheap regression check that
the split CPU path works through redaction (`dvc repro pipeline-sample`).
