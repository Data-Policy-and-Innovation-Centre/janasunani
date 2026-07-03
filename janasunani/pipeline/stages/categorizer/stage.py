"""Grievance categorizer stage entry point — the final pipeline stage.

Reads document-level rows from `documents` (grievance text + ticket),
pulls the redacted OCR text for that document's pages from `pages`,
builds the `grievance_and_docs` feature exactly as training did
(grievance + " " + joined redacted page text), runs the MuRIL classifier,
and writes the predicted category to `documents.grievance_category`.

Feature construction mirrors the training-time `add_document_text`:
  - document text = all of a ticket's page `redacted_text`, space-joined,
    skipping pages whose redacted_text is NULL (no fall back to
    extracted_text)
  - grievance_and_docs = grievance + " " + document_text, space-joined,
    skipping empty parts

Only English documents are categorized (the model was trained English-only
via langdetect); non-English rows are left with NULL category.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ...config import PipelineConfig
from ...db import connect
from .ingest_grievances import ingest_grievances
from .model import GrievanceCategorizer
from loguru import logger

DB_BATCH_SIZE = 50


def run_categorizer(config: PipelineConfig) -> None:
    """Run the grievance categorizer stage.

    If config provides a complaints JSON path, grievances are ingested into
    the documents table first (so this stage can run standalone). Then every
    documents row with a grievance but no category yet is categorized.
    """
    # Step 1 (optional): ingest grievances from JSON so documents rows exist.
    # config.complaints_json is added to PipelineConfig; if unset, we assume
    # documents have already been populated by an earlier ingestion run.
    complaints_json = getattr(config, "complaints_json", None)
    if complaints_json is not None:
        ingest_grievances(db_path=config.db_path, complaints_json=Path(complaints_json))

    # Step 2: build features + classify.
    work = _load_pending_documents(config.db_path)
    if not work:
        logger.info("categorizer: nothing to do (no documents with grievance and "
              "NULL grievance_category)")
        return

    logger.info(f"categorizer: {len(work)} document(s) to categorize")

    # Model provenance rule: prefer the DVC-mirrored copy under models/ —
    # runtime must not depend on the (orphaned) student HF account.
    local_dir = config.models_dir / "categorizer"
    model_dir = str(local_dir) if (local_dir / "config.json").exists() else "jeseniapar/categorizer"

    _run(
        db_path=config.db_path,
        model_dir=model_dir,
        work=work,
    )


# ---------------------------------------------------------------------------
# DB reading: build the work list
# ---------------------------------------------------------------------------

def _load_pending_documents(db_path: Path) -> list[dict[str, Any]]:
    """Return documents needing categorization, each with its built feature.

    A document is eligible if it has grievance text and no category yet.
    For each, we assemble grievance_and_docs from the grievance plus the
    document's page redacted_text.
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        doc_rows = conn.execute(
            """SELECT doc_id, ticket_number, grievance
               FROM documents
               WHERE grievance IS NOT NULL
                 AND grievance != ''
                 AND grievance_category IS NULL"""
        ).fetchall()

        work: list[dict[str, Any]] = []
        for d in doc_rows:
            doc_id = d["doc_id"]
            # Pull this document's page redacted_text. Join by doc_id (pages
            # and documents share doc_id at 1:1 ticket granularity). Skip
            # NULL redacted_text per the rule — no extracted_text fallback.
            page_rows = conn.execute(
                """SELECT redacted_text FROM pages
                   WHERE doc_id = ? AND redacted_text IS NOT NULL
                   ORDER BY page_number""",
                (doc_id,),
            ).fetchall()
            doc_text = " ".join(
                str(r["redacted_text"]) for r in page_rows if r["redacted_text"]
            )

            grievance = str(d["grievance"]) if d["grievance"] else ""
            # grievance + " " + doc_text, skipping empties, like training.
            grievance_and_docs = " ".join(
                part for part in (grievance, doc_text) if part
            ).strip()

            work.append(
                {
                    "doc_id": doc_id,
                    "ticket_number": d["ticket_number"],
                    "text": grievance_and_docs,
                }
            )
    finally:
        conn.close()
    return work


# ---------------------------------------------------------------------------
# English filter (training was English-only via langdetect)
# ---------------------------------------------------------------------------

def _is_english(text: str, min_english_ratio: float = 0.90) -> bool:
    """Mirror the training script's is_english_only filter."""
    import re

    if not isinstance(text, str) or len(text.strip()) < 10:
        return False
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 42
        if detect(text) != "en":
            return False
    except Exception:
        return False
    latin = re.findall(r"[A-Za-z]", text)
    total = re.findall(r"\w", text)
    if not total:
        return False
    return (len(latin) / len(total)) >= min_english_ratio


# ---------------------------------------------------------------------------
# Internal runner
# ---------------------------------------------------------------------------

def _run(db_path: Path, model_dir: Path, work: list[dict[str, Any]]) -> None:
    n = len(work)

    # Load the model once (slow). Done here, after we know there's work.
    classifier = GrievanceCategorizer(model_dir=model_dir)

    conn = connect(db_path)
    buf: list[dict[str, Any]] = []
    total_ok = 0
    total_skipped_lang = 0
    t_start = time.time()

    def flush() -> None:
        if not buf:
            return
        conn.executemany(
            """UPDATE documents
               SET grievance_category = :category
               WHERE doc_id = :doc_id""",
            buf,
        )
        conn.commit()
        buf.clear()

    try:
        for i, item in enumerate(work, 1):
            text = item["text"]
            # English-only, matching training distribution.
            if not _is_english(text):
                total_skipped_lang += 1
            else:
                category = classifier.predict(text)
                buf.append({"category": category, "doc_id": item["doc_id"]})
                total_ok += 1

            if len(buf) >= DB_BATCH_SIZE:
                flush()

            if i % 10 == 0 or i == n:
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                logger.info(f"[{i}/{n}] categorized={total_ok} "
                      f"skipped_non_english={total_skipped_lang} "
                      f"({rate:.2f} docs/s)")
    except KeyboardInterrupt:
        logger.error("interrupted — flushing...")
    finally:
        flush()
        conn.close()

    elapsed = time.time() - t_start
    logger.success(f"categorizer done in {elapsed:.1f}s: "
          f"categorized={total_ok} skipped_non_english={total_skipped_lang}")
