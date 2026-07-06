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

Builds a small, clean evaluation bundle: **N complaints (default 10) whose
grievance subject is written in English**, with their scanned documents and
their full metadata rows, in a single zip.

### Run it

```bash
# needs: the lake locally (dvc pull / janasunani-materialize) + AWS credentials
uv run --with langdetect python scripts/sample_english_complaints.py \
    --n 10 --seed 7 --out data/output/english_complaints_sample.zip
```

All flags optional (defaults shown). langdetect isn't a base dependency —
`--with langdetect` supplies it ad hoc. Same seed ⇒ same sample.

### What lands in the zip

```
english_complaints_sample.zip
├── complaints.parquet          # the N sampled rows, all lake columns
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
   qualify — the expensive checks only run on rows actually considered:
   - **English gate** (three tests, in order):
     no Odia codepoints → **≥ 2 common English stopwords** (this is what
     rejects *romanized* Odia, which langdetect often mislabels) →
     langdetect says `en` (seeded, so deterministic).
   - **Document gate**: the ticket has ≥ 1 object in the S3 documents bucket
     under `{ticket_no}_complaint_` with storage class **STANDARD** — parts of
     the bucket are GLACIER-archived and can't be downloaded directly, so
     those tickets are skipped rather than failed on.
3. Download the qualifying documents and write the zip (parquet + documents).

Exits non-zero if fewer than N complaints qualify, telling you how many
candidates were checked. The language gate has its own tests
(`tests/test_sample_english_complaints.py`).
