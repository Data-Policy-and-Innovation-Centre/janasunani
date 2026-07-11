# Live demo runbook — real-inference API bring-up

This runs the **real** grievance pipeline behind the frozen serving contract:
`janasunani-api-live` loads every in-process model once (page-type ViT →
summarizer BART → categorizer MuRIL → Presidio PII), runs OCR with
pytesseract/Poppler, and persists each submission to the `live_grievances`
OLTP table. The default `janasunani-api` app stays mock; this entry point is
the opt-in real one.

For the full **cloud** deployment (CPU box + compose + backups) see
[DEPLOY.md](DEPLOY.md). This document is the **integration bring-up** — proven
locally first, then repeated on the box.

> **Fast path — `make`.** The `Makefile` wraps every step below:
> `make models` (scoped DVC pull) · `make preflight` · `make up` (throwaway
> Postgres + API + frontend, waits for `processor: pipeline` before starting the
> UI, one `Ctrl-C` stops both) · `make down` (tear down by port). `make api`/
> `make up` provision and migrate the local Postgres for you — if you point
> `OLTP_DB_URL` at anything other than the throwaway default they skip that step
> and never migrate it (you manage that database yourself).
>
> **On the box / remote:** `API_URL` is baked into the frontend bundle, so serve
> with `make up API_URL=http://<box-ip>:$API_PORT API_HOST=0.0.0.0` — otherwise
> a browser on another machine calls its own `127.0.0.1`. Full cloud path is in
> [DEPLOY.md](DEPLOY.md).
>
> The numbered sections below are the underlying manual commands, for running a
> step by hand.

---

## 1. Prerequisites

| Dependency | How to get it | Checked by |
|---|---|---|
| Model artifacts under `models/` (categorizer, page-type ViT) | `dvc pull models/categorizer.dvc models/page_type_classifier/vit_type_classifier.dvc` | `janasunani-demo-preflight` |
| `tesseract` binary | `brew install tesseract` / `apt-get install tesseract-ocr` | preflight |
| Odia (`ori`) traineddata | `brew install tesseract-lang` / `apt-get install tesseract-ocr-ori` | **manual** — `tesseract --list-langs \| grep ori` (preflight checks only the binary) |
| Poppler (`pdfinfo` **and** `pdftoppm`) | `brew install poppler` / `apt-get install poppler-utils` | preflight |
| The `demo` Python extra | `uv sync --extra demo` | — |
| A reachable Postgres (see §3) | Docker | manual (`pg_isready`) |

> **Do not run an unqualified `dvc pull`** on a demo box — the workspace also
> DVC-tracks the raw SQL dump and document samples (real grievance PII). Pull
> only the model targets above (plus the routing mappings,
> `data/raw/janasunani-mappings.dvc`, if you want `method: "rules"` routing).

The summarizer downloads `facebook/bart-large-cnn` (~1.6 GB) from the Hugging
Face hub **on first startup** — unlike the categorizer/page-type models it is
not DVC-mirrored. Pre-warm it once on a good connection so the demo itself
doesn't stall on a cold download (see [§6 Known limitations](#6-known-limitations)).

---

## 2. Preflight (fast, weight-free)

Run this *before* the multi-minute warm start — it surfaces a missing model
file or OCR binary in milliseconds instead of minutes into model loading, and
reports which OLTP store will be selected:

```bash
uv run --extra demo janasunani-demo-preflight
```

Exits non-zero if any dependency is missing. Expected output when ready:

```
[OK  ] categorizer config: .../models/categorizer/config.json
[OK  ] categorizer weights: .../categorizer/model.safetensors | .../pytorch_model.bin
[OK  ] categorizer tokenizer: .../categorizer/tokenizer.json | .../vocab.txt
[OK  ] categorizer label encoder: .../categorizer/label_encoder_ROS_wDOCS_english.pkl
[OK  ] page-type config: .../vit_type_classifier/config.json
[OK  ] page-type weights: .../vit_type_classifier/model.safetensors | .../pytorch_model.bin
[OK  ] page-type image processor: .../vit_type_classifier/preprocessor_config.json
[OK  ] tesseract: OCR text-extraction binary
[OK  ] pdfinfo/pdftoppm: PDF page renderer (poppler)
[INFO] OLTP: explicit URL set -> DatabaseResultStore (persistent)
```

