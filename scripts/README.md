# scripts/ — operational one-offs

Jobs whose effects live in external systems (OLTP, S3, a GPU box), so they run
as scripts rather than DVC stages. Each is safe to re-run.

| Script | What it does |
|---|---|
| `migrate.sh` | Cold-start migration: ephemeral MySQL → restore dump → load OLTP ([migration/README](../janasunani/migration/README.md)). |
| `gpu_smoke.sh` | DeepSeek OCR smoke on the GPU box ([terraform/README](../deploy/terraform/README.md), "GPU box"). |
| `sample_english_complaints.py` | Sample English-subject complaints + their documents into a zip (below). |
| `evaluate_pii_redaction.py` | Thin wrapper over `janasunani-evaluate-pii` (the PII gold-JSONL gate). |
| `setup.sh` | Workspace setup backing `make setup` (uv, rclone, AWS CLI, hooks). |

## sample_english_complaints.py

Builds a small, clean evaluation bundle: **N complaints (default 10) that are
English on both sides** — the grievance subject *and* the scanned document —
with the documents and full metadata rows in a single zip. Documents that are
nothing but PII (an Aadhaar or voter ID, a bill) are dropped.

### Run it

```bash
# needs: the lake locally (dvc pull / janasunani-materialize), the model
# mirrors under models/, tesseract (+ ori traineddata), AWS credentials
uv run --extra pipeline-core python scripts/sample_english_complaints.py \
    --n 10 --seed 7 --out data/output/english_complaints_sample.zip
```

All flags optional (defaults shown). The document gates run pipeline models,
hence the `pipeline-core` env. Same seed ⇒ same sample.

### What lands in the zip

```
english_complaints_sample.zip
├── complaints.parquet          # the N sampled rows, all lake columns, plus
│                               # gate evidence: doc_languages, doc_page_types,
│                               # doc_english_share
└── documents/
    ├── CMO2022142102_complaint_20250919_012958.pdf
    └── ...                     # S3 key paths preserved — nested tickets keep
                                # the directory structure the pipeline's
                                # ticket parsing requires
```

### How it selects

1. Load `data/interim/complaints.parquet`; keep rows with a subject
   (≥ 30 chars) and a `document_url`.
2. Shuffle deterministically (`--seed`), then walk candidates lazily until N
   qualify — cheapest gate first, so the model work only runs on plausible
   rows:
   - **Subject gate** (three tests, in order): no Odia codepoints →
     **≥ 2 common English stopwords** (this is what rejects *romanized* Odia,
     which langdetect often mislabels) → langdetect says `en` (seeded, so
     deterministic).
   - **Availability gate**: the ticket has ≥ 1 object in the S3 documents
     bucket under `{ticket_no}_complaint_` with storage class **STANDARD** —
     parts of the bucket are GLACIER-archived and can't be downloaded
     directly, so those tickets are skipped rather than failed on.
   - **Document gates** (the pipeline's own components, on the first 5
     pages). Language: per-page **tesseract dominance** — eng vs ori
     confidence-weighted word counts via the pipeline's `perform_ocr` (the
     raw signal, deliberately not the ~76%-accuracy format-classifier label,
     which let Odia pages through). **Any Odia-dominant page rejects the
     document**; pages with too little confident text either way count as
     `Sparse` and are tolerated, but ≥ 1 confidently-English page is
     required. Substance: the page-type ViT must find **≥ 1 signal-class
     page** (Letter / Form/Application / Text Only). A document whose pages
     are all Identification / Bills / Misc — i.e. pure PII like an Aadhaar —
     has no signal page and is dropped, with the reason logged.
3. Write the zip: the qualifying documents + the metadata parquet, which
   carries the per-document gate evidence for auditability.

Exits non-zero if fewer than N complaints qualify, telling you how many
candidates were checked. Both the subject gate and the document verdict logic
have tests (`tests/test_sample_english_complaints.py`).

### Debugging a run

Every decision is logged: model load times, a progress heartbeat every 100
candidates (checked/picked/reject counters), per-ticket S3 keys, per-page
`language`/`page_type` verdicts with timings (DEBUG), drop reasons per
document, and an end-of-run breakdown (subject rejects / no-STANDARD-doc
skips / documents dropped by gates). Per-candidate subject rejections are
high-volume, so they sit at TRACE:

```bash
LOGURU_LEVEL=TRACE uv run --extra pipeline-core python scripts/sample_english_complaints.py
```
