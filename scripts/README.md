# scripts/ — operational one-offs

Jobs whose effects live in external systems (OLTP, S3, a GPU box), so they run
as scripts rather than DVC stages. Each is safe to re-run.

| Script | What it does |
|---|---|
| `migrate.sh` | Cold-start migration: ephemeral MySQL → restore dump → load OLTP ([migration/README](../janasunani/migration/README.md)). |
| `gpu_smoke.sh` | DeepSeek OCR smoke on the GPU box ([terraform/README](../deploy/terraform/README.md), "GPU box"). |
| `sample_english_complaints.py` | Sample English complaints (subject + document) into a zip (below). |
| `bootstrap_pii_gold.py` | OCR a bundle + pre-annotate with the production analyzer → draft gold JSONL for the PII eval (below). |
| `evaluate_pii_redaction.py` | Thin wrapper over `janasunani-evaluate-pii` (the PII gold-JSONL gate). |
| `setup.sh` | Workspace setup backing `make setup` (uv, rclone, AWS CLI, hooks). |
| `infra_status.py` | Read-only health pass over the cloud infra, behind `make infra` (below). |

## infra_status.py

One pass answering "is anything wrong right now" across the CPU box (which
holds production Postgres, the models, the lake and the `pg_dump` target), the
on-demand GPU box, the deployed stack, and the backups.

```bash
make infra                                          # AWS + box over SSH
make infra SITE=52-66-116-80.nip.io SG_ID=sg-0abc    # + health + SSH exposure
make infra ARGS="--no-ssh"                          # AWS only
make infra ARGS="--json"                            # machine-readable
```

**Read-only by construction.** Every command is a query (`aws ec2 describe-*`,
`aws s3api list-objects-v2`, `df`, `docker ps`, an unauthenticated
`GET /api/health`). It never starts, stops, deploys or prunes anything, and a
test asserts no mutating invocation appears in the source. This points at the
box holding real citizen data, so a status tool that *can* mutate is one that
eventually will, at the worst moment. It also never prints the OLTP URL, which
carries a password.

Thresholds come from the repo, not from taste:

| Check | Threshold | Source |
|---|---|---|
| Disk free | CRIT < 20 GiB | `deploy.sh`'s own `MIN_FREE_KIB`; that volume also holds prod Postgres |
| Backup age | WARN > 26 h, CRIT > 48 h | nightly cadence, §5 of [DEPLOY.md](../docs/DEPLOY.md). The cron lives only on the box (#31), so a rebuilt box loses it silently |
| Port 22 ingress | WARN on any, CRIT on `0.0.0.0/0` | the deploy opens 22 to the runner's /32 and revokes it; a leftover rule is #32 |
| GPU box running | WARN | on-demand and billed hourly |
| `processor=mock` | WARN | site is up and serving canned results, not the real pipeline |

Exit code is 0 unless something is CRIT, so it is cron-safe. Two deliberate
behaviours: skipped or unreachable checks report as `--`, never as healthy, and
a run where *nothing* was checked prints `NOTHING CHECKED` rather than
`all clear`. AWS and SSH failures degrade to "not checked" instead of aborting
the run, so one missing credential does not hide the checks that did work.

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

## bootstrap_pii_gold.py

Turns an English complaints bundle into labeling material for the PII eval
(the ROADMAP gate: beat the legacy 80.56% any-overlap coverage before the
backfill ships redacted pages).

```bash
uv run --extra pipeline-core python scripts/bootstrap_pii_gold.py \
    [--bundle data/output/english_complaints_sample.zip] \
    [--out data/output/pii_gold_draft.jsonl] [--max-pages-per-doc N]
```

For every document page it runs pytesseract OCR and pre-annotates the text
with the **production** Presidio analyzer, writing two files:

- `pii_gold_draft.jsonl` — the machine-editable draft, in exactly the format
  `janasunani-evaluate-pii --gold` consumes;
- `pii_gold_draft.review.txt` — the same pages with spans marked inline
  (`⟦NAME:Ramesh Kumar⟧`) for a fast human read.

**The draft is not gold.** It contains what the analyzer already finds, so an
unedited draft scores ~100% by construction. The human pass *is* the
measurement: **add** the PII the analyzer missed (these become the recall
misses), **delete** false hits, **fix** boundaries/labels. Then:

```bash
uv run --extra pipeline-core janasunani-evaluate-pii --gold <corrected.jsonl>
```

### Gold-file lifecycle

- **Draft** (`data/output/pii_gold_draft.jsonl`): regenerable by this script —
  not tracked anywhere, gitignored, safe to delete.
- **Corrected gold** (`data/external/pii_gold.jsonl`): irreplaceable human
  labeling work — **DVC-tracked** so it survives any one machine. Only the
  `.dvc` pointer (md5 + path) enters git; the content goes to the private,
  IAM-scoped S3 remote — the same posture as the raw dump, which also holds
  citizen PII. Never commit the file itself (the `no-raw-data-in-git` CI
  guard blocks it; pointers are exempt). Promote after the label pass:

  ```bash
  mkdir -p data/external && mv <corrected> data/external/pii_gold.jsonl
  dvc add data/external/pii_gold.jsonl && dvc push
  git add data/external/pii_gold.jsonl.dvc   # commit the pointer
  ```

  Reproduce the eval anywhere with bucket access:
  `dvc pull` → `janasunani-evaluate-pii --gold data/external/pii_gold.jsonl`.
  When the gold set grows, `dvc add` again — each eval result stays diffable
  against the exact gold revision that produced it.

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
