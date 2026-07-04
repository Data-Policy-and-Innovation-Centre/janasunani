#!/usr/bin/env bash
# DeepSeek OCR smoke test — run ON the GPU box, from the repo root, after
# `git clone` (see deploy/terraform/README.md, "GPU box").
#
# Exercises the two-env split the pyproject conflict rules force: document
# discovery + format classification run in the pipeline-core env, then
# DeepSeek OCR runs in the ocr-deepseek env (older transformers), both against
# the same SQLite artifact. First OCR run downloads the ~7 GB weights from HF.
#
# Success = both sample pages get non-empty extracted_text with
# ocr_model='deepseek' and no repetition collapse.
set -euo pipefail

DB=data/processed/gpu-smoke.sqlite

echo "== GPU check =="
nvidia-smi --query-gpu=name,memory.total --format=csv

echo "== Pull sample + format model (instance role -> DVC remote) =="
uv run dvc pull data/raw/documents-sample.dvc \
  models/format_classifier/page_split_v3.0_doc_split.pkl.dvc

rm -f "$DB"

echo "== Stage 1: format_classifier (pipeline-core env) =="
uv run --extra pipeline-core janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db "$DB" \
  --models models \
  --stages format_classifier \
  --workers 1

echo "== Stage 2: DeepSeek OCR (ocr-deepseek env) =="
uv run --extra ocr-deepseek janasunani-pipeline run \
  --input data/raw/documents-sample \
  --db "$DB" \
  --models models \
  --stages ocr_extraction \
  --ocr-engine deepseek

echo "== Results =="
uv run python - "$DB" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    """SELECT page_id, ocr_model, length(extracted_text)
       FROM pages ORDER BY doc_id, page_number"""
).fetchall()
for page_id, model, n_chars in rows:
    print(f"  {page_id}: ocr_model={model} extracted_chars={n_chars}")

failures = conn.execute(
    "SELECT doc_id, page_number, reason FROM unreadable_pages"
).fetchall()
for doc_id, page_number, reason in failures:
    print(f"  FAILED {doc_id} p{page_number}: {reason}")

ok = [r for r in rows if r[1] == "deepseek" and (r[2] or 0) > 0]
# Every discovered page must extract — a page left NULL/empty without even an
# unreadable_pages row is a silent failure, not a pass.
if not rows or len(ok) < len(rows) or failures:
    print(
        f"SMOKE FAILED: {len(ok)}/{len(rows)} page(s) extracted, "
        f"{len(failures)} failure(s)"
    )
    sys.exit(1)
print(f"SMOKE OK: {len(ok)}/{len(rows)} page(s) extracted via deepseek")
PY
