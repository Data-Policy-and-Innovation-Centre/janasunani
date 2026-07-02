"""Document-processing pipeline: structure, CLI, and a real OCR path.

Structural tests (DB schema, stage validation, CLI parsing) always run. The
OCR test exercises the real format_classifier -> pytesseract path on a
generated fixture image and skips where the tesseract binary or the DVC-pulled
format-classifier pickle is missing (e.g. CI).
"""

import shutil
import sqlite3

import pytest

from janasunani.config import directories
from janasunani.pipeline.cli import build_parser
from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect, initialize_database
from janasunani.pipeline.pipeline import STAGE_ORDER, run_pipeline

FORMAT_MODEL = directories.MODELS / "format_classifier" / "page_split_v3.0_doc_split.pkl"


def test_init_db_creates_schema_and_is_idempotent(tmp_path):
    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    initialize_database(db)  # second run must not fail or clobber
    with connect(db) as con:
        tables = {
            r["name"]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"pages", "documents", "unreadable_pages"} <= tables


def test_unknown_stage_fails_fast_before_any_import(tmp_path):
    config = PipelineConfig(
        input_dir=tmp_path,
        db_path=tmp_path / "p.sqlite",
        models_dir=tmp_path,
        stages=("ocr_extraction", "not_a_stage"),
    )
    with pytest.raises(ValueError, match="not_a_stage"):
        run_pipeline(config)


def test_cli_parses_stage_subset_and_engine():
    args = build_parser().parse_args(
        ["run", "--stages", "ocr_extraction", "--ocr-engine", "pytesseract"]
    )
    assert args.stages == ["ocr_extraction"]
    assert args.ocr_engine == "pytesseract"
    # every CLI-selectable stage must be a real stage
    assert set(STAGE_ORDER) >= set(args.stages)


def test_cli_rejects_unknown_stage():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--stages", "bogus"])


@pytest.mark.skipif(
    shutil.which("tesseract") is None or not FORMAT_MODEL.exists(),
    reason="needs the tesseract binary and the DVC-pulled format-classifier model",
)
def test_format_and_ocr_on_generated_image(tmp_path):
    """Real code path: a rendered text image through format_classifier +
    pytesseract OCR lands in the pages table with the text recovered."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=64)
    draw.text((60, 150), "WATER SUPPLY COMPLAINT 12345", fill="black", font=font)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    img.save(input_dir / "sample.jpg")

    db = tmp_path / "pipeline.sqlite"
    run_pipeline(
        PipelineConfig(
            input_dir=input_dir,
            db_path=db,
            models_dir=directories.MODELS,
            ocr_engine="pytesseract",
            stages=("format_classifier", "ocr_extraction"),
            n_workers=1,
        )
    )

    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT doc_id, extracted_text FROM pages WHERE extracted_text IS NOT NULL"
        ).fetchall()
    assert len(rows) == 1
    assert "WATER" in rows[0][1] and "12345" in rows[0][1]
