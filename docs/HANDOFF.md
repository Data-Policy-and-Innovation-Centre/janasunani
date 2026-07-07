# Handoff — state as of 2026-07-07

Written by the outgoing agent (Claude Fable, subscription window ended) for
whichever agent picks this up next (Codex / Claude Opus / a human). Assume the
reader has **zero context** beyond this repo.

**Read in this order:** this file → [ARCHITECTURE.md](ARCHITECTURE.md) (system
overview) → [ROADMAP.md](ROADMAP.md) (sequencing source of truth — keep it in
sync with reality, it always has been) → the per-package READMEs as needed.
The root [README](../README.md) has runnable commands for every component.

## Where the project stands (everything below is merged to `main`)

- **Foundation (Phases 0–5)**: migration (1,371,288 complaints / 6,556,171
  action rows), Parquet lake, S3 ingestion, and the six-stage document
  pipeline are built and running in the cloud (CPU box). GPU box was shaken
  down for real on 2026-07-04 and destroyed after (it's a create/destroy
  toggle, see below).
- **PII eval tooling** (PR #11): `scripts/sample_english_complaints.py`
  (English-both-sides bundles), `scripts/bootstrap_pii_gold.py` (OCR +
  pre-annotate → labeling draft), `janasunani-evaluate-pii` (the gate).
  The gate: **beat the legacy 80.56% any-overlap coverage** before the
  backfill ships redacted pages. See `scripts/README.md` §Gold-file lifecycle.
- **Phase 10 API skeleton** (PR #12): `janasunani/serving/` — full endpoint
  surface with a mocked processor. **`serving/schemas.py` is the frozen
  contract**; `tests/test_serving_api.py` pins it and must pass unchanged
  after the Phase 8/9 wire-up. `uv run --extra serving janasunani-api` serves
  it. See `janasunani/serving/README.md` for the mock-vs-real seam table.
- **Sampler corrupt-file fix** (PR #13): the documents bucket contains
  corrupt uploads; `assess()` rejects instead of crashing.
- Gate status: full suite green
  (`uv run --extra serving --extra pipeline-core pytest`, 84 tests), ruff
  clean, CI green (CI runs the serving contract tests via `--extra serving`).

## Artifacts on the maintainer's machine only (gitignored, regenerable)

- `data/output/english_complaints_sample_n50.zip` — 50 English complaints +
  their documents (seed 7, so any smaller same-seed sample is a prefix).
- `data/output/pii_gold_draft_n50.jsonl` — **85 pre-annotated pages, 573
  draft spans** (451 NAME / 73 PHONE / 34 EMAIL / 15 AADHAAR) +
  `pii_gold_draft_n50.review.txt` (inline ⟦ENTITY:text⟧ render for reading).

Regenerate anywhere with lake + models + AWS creds:

```bash
uv run --extra pipeline-core python scripts/sample_english_complaints.py --n 50 --seed 7 --out data/output/english_complaints_sample_n50.zip
uv run --extra pipeline-core python scripts/bootstrap_pii_gold.py --bundle data/output/english_complaints_sample_n50.zip --out data/output/pii_gold_draft_n50.jsonl
```

## The queue, in order

1. **Human labeling pass** (the maintainer, manually — do not automate the
   labels): correct `pii_gold_draft_n50.jsonl` (ADD missed PII, DELETE false
   hits, FIX boundaries — the draft scores ~100% by construction, the human
   pass IS the measurement). Then
   `uv run --extra pipeline-core janasunani-evaluate-pii --gold <corrected>`
   vs the 0.8056 baseline, and promote:
   `mv <corrected> data/external/pii_gold.jsonl && dvc add data/external/pii_gold.jsonl && dvc push`,
   commit only the `.dvc` pointer.
2. **Next.js scaffold (Phase 11)** — unblocked now: build against the PR #12
   contract with the mock API running locally. Submit page (text/upload →
   staged result cards) + history browse. `NEXT_PUBLIC_API_URL`, client-side
   fetch only, no auth/SSR.
3. **Sample backfill** (~200 docs; needs the maintainer's GPU-billing go and
   PII gate pass first): GPU box up → DeepSeek at `--filter-language English`,
   pytesseract+`ori` for Odia (DeepSeek is English-only in practice — Odia
   comes out script-confused) → `janasunani-export-pipeline` → OLTP →
   re-materialize.
4. **MLflow slim**: local backend on the CPU box, artifacts to S3, register
   categorizer/summarizer/page-type versions tagged with their DVC hashes.
5. **Phases 8/9 wire-up** behind the serving seams (single-item inference,
   rules router first, learned router only after E2E works).
6. Parked: ingestion smoke (Janasunani API credentials unavailable).

Demo deadline: **end of July 2026** (Next.js demo, stakeholder dry run).

## Cloud state right now

- **CPU box** (always on): `i-0ef24e15a80ba7128`, EIP `52.66.116.80`,
  ap-south-1. Runs the prod Postgres in docker-compose (the migrated
  production data lives in its volume) + nightly `pg_dump` → S3 cron.
  SSH with agent forwarding; see `deploy/terraform/README.md`.
- **GPU box**: currently **destroyed** (by design). Create with
  `gpu_box_count = 1` + `terraform apply` (~$1/hr, g6.xlarge, **ap-south-1a
  only** — g6 isn't offered in 1c). Push outputs off the box before
  teardown; the root volume dies with it.
- `admin_cidr` in the Terraform variables pins SSH to the maintainer's home
  IP, **which rotates every few days**. If SSH times out: `curl -4
  ifconfig.me`, update the var, apply. This has struck three times.

## Hard rules — violating these loses production data or leaks citizen PII

1. **Never** `docker compose down -v` on the CPU box — the volume holds the
   migrated production OLTP data.
2. **Never** run pytest against the box's prod Postgres — fixtures DROP
   TABLES. Tests use a throwaway Postgres on `127.0.0.1:5433` only.
3. Citizen text **never** goes to an external API for redaction/processing —
   redaction is in-process (Presidio).
4. Gold files and drafts hold real citizen text — **never in git**. Drafts
   live in gitignored `data/output/`; corrected gold is DVC-tracked at
   `data/external/pii_gold.jsonl` (pointer in git, bytes in the private S3
   remote). `.gitignore` + the `no-raw-data-in-git` CI guard both enforce it.
5. Models load only from **our DVC mirrors** under `models/` (or large public
   repos) — never from DSI accounts (the team disbanded; their assets vanish).
6. Postgres password exists only in the box's chmod-600 `.env` / gitignored
   `deploy/.env`. Terraform state/tfvars/keys stay local (pre-commit + CI
   guards).
7. Every feature ships with real-code-path pytest, run green before "done",
   plus `ruff check`.
8. Treat `data/` as sensitive per `AGENTS.md` — don't browse it beyond the
   task at hand.

## Gotchas that already cost real time (don't rediscover)

- **uv extras conflict**: `ocr-deepseek` (transformers 4.46) can never share
  an env with `pipeline-core`/`categorizer` (transformers ≥4.57). One
  `uv run --extra X` env per extra, same SQLite artifact DB between them.
- **macOS**: mixing xgboost and torch in one process hangs/segfaults OpenMP —
  `OMP_NUM_THREADS=1` before either loads (see `tests/conftest.py` and the
  sampler's top-of-file guard).
- **fastapi imports transitively via mlflow** — a `pytest.importorskip("fastapi")`
  guard passes in envs that still lack `python-multipart`; guard on the
  actually-optional package.
- pdf2image exceptions subclass plain `Exception`, not `OSError`/`ValueError`.
- The documents bucket has **GLACIER-archived parts** (filter
  `StorageClass == "STANDARD"`) and **corrupt uploads** (bad bytes behind
  .jpeg/.pdf names).
- Language gating: langdetect and the format classifier (~76% acc) both pass
  Odia as English — use raw tesseract eng-vs-ori confidence dominance
  (`perform_ocr`), and reject on ANY Odia-dominant page.
- DeepSeek-OCR: `trust_remote_code` imports torchvision unconditionally;
  watch repetition collapse (guard: repeated-trigram share > 0.5 in
  `janasunani/pipeline/ocr_quality.py`).
- DVC `materialize` stage deps on the SQLite path — with Postgres OLTP run
  `janasunani-materialize` directly and `dvc commit`.

## Legacy baselines (the only surviving eval record — DSI report PDF)

PII coverage **80.56%** overlap / 50% exact · MuRIL categorizer 0.7104 acc ·
page-type ViT 0.67 · format classifier 75.71% · DeepSeek OCR 77.89%
heuristic pass-rate. These are the numbers to beat/track.
