"""Page type classifier stage.

This stage does inference only. It loads the trained ViT classifier from
Hugging Face and writes predicted labels into `pages.page_type`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect
from loguru import logger


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
PDF_EXTENSIONS = {".pdf"}
DB_BATCH_SIZE = 50
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
PAGE_TYPE_CLASS_BY_LABEL = {
    "Identification": 0,
    "Misc/Not Sure": 0,
    "Bill/Receipts": 0,
    "Bills/Receipts": 0,
    "Letter": 1,
    "Form/Application": 1,
    "Text Only": 1,
}


def _resolve_model_id(config: PipelineConfig) -> str:
    """Model provenance rule: the DVC-mirrored copy under models/ wins;
    the orphaned-org HF repo is only a fallback. Explicit config overrides."""
    if config.page_type_model_id:
        return config.page_type_model_id
    local = config.models_dir / "page_type_classifier" / "vit_type_classifier"
    if (local / "config.json").exists():
        return str(local)
    return "DPIC-Pipeline/vit_type_classifier"


def run_page_type_classifier(config: PipelineConfig) -> None:
    """Add page type predictions into `pages.page_type` and `pages.page_type_class`."""
    _ensure_page_type_class_column(config.db_path)
    _backfill_page_type_class(config.db_path)

    rows = _load_pending_pages(config.db_path)
    if not rows:
        logger.info("page type classifier: no pages need classification")
        return

    model_id = _resolve_model_id(config)
    classifier = _PageTypeClassifier(model_id)

    logger.info(f"page type classifier: {len(rows)} page(s), model={model_id}")

    totals = {"classified": 0, "skipped": 0, "errors": 0}
    updates: list[tuple[str, int | None, str]] = []

    with connect(config.db_path) as connection:
        for index, row in enumerate(rows, 1):
            try:
                image = _load_page_image(row, config.input_dir)
                if image is None:
                    totals["skipped"] += 1
                    continue

                page_type = classifier.predict(image)
                updates.append(
                    (page_type, _page_type_class_for_label(page_type), row["page_id"])
                )
                totals["classified"] += 1
            except Exception as exc:
                totals["errors"] += 1
                logger.error(
                    "page type classifier error "
                    f"{row['doc_id']} page {row['page_number']}: {exc}",
                )

            if len(updates) >= DB_BATCH_SIZE:
                _flush_updates(connection, updates)

            if index % 50 == 0 or index == len(rows):
                logger.info(
                    f"[{index}/{len(rows)}] classified={totals['classified']} "
                    f"skipped={totals['skipped']} errors={totals['errors']}"
                )

        _flush_updates(connection, updates)

    logger.success(
        "page type classifier done: "
        f"classified={totals['classified']} skipped={totals['skipped']} "
        f"errors={totals['errors']}"
    )


def _page_type_class_for_label(page_type: str) -> int | None:
    return PAGE_TYPE_CLASS_BY_LABEL.get(page_type)


def _ensure_page_type_class_column(db_path: Path) -> None:
    with connect(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(pages)").fetchall()
        }
        if "page_type_class" not in columns:
            connection.execute("ALTER TABLE pages ADD COLUMN page_type_class INTEGER")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pages_page_type_class "
            "ON pages(page_type_class)"
        )
        connection.commit()


def _backfill_page_type_class(db_path: Path) -> None:
    with connect(db_path) as connection:
        for page_type, page_type_class in PAGE_TYPE_CLASS_BY_LABEL.items():
            connection.execute(
                """UPDATE pages
                   SET page_type_class = ?
                   WHERE page_type = ?
                     AND page_type_class IS NULL""",
                (page_type_class, page_type),
            )
        connection.commit()


class _PageTypeClassifier:
    def __init__(self, model_id: str) -> None:
        try:
            import torch
            from transformers import (
                AutoImageProcessor,
                AutoModelForImageClassification,
            )
        except ImportError as exc:
            raise RuntimeError(
                "page type classifier requires `torch` and `transformers`. "
                "Install the project dependencies before running this stage."
            ) from exc


        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self._processor = AutoImageProcessor.from_pretrained(model_id)
            self._model = AutoModelForImageClassification.from_pretrained(
                model_id,
            ).to(self._device)
        except Exception as exc:
            raise RuntimeError(
                f"could not load page type model {model_id!r}. "
                "If it is private, run `hf auth login` with an account that "
                "has access to the Hugging Face repo."
            ) from exc

        self._model.eval()

    def predict(self, image: Image.Image) -> str:
        inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
        predicted_id = int(logits.argmax(-1).item())
        return self._model.config.id2label[predicted_id]


def _load_pending_pages(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT page_id, doc_id, page_number, full_path, page_path
               FROM pages
               WHERE page_type IS NULL OR page_type = ''
               ORDER BY doc_id, page_number"""
        ).fetchall()
    return [dict(row) for row in rows]


def _flush_updates(connection: Any, updates: list[tuple[str, int | None, str]]) -> None:
    if not updates:
        return
    connection.executemany(
        "UPDATE pages SET page_type = ?, page_type_class = ? WHERE page_id = ?",
        updates,
    )
    connection.commit()
    updates.clear()


def _load_page_image(row: dict[str, Any], input_dir: Path) -> Image.Image | None:
    page_path = _resolve_input_path(row.get("page_path"), input_dir)
    if (
        page_path is not None
        and page_path.exists()
        and page_path.suffix.lower() in IMAGE_EXTENSIONS
    ):
        return _open_image(page_path)

    source_path = _resolve_input_path(row.get("full_path"), input_dir)
    if source_path is None or not source_path.exists():
        logger.error(
            f"page type classifier skip {row['doc_id']} page {row['page_number']}: "
            "source file not found",
        )
        return None

    suffix = source_path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return _open_image(source_path)
    if suffix in PDF_EXTENSIONS:
        return _render_pdf_page(source_path, int(row["page_number"]))

    logger.error(
        f"page type classifier skip {row['doc_id']} page {row['page_number']}: "
        f"unsupported file extension {suffix}",
    )
    return None


def _resolve_input_path(value: str | None, input_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return input_dir / path


def _open_image(path: Path) -> Image.Image:
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGB")


def _render_pdf_page(path: Path, page_number: int) -> Image.Image:
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError(
            "page type classifier needs `pdf2image` to classify PDF pages. "
            "Install it and make sure Poppler is available on the system."
        ) from exc

    pages = convert_from_path(
        str(path),
        dpi=200,
        fmt="png",
        first_page=page_number,
        last_page=page_number,
        timeout=PDF_RENDER_TIMEOUT_SECONDS,
        poppler_path=POPPLER_PATH,
    )
    if not pages:
        raise RuntimeError(f"could not render PDF page {page_number}")
    return pages[0].convert("RGB")
