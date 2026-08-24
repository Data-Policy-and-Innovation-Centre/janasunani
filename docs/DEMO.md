# Live demo runbook — real-inference API bring-up

This runs the **real** grievance pipeline behind the frozen serving contract:
`janasunani-api-live` loads every in-process model once (page-type ViT →
summarizer BART → categorizer MuRIL → Presidio PII), runs OCR with
pytesseract/Poppler, and persists each submission to the `live_grievances`
OLTP table. The default `janasunani-api` app stays mock; this entry point is
the opt-in real one.

For the full **cloud** deployment (CPU box + compose + backups) see
[DEPLOY.md](DEPLOY.md). This document is the **integration bring-up** — proven
locally first, then repeated on the box. For the automated build →
GHCR → box rollout of this same API (Docker images, CI, one-time box setup),
see [DEPLOY.md §4 "Automated demo deploy"](DEPLOY.md#4--automated-demo-deploy-ci--ghcr--box).

> **Fast path — `make`.** The `Makefile` wraps every step below:
> `make models` (legacy category/page-type mirrors only) · materialize an
> approved release or provision the remaining local artifacts · `make preflight`
> · `make up` (throwaway
> Postgres + API + frontend, waits for `processor: pipeline` before starting the
> UI, one `Ctrl-C` stops both) · `make down` (tear down by port). `make api`/
> `make up` provision and migrate the local Postgres for you — if you point
> `OLTP_DB_URL` at anything other than the throwaway default they skip that step
> and never migrate it (you manage that database yourself).
>
> This fast path is **local**. It assumes a dev machine with Docker, `uv`,
> Node/npm, and `lsof`. The CPU box is **not** provisioned for `make up` (no
> Node, demo ports 3000/8000 closed by the security group, and prod Postgres
> already on 5432) — deploy there via [DEPLOY.md](DEPLOY.md) (compose), not this
> fast path. To *view* a locally-run demo from another machine, SSH-tunnel the
> ports rather than exposing them:
> `ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 <box>`.
>
> The numbered sections below are the underlying manual commands, for running a
> step by hand.

---

## 1. Prerequisites

| Dependency | How to get it | Checked by |
|---|---|---|
| Mandatory local model artifacts (categorizer, page-type ViT, BART summarizer) | Prefer an approved immutable release; `make models` pulls only the two legacy DVC mirrors | `janasunani-demo-preflight` |
| Approved release manifest (strict deploy) | Follow [MODELS.md](MODELS.md#serving-different-versions-safely); never run the example spec unchanged | preflight `model release` check |
| `tesseract` binary | `brew install tesseract` / `apt-get install tesseract-ocr` | preflight |
| Odia (`ori`) traineddata | `brew install tesseract-lang` / `apt-get install tesseract-ocr-ori` | **manual** — `tesseract --list-langs \| grep ori` (preflight checks only the binary) |
| Poppler (`pdfinfo` **and** `pdftoppm`) | `brew install poppler` / `apt-get install poppler-utils` | preflight |
| The `demo` Python extra | `uv sync --extra demo` | — |
| A reachable Postgres (see §3) | Docker | manual (`pg_isready`) |

> **Do not run an unqualified `dvc pull`** on a demo box — the workspace also
> DVC-tracks the raw SQL dump and document samples (real grievance PII). Pull
> only approved model targets (plus the routing mappings,
> `data/raw/janasunani-mappings.dvc`, if you want `method: "rules"` routing).

Production startup never downloads a model. The summarizer must resolve from an
operator override, an active checksum-valid release or its local DVC mirror.
A mutable remote model ID is permitted only with the explicit
`JANASUNANI_ALLOW_REMOTE_MODELS=1` development opt-in.

---

## 2. Preflight (fast, weight-free)

Run this *before* the multi-minute warm start — it surfaces a missing model
file or OCR binary in milliseconds instead of minutes into model loading, and
reports which OLTP store will be selected:

```bash
uv run --extra demo janasunani-demo-preflight
```

Exits non-zero if a required model/binary is missing. Advisory release, router,
triage, lake and OLTP checks are warnings normally and failures under `--strict`.
Expected output includes:

```
[OK  ] categorizer config: .../models/categorizer/config.json
[OK  ] categorizer weights: .../categorizer/model.safetensors | .../pytorch_model.bin
[OK  ] categorizer tokenizer: .../categorizer/tokenizer.json | .../vocab.txt
[OK  ] categorizer label encoder: .../categorizer/label_encoder_ROS_wDOCS_english.pkl
[OK  ] page-type config: .../vit_type_classifier/config.json
[OK  ] page-type weights: .../vit_type_classifier/model.safetensors | .../pytorch_model.bin
[OK  ] page-type image processor: .../vit_type_classifier/preprocessor_config.json
[OK  ] summarizer config/weights/tokenizer: ...
[OK  ] tesseract: OCR text-extraction binary
[OK  ] pdfinfo/pdftoppm: PDF page renderer (poppler)
[OK  ] model release: active release ...
[OK  ] router: ...
[OK  ] triage: ...
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
  -p 127.0.0.1:5544:5432 \
  -v janasunani-demo-oltp:/var/lib/postgresql/data \
  postgres:17

export OLTP_DB_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:5544/janasunani"
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
export OLTP_DB_URL="postgresql+asyncpg://postgres:demo@127.0.0.1:5544/janasunani"
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
- **PII evidence is incomplete.** The development scorecard measures recall,
  but precision and required language/source slices are not release-ready.
- **No reviewed production release is active by default.** Local immutable
  release and rollback machinery exists, but an operator must approve and
  materialize the exact artifacts before strict preflight can pass.
- **Triage is advisory.** `spam-v1.1-bounded` is the default. Setting
  `JANASUNANI_TRIAGE=model` loads the checksummed binary actionability candidate
  when available and otherwise falls back safely; neither mode rejects, closes
  or reroutes a grievance.
- **Non-English submissions** skip the summarizer and are marked
  `Uncategorized` — the first real-model demo targets English.

---

## 7. Rehearsal gate (13 Aug)

The **13 August freeze** is gated by a single laptop command that proves every [DELIVERY.md](DELIVERY.md) Table 1 row has either a live surface or a reconciled artifact on this machine — before any code freeze or tag.

```bash
make rehearsal
# runs scripts/demo_rehearsal.sh — see docs/plans/2026-08-08-demo-integration-rehearsal.md Part 2
# Note: the Make target and script land in PR #203 (chore/demo-rehearsal-script);
# until that merges, run the individual Phase A–C checks from the plan directly.
```

What it checks (four phases, fail-fast):

- **Phase A — static (no stack):** `ruff check`, `pytest` on the demo contract (`test_demo_integration.py`, `test_pipeline_e2e.py`, `test_routing_integration.py`, `test_supervisor_intelligence.py`, `test_serving_triage_contract.py`), and `janasunani-demo-preflight`.
- **Phase B — stack smoke:** requires `make up` (or starts a throwaway DB+API itself), polls `GET /health` until `{"status":"ok","processor":"pipeline"}`, drives a text submission and asserts `redaction.redacted_text` / `classification.category` / `summary` / `routing.method` / `triage.spam.spam_score`, checks `GET /grievance/{id}` round-trip, `GET /supervisor` (warn if `Unavailable*`, fail only if all panels unavailable), `GET /history` (may be empty on a fresh lake — see §5 vs §3), and `curl -sf http://127.0.0.1:3000`.
- **Phase C — artifact presence:** warns (or fails, configurable) if `janasunani/routing/reference/routing_crosswalk.json`, `outputs/findings/`, `DATA_DIR/aggregates/` / `JANASUNANI_SUPERVISOR_FINDINGS_DIR`, or `outputs/sarvam/` / `outputs/benchmark/table2.md` are missing — each maps to a demo component.
- **Phase D — optional real models:** `JANASUNANI_RUN_MODEL_SMOKE=1` runs `tests/test_inference_model_smoke.py`.

Run it **the night before the demo** (see [DEMO_SCRIPT.md](DEMO_SCRIPT.md#pre-demo-checklist)) and again the morning of. The full walkthrough it gates is [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — Scenes 0–6 (43 min scripted, ~45 min with buffer, with privacy preamble, OLTP-vs-lake note, routing ladder, triage banner, supervisor 61% talking point, benchmark Table 2, and pre-demo checklist).

| Artifact | Where |
|---|---|
| Timed walkthrough (Scenes 0–6) | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
| Automated gate | [`scripts/demo_rehearsal.sh`](../scripts/demo_rehearsal.sh) (via PR #203) |
| Integration plan (gates, Table 2, three Sarvam arms) | [`docs/plans/2026-08-08-demo-integration-rehearsal.md`](plans/2026-08-08-demo-integration-rehearsal.md) |
| Delivery scope & benchmark Table 2 | [DELIVERY.md](DELIVERY.md) Table 1 & 2 |

> **History empty after a live submit is not a failure.** `GET /grievance/{id}` is OLTP and is the gate that must pass; `GET /history` is lake-backed and may lag until the next `janasunani-materialize`. The rehearsal script documents this gap rather than patching serving to query OLTP for history.
