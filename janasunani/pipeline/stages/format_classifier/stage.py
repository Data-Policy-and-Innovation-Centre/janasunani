"""Format classifier stage entry point.

This is the function the pipeline orchestrator calls. It:
  1. Iterates over input files (PDFs and images)
  2. For PDFs, splits into pages
  3. Runs the classifier on each page
  4. Writes results to the `pages` table (or `unreadable_pages` on failure)

The execution strategy (serial vs multiprocessing) is pluggable via
the executor module. By default `auto_executor()` picks one based on
the number of input files.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pdf2image import convert_from_path
from PIL import Image

from ...config import PipelineConfig
from ...db import connect
from ...ticket import doc_id_from_relpath, ticket_from_relpath
from .executors import Executor, auto_executor
from .features import (
    configure_tesseract,
    restore_stderr,
    suppress_stderr,
)
from .model import FormatClassifier
from loguru import logger

# Avoid pdf2image's pixel-bomb DoS protection blocking large but legitimate
# scans. We trust our own input.
Image.MAX_IMAGE_PIXELS = None

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
PDF_EXTENSIONS = {".pdf"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

# How many rows to buffer before flushing to SQLite.
#
# Keep this deliberately small. Some inputs are multi-page PDFs, and workers
# return one result per input file, so large buffers make the DB appear empty
# for a long time even though work is happening.
DB_BATCH_SIZE = 10

# Safety cap for PDFs whose page count we can't determine upfront
MAX_PAGES_FALLBACK = 500

# Stop a PDF early after this many consecutive page failures.
MAX_CONSECUTIVE_FAILURES = 5

# Bound Poppler/pdf2image calls so a bad page cannot block all workers forever.
PDF_RENDER_TIMEOUT_SECONDS = 60

_USER_POPPLER_PATH = Path.home() / ".local/poppler/usr/bin"
if (_USER_POPPLER_PATH / "pdfinfo").exists():
    POPPLER_PATH = str(_USER_POPPLER_PATH)
    _USER_POPPLER_LIB = Path.home() / ".local/poppler/usr/lib/x86_64-linux-gnu"
    if _USER_POPPLER_LIB.exists():
        os.environ["LD_LIBRARY_PATH"] = (
            f"{_USER_POPPLER_LIB}:{os.environ.get('LD_LIBRARY_PATH', '')}"
        ).rstrip(":")
elif Path("/usr/bin/pdfinfo").exists():
    POPPLER_PATH = "/usr/bin"
else:
    POPPLER_PATH = None

# Length of page_id hex string. 16 hex chars = 64 bits — plenty.
PAGE_ID_LENGTH = 16

MODEL_NAME = "format_classifier_v3"


# ---------------------------------------------------------------------------
# Public entry point — called by pipeline.py
# ---------------------------------------------------------------------------

def run_format_classifier(config: PipelineConfig) -> None:
    """Run the format classifier stage.

    Reads input files from config.input_dir (or config.file_list if set),
    classifies each page, and writes results to config.db_path.

    The trained model is loaded from the first .pkl file in
    config.models_dir / "format_classifier".
    """
    model_dir = config.models_dir / "format_classifier"
    candidates = sorted(model_dir.glob("*.pkl"))
    if not candidates:
        raise FileNotFoundError(
            f"no .pkl model found in {model_dir}. "
            "Place the trained format classifier there."
        )
    model_path = candidates[0]

    _run(
        db_path=config.db_path,
        model_path=model_path,
        input_dir=config.input_dir,
        file_list=config.file_list,
        n_workers=config.n_workers,
    )


# ---------------------------------------------------------------------------
# Per-worker state
# ---------------------------------------------------------------------------

_worker_classifier: FormatClassifier | None = None
_worker_existing: set[tuple[str, int]] = set()


def _worker_init(model_path: str, existing: set[tuple[str, int]]) -> None:
    """Initializer called once per worker process."""
    global _worker_classifier, _worker_existing
    configure_tesseract()
    _worker_classifier = FormatClassifier(Path(model_path), model_name=MODEL_NAME)
    _worker_existing = existing


# ---------------------------------------------------------------------------
# Per-file work function (runs in worker)
# ---------------------------------------------------------------------------

def _process_file(args: tuple[str, str, str]) -> dict[str, Any]:
    """Process a single file (image or PDF) and return classification results.

    Returns a stats dict rather than raising so one bad file doesn't kill
    the whole batch.
    """
    file_path_str, input_root_str, today = args
    file_path = Path(file_path_str)
    input_root = Path(input_root_str)

    stats: dict[str, Any] = {
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
        "pages": [],
        "unreadable": [],
        "error_detail": "",
    }

    try:
        rel_path = str(file_path.relative_to(input_root))
    except ValueError:
        rel_path = str(file_path)

    # doc_id must be unique per RELATIVE PATH, not per basename: the corpus
    # nests documents (OR107/E/2021/00324_...pdf) and two files in different
    # subdirs can share a stem — a stem-only id silently merges/drops the
    # later one via the (doc_id, page_number) unique key. Flat files keep the
    # same id as before (stem).
    doc_id = doc_id_from_relpath(rel_path)
    ext = file_path.suffix.lower()

    if ext in PDF_EXTENSIONS:
        _process_pdf(file_path, rel_path, doc_id, today, stats)
    elif ext in IMAGE_EXTENSIONS:
        _process_image(file_path, rel_path, doc_id, today, stats)
    else:
        stats["errors"] += 1
        stats["error_detail"] = f"unknown extension: {file_path}"
        stats["unreadable"].append({
            "doc_id": doc_id, "page_number": 0, "full_path": rel_path,
            "reason": f"unknown extension {ext}",
        })

    return stats


def _process_pdf(
    file_path: Path, rel_path: str, doc_id: str, today: str, stats: dict[str, Any],
) -> None:
    assert _worker_classifier is not None

    saved_stderr = suppress_stderr()
    try:
        known_page_count = True
        try:
            from pdf2image.pdf2image import pdfinfo_from_path
            info = pdfinfo_from_path(
                str(file_path),
                timeout=PDF_RENDER_TIMEOUT_SECONDS,
                poppler_path=POPPLER_PATH,
            )
            num_pages = info.get("Pages", 0)
        except Exception:
            try:
                test = convert_from_path(
                    str(file_path),
                    dpi=72,
                    first_page=1,
                    last_page=1,
                    timeout=PDF_RENDER_TIMEOUT_SECONDS,
                    poppler_path=POPPLER_PATH,
                )
                if not test:
                    stats["errors"] += 1
                    stats["unreadable"].append({
                        "doc_id": doc_id, "page_number": 0, "full_path": rel_path,
                        "reason": "pdf empty",
                    })
                    return
                num_pages = MAX_PAGES_FALLBACK
                known_page_count = False
            except Exception as e:
                stats["errors"] += 1
                stats["error_detail"] = f"pdf unreadable: {file_path}: {e}"
                stats["unreadable"].append({
                    "doc_id": doc_id, "page_number": 0, "full_path": rel_path,
                    "reason": f"pdf unreadable: {e}",
                })
                return

        consecutive_failures = 0
        for page_num in range(1, num_pages + 1):
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                if known_page_count:
                    stats["errors"] += num_pages - page_num + 1
                break

            if (doc_id, page_num) in _worker_existing:
                stats["skipped"] += 1
                consecutive_failures = 0
                continue

            try:
                images = convert_from_path(
                    str(file_path), dpi=200, fmt="png",
                    first_page=page_num, last_page=page_num,
                    timeout=PDF_RENDER_TIMEOUT_SECONDS,
                    poppler_path=POPPLER_PATH,
                )
                if not images:
                    break
                img_cv = cv2.cvtColor(np.array(images[0]), cv2.COLOR_RGB2BGR)
                preds = _worker_classifier.predict(img_cv)
                if preds is None:
                    stats["errors"] += 1
                    # Persist the failure like image/whole-PDF failures do —
                    # otherwise the page vanishes from the audit and is
                    # retried forever.
                    stats["unreadable"].append({
                        "doc_id": doc_id, "page_number": page_num,
                        "full_path": rel_path,
                        "reason": "classifier returned no prediction",
                    })
                    consecutive_failures += 1
                    continue
                stats["pages"].append(
                    _build_page_row(doc_id, page_num, rel_path, preds, today)
                )
                stats["inserted"] += 1
                consecutive_failures = 0
            except Exception:
                stats["errors"] += 1
                consecutive_failures += 1
    finally:
        restore_stderr(saved_stderr)


def _process_image(
    file_path: Path, rel_path: str, doc_id: str, today: str, stats: dict[str, Any],
) -> None:
    assert _worker_classifier is not None

    page_num = 1
    if (doc_id, page_num) in _worker_existing:
        stats["skipped"] += 1
        return

    img_cv = cv2.imread(str(file_path))
    if img_cv is None:
        stats["errors"] += 1
        stats["error_detail"] = f"image unreadable: {file_path}"
        stats["unreadable"].append({
            "doc_id": doc_id, "page_number": page_num, "full_path": rel_path,
            "reason": "image unreadable",
        })
        return

    preds = _worker_classifier.predict(img_cv)
    if preds is None:
        stats["errors"] += 1
        stats["error_detail"] = f"classify failed: {file_path}"
        stats["unreadable"].append({
            "doc_id": doc_id, "page_number": page_num, "full_path": rel_path,
            "reason": "classification failed",
        })
        return

    stats["pages"].append(_build_page_row(doc_id, page_num, rel_path, preds, today))
    stats["inserted"] += 1


def _build_page_row(
    doc_id: str, page_number: int, full_path: str,
    preds: dict[str, str | None], today: str,
) -> dict[str, Any]:
    page_id = hashlib.sha256(
        f"{doc_id}_{page_number}".encode()
    ).hexdigest()[:PAGE_ID_LENGTH]
    ticket_number = ticket_from_relpath(full_path)
    return {
        "doc_id": doc_id,
        "page_number": page_number,
        "full_path": full_path,
        "page_id": page_id,
        "page_path": full_path,
        "scanned": preds.get("scanned"),
        "handwritten": preds.get("handwritten"),
        "printed": preds.get("printed"),
        "image_quality": preds.get("image_quality"),
        "color_scale": preds.get("color_scale"),
        "language": preds.get("language"),
        "ticket_number": ticket_number,
        "model": MODEL_NAME,
        "date_classified": today,
        "data_access_date": today,
    }


# ---------------------------------------------------------------------------
# Path collection
# ---------------------------------------------------------------------------

def _collect_paths_from_dir(input_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALL_EXTENSIONS:
            paths.append(p)
    return paths


def _collect_paths_from_file_list(file_list: Path, input_root: Path | None) -> list[Path]:
    paths: list[Path] = []
    with file_list.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = Path(line)
            if not p.is_absolute():
                if input_root is None:
                    raise ValueError(
                        f"file list contains relative path {line!r} but no "
                        "input root was provided to resolve it against"
                    )
                p = input_root / p
            if p.suffix.lower() in ALL_EXTENSIONS:
                paths.append(p)
    return paths


def _load_existing_pages(db_path: Path) -> set[tuple[str, int]]:
    """Return (doc_id, page_number) pairs already in the pages table."""
    existing: set[tuple[str, int]] = set()
    if not db_path.exists():
        return existing
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60.0)
        for row in conn.execute("SELECT doc_id, page_number FROM pages"):
            existing.add((row[0], row[1]))
        conn.close()
    except Exception as e:
        logger.warning(f"could not load existing pages: {e}")
    return existing


# ---------------------------------------------------------------------------
# Internal runner — does the actual work
# ---------------------------------------------------------------------------

def _run(
    db_path: Path,
    model_path: Path,
    input_dir: Path | None = None,
    file_list: Path | None = None,
    n_workers: int | None = None,
    executor: Executor | None = None,
) -> dict[str, int]:
    """Internal implementation. Kept separate from the public entry point
    so unit tests can call it directly with individual paths rather than
    constructing a full PipelineConfig.
    """
    if input_dir is None and file_list is None:
        raise ValueError("must provide input_dir or file_list (or both)")

    if file_list is not None:
        paths = _collect_paths_from_file_list(file_list, input_dir)
    else:
        assert input_dir is not None
        paths = _collect_paths_from_dir(input_dir)

    if not paths:
        logger.error("no input files found")
        return {"inserted": 0, "skipped": 0, "errors": 0, "db_errors": 0}

    path_root = input_dir if input_dir is not None else Path("/")
    existing = _load_existing_pages(db_path)
    today = datetime.now().strftime("%Y-%m-%d")
    work_items = [(str(p), str(path_root), today) for p in paths]

    logger.success(f"format classifier: {len(paths)} file(s), {len(existing)} pages already done")

    owns_executor = executor is None
    if executor is None:
        executor = auto_executor(
            n_items=len(paths),
            n_workers=n_workers,
            initializer=_worker_init,
            initargs=(str(model_path), existing),
        )

    # Serial path doesn't run the initializer in a subprocess — set globals here.
    if _worker_classifier is None:
        _worker_init(str(model_path), existing)

    totals = {"inserted": 0, "skipped": 0, "errors": 0, "db_errors": 0}
    page_buf: list[dict[str, Any]] = []
    unreadable_buf: list[dict[str, Any]] = []
    recent_errors: list[str] = []

    db_conn = connect(db_path)

    def flush() -> None:
        if page_buf:
            try:
                db_conn.executemany(
                    """INSERT OR IGNORE INTO pages
                       (doc_id, page_number, full_path, page_id, page_path,
                        scanned, handwritten, printed, image_quality,
                        color_scale, language, ticket_number, model,
                        date_classified, data_access_date)
                       VALUES (:doc_id, :page_number, :full_path, :page_id,
                               :page_path, :scanned, :handwritten, :printed,
                               :image_quality, :color_scale, :language,
                               :ticket_number, :model, :date_classified,
                               :data_access_date)""",
                    page_buf,
                )
                db_conn.commit()
            except Exception as e:
                totals["db_errors"] += len(page_buf)
                logger.error(f"db batch error (pages): {e}")
            page_buf.clear()
        if unreadable_buf:
            try:
                for row in unreadable_buf:
                    row["stage"] = "format_classifier"
                    row["detected_at"] = today
                db_conn.executemany(
                    """INSERT OR IGNORE INTO unreadable_pages
                       (doc_id, page_number, full_path, stage, reason, detected_at)
                       VALUES (:doc_id, :page_number, :full_path, :stage,
                               :reason, :detected_at)""",
                    unreadable_buf,
                )
                db_conn.commit()
            except Exception as e:
                logger.error(f"db batch error (unreadable): {e}")
            unreadable_buf.clear()

    t_start = time.time()
    try:
        for i, stats in enumerate(executor.map(_process_file, work_items), 1):
            totals["inserted"] += stats["inserted"]
            totals["skipped"] += stats["skipped"]
            totals["errors"] += stats["errors"]
            page_buf.extend(stats["pages"])
            unreadable_buf.extend(stats["unreadable"])

            if stats["error_detail"] and len(recent_errors) < 20:
                recent_errors.append(stats["error_detail"])

            if len(page_buf) >= DB_BATCH_SIZE or stats["pages"]:
                flush()

            elapsed = time.time() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            logger.info(
                f"[{i}/{len(paths)}] inserted={totals['inserted']} "
                f"skipped={totals['skipped']} errors={totals['errors']} "
                f"({rate:.1f} files/s)"
            )
    except KeyboardInterrupt:
        logger.error("interrupted — flushing buffers...")
    finally:
        flush()
        if owns_executor:
            executor.close()
        db_conn.close()

    elapsed = time.time() - t_start
    logger.success(
        f"format classifier done in {elapsed:.1f}s: "
        f"inserted={totals['inserted']} skipped={totals['skipped']} "
        f"errors={totals['errors']} db_errors={totals['db_errors']}"
    )
    if recent_errors:
        logger.error(f"first {min(len(recent_errors), 5)} error(s):")
        for err in recent_errors[:5]:
            logger.error(f"  - {err}")

    return totals