The weight/tokenizer entries accept either candidate filename (`model.safetensors`
or `pytorch_model.bin`); a partial mirror missing the weights fails preflight
rather than crashing mid-warm-up.

---

## 3. Postgres + migrations

**Local (laptop):** run a throwaway Postgres — do **not** use
`deploy/docker-compose.yml`, whose `oltp` service attaches to the CPU box's
`external` production volume by name.

```bash
docker run -d --name janasunani-demo-oltp \
  -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=janasunani \
  -p 127.0.0.1:5432:5432 \
  -v janasunani-demo-oltp:/var/lib/postgresql/data \
  postgres:17

export OLTP_DB_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:5432/janasunani"
uv run alembic upgrade head   # creates live_grievances (+ the rest of the schema)
```

> The test-fixture Postgres on `127.0.0.1:5433` (`jana-pg`) **drops tables** —
> keep the demo DB on a different port/container so a `pytest` run can't wipe it.

**CPU box:** the compose `oltp` service already runs against the migrated
production volume; run `alembic upgrade head` against it (it is idempotent) and
point `OLTP_DB_URL` at it. `live_grievances` is a **sibling** table — it never
touches the historical `complaints` data. Never `docker compose down -v`.

---

## 4. Launch + health gate

```bash
export OLTP_DB_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:5432/janasunani"
uv run --extra demo janasunani-api-live      # host/port: JANASUNANI_API_HOST/PORT
```

First boot is slow (model warm-up). When it's up, the health check **must**
report the real processor — `mock` here means the live entry point isn't the
one answering (e.g. a stale `janasunani-api` is squatting on the port):

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","processor":"pipeline"}
```

---

## 5. Drive a submission

Typed text:

```bash
curl -s -X POST http://127.0.0.1:8000/grievance \
  -F "text=The village water supply has been contaminated for weeks; the panchayat has not responded." \
  -F "district=Cuttack" | python3 -m json.tool
```

A document (PDF/image — the OCR path). The page-type gate only feeds
grievance-bearing pages (Letter / Form / Text) to the models; a document with
no such page is rejected with HTTP 422:

```bash
curl -s -X POST http://127.0.0.1:8000/grievance \
  -F "file=@/path/to/letter.pdf;type=application/pdf" \
  -F "district=Khordha" | python3 -m json.tool
```

Verify persistence + the round-trip read:

```bash
curl -s http://127.0.0.1:8000/grievance/<id>            # reads back from OLTP
docker exec janasunani-demo-oltp psql -U postgres -d janasunani \
  -c 'select id, ticket_no, source, category, language, routing_method from live_grievances;'
```

A submission is fully real when `extraction.source` is `text`/`document`
(with `ocr_model="pytesseract"` + `pages` for documents), `classification`
and `summary` come from the models, and the row lands in `live_grievances`.

---

## 6. Known limitations

Surfaced during the first real bring-up — none block the demo, all are tracked
for evaluation/retraining (see [ROADMAP.md](ROADMAP.md)):

- **Routing degrades to `method: "fallback"` (low confidence)** unless the
  DVC-tracked routing mappings are loaded; the router is designed to degrade
  gracefully rather than fail. Load the mappings for a full `method: "rules"`
  demo.
- **PII recall is limited on Indian names** — Presidio's `en_core_web_sm`
  model misses many person names (e.g. it redacted a phone number but not the
  submitter's name in one text sample). This is exactly what the PII gold
  labeling + `eval_results.jsonl` scoring is meant to quantify before real
  citizen text flows through.
- **BART is fetched from the public HF hub at startup**, not from a DVC
  mirror. On macOS the Rust `hf_xet` transfer backend can hang *after* the
  download completes; `serve.py` sets `HF_HUB_DISABLE_XET=1` on darwin to
  avoid it. Pre-warm the model (or mirror it) for a hands-off box demo.
- **Non-English submissions** skip the summarizer and are marked
  `Uncategorized` — the first real-model demo targets English.
