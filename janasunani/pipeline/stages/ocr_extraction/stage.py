"""OCR extraction stage entry point.

Reads the `pages` table to find rows where `extracted_text IS NULL`,
optionally filters by language, renders each page to an image, runs
the configured OCR backend, and writes the extracted text back to the
`pages` row.

The choice of backend is driven by config.ocr_engine:
  - "pytesseract": CPU-only, multi-language with confidence picking
  - "deepseek":    GPU-only, vision-language model

The pytesseract path can use multiprocessing for parallelism. DeepSeek
runs serial within one process; cross-machine parallelism uses the
worker-sharding pattern (worker_id / num_workers).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ...config import PipelineConfig
from ...db import connect
from .executors import pick_executor, shard_work_items
from .page_renderer import render_page
from loguru import logger

DB_BATCH_SIZE = 50
DB_LOCK_RETRIES = 5


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ocr_extraction(config: PipelineConfig) -> None:
    """Run the OCR extraction stage.

    Reads `pages` rows where extracted_text IS NULL, optionally filtered
    by language, OCRs each one, and writes the results back.
    """
    backend = config.ocr_engine
    if backend not in ("pytesseract", "deepseek"):
        raise ValueError(
            f"unknown ocr_engine: {backend!r}. "
            "must be 'pytesseract' or 'deepseek'."
        )

    work_items = _load_pending_pages(
        db_path=config.db_path,
        input_root=config.input_dir,
        filter_language=config.filter_language,
    )

    if not work_items:
        logger.info("ocr_extraction: nothing to do (no pages with NULL extracted_text)")
        return

    # Worker sharding: each invocation processes its slice of the total work
    work_items = shard_work_items(
        items=work_items,
        worker_id=config.worker_id,
        num_workers=config.num_workers,
    )

    if not work_items:
        logger.info(
            f"ocr_extraction: worker {config.worker_id}/{config.num_workers} "
            "has no items in its shard"
        )
        return

    n = len(work_items)
    logger.info(
        f"ocr_extraction: backend={backend} "
        f"worker={config.worker_id + 1}/{config.num_workers} "
        f"items={n}"
        + (f" (filter_language={config.filter_language})" if config.filter_language else "")
    )

    _run(backend=backend, work_items=work_items, db_path=config.db_path, n_workers=config.n_workers)


# ---------------------------------------------------------------------------
# DB reading
# ---------------------------------------------------------------------------

def _load_pending_pages(
    db_path: Path,
    input_root: Path,
    filter_language: str | None,
) -> list[dict[str, Any]]:
    """Return rows from `pages` that still need OCR.

    Includes the resolved absolute file path so workers don't need to
    know about input_root.
    """
    if not db_path.exists():
        return []

    # Known-bad pages (already in unreadable_pages for this stage) are
    # excluded — otherwise every resume re-grinds the same failures forever.
    sql = """
        SELECT page_id, doc_id, page_number, full_path, language
        FROM pages
        WHERE extracted_text IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM unreadable_pages u
              WHERE u.doc_id = pages.doc_id
                AND u.page_number = pages.page_number
                AND u.stage = 'ocr_extraction'
          )
    """
    params: list[Any] = []
    if filter_language is not None:
        sql += " AND language = ?"
        params.append(filter_language)
    sql += " ORDER BY doc_id, page_number"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for page_id, doc_id, page_number, full_path, language in rows:
        # full_path in `pages` is stored relative to the original input_dir.
        # If the user passed --input, resolve against it. Otherwise treat
        # as absolute.
        p = Path(full_path)
        if not p.is_absolute() and input_root is not None:
            p = input_root / p
        items.append(
            {
                "page_id": page_id,
                "doc_id": doc_id,
                "page_number": page_number,
                "file_path": str(p),
                "language": language,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Per-worker state and worker function
# ---------------------------------------------------------------------------

_worker_backend: str = ""
_worker_ds_tokenizer: Any = None
_worker_ds_model: Any = None


def _worker_init(backend: str) -> None:
    """Per-worker initializer.

    For deepseek, loads the GPU model (slow, multi-GB) once per worker.
    For pytesseract, just configures the tesseract binary path.
    """
    global _worker_backend, _worker_ds_tokenizer, _worker_ds_model
    _worker_backend = backend

    if backend == "deepseek":
        from .deepseek_backend import load_model
        _worker_ds_tokenizer, _worker_ds_model = load_model()
    else:
        from .pytesseract_backend import _configure_tesseract
        _configure_tesseract()


def _process_page(item: dict[str, Any]) -> dict[str, Any]:
    """OCR a single page. Returns a dict with the result + status."""
    result: dict[str, Any] = {
        "page_id": item["page_id"],
        "doc_id": item["doc_id"],
        "page_number": item["page_number"],
        "file_path": item["file_path"],
        "text": None,
        "error": None,
    }
    try:
        image = render_page(Path(item["file_path"]), item["page_number"])
    except Exception as e:
        result["error"] = f"render failed: {e}"
        return result

    try:
        if _worker_backend == "deepseek":
            from ...ocr_quality import (
                is_repetition_collapsed,
                repeated_trigram_share,
                top_trigram_share,
            )
            from .deepseek_backend import extract_text as ds_extract
            text = ds_extract(image, tokenizer=_worker_ds_tokenizer, model=_worker_ds_model)
            # DeepSeek's signature failure mode (per the DSI report): the
            # generation loops and one phrase fills the page. Store nothing —
            # record the page in unreadable_pages so resumes skip it.
            if is_repetition_collapsed(text):
                result["error"] = (
                    f"repetition collapse: repeated-trigram share "
                    f"{repeated_trigram_share(text):.2f} > 0.5 "
                    f"(top-trigram share {top_trigram_share(text):.2f})"
                )
                return result
        else:
            from .pytesseract_backend import (
                extract_text as pt_extract,
                pipeline_lang_to_tesseract,
            )
            force_lang = pipeline_lang_to_tesseract(item.get("language"))
            text = pt_extract(image, force_lang=force_lang)
    except Exception as e:
        result["error"] = f"ocr failed: {e}"
        return result

    result["text"] = text
    return result


# ---------------------------------------------------------------------------
# DB writing
# ---------------------------------------------------------------------------

def _write_results_with_retry(
    db_conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    today_iso: str,
    backend: str,
) -> int:
    """Write OCR results to `pages` table. Returns count of successful writes."""
    params = [
        {
            "text": r["text"],
            "model": backend,
            "date": today_iso,
            "page_id": r["page_id"],
        }
        for r in rows
        if r["text"] is not None
    ]
    if not params:
        return 0

    sql = """
        UPDATE pages
        SET extracted_text = :text,
            ocr_model = :model,
            extracted_date = :date
        WHERE page_id = :page_id
    """
    for attempt in range(DB_LOCK_RETRIES):
        try:
            db_conn.executemany(sql, params)
            db_conn.commit()
            return len(params)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < DB_LOCK_RETRIES - 1:
                wait = 2 ** attempt
                logger.error(f"db locked, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    return 0


def _write_failures(
    db_conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    today_iso: str,
) -> None:
    """Record OCR failures in unreadable_pages."""
    params = [
        {
            "doc_id": r["doc_id"],
            "page_number": r["page_number"],
            "full_path": r["file_path"],
            "stage": "ocr_extraction",
            "reason": r["error"],
            "detected_at": today_iso,
        }
        for r in rows
        if r["error"] is not None
    ]
    if not params:
        return

    db_conn.executemany(
        """INSERT OR IGNORE INTO unreadable_pages
           (doc_id, page_number, full_path, stage, reason, detected_at)
           VALUES (:doc_id, :page_number, :full_path, :stage, :reason, :detected_at)""",
        params,
    )
    db_conn.commit()


# ---------------------------------------------------------------------------
# Internal runner
# ---------------------------------------------------------------------------

def _run(
    backend: str,
    work_items: list[dict[str, Any]],
    db_path: Path,
    n_workers: int | None,
) -> None:
    n = len(work_items)
    today_iso = datetime.now().isoformat()

    executor = pick_executor(
        backend=backend,
        n_items=n,
        n_workers=n_workers,
        initializer=_worker_init,
        initargs=(backend,),
    )

    # For serial execution, the executor's __init__ already ran _worker_init.
    # For multiprocessing, each worker runs it. Either way, we're good.

    db_conn = connect(db_path)

    success_buf: list[dict[str, Any]] = []
    failure_buf: list[dict[str, Any]] = []
    total_success = 0
    total_failure = 0
    t_start = time.time()

    try:
        for i, result in enumerate(executor.map(_process_page, work_items), 1):
            if result["error"]:
                failure_buf.append(result)
                total_failure += 1
            else:
                success_buf.append(result)
                total_success += 1

            if len(success_buf) >= DB_BATCH_SIZE:
                _write_results_with_retry(db_conn, success_buf, today_iso, backend)
                success_buf.clear()
            if len(failure_buf) >= DB_BATCH_SIZE:
                _write_failures(db_conn, failure_buf, today_iso)
                failure_buf.clear()

            if i % 5 == 0 or i == n:
                elapsed = time.time() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                logger.info(
                    f"[{i}/{n}] ok={total_success} fail={total_failure} "
                    f"({rate:.2f} pages/s)"
                )
    except KeyboardInterrupt:
        logger.error("interrupted — flushing buffers...")
    finally:
        if success_buf:
            _write_results_with_retry(db_conn, success_buf, today_iso, backend)
        if failure_buf:
            _write_failures(db_conn, failure_buf, today_iso)
        executor.close()
        db_conn.close()

    elapsed = time.time() - t_start
    logger.success(
        f"ocr_extraction done in {elapsed:.1f}s: "
        f"ok={total_success} fail={total_failure}"
    )
