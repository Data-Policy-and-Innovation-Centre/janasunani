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
from janasunani.pipeline.cli import build_parser, main
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


def test_doc_id_unique_across_nested_dirs_and_stable_for_flat_files():
    # Same stem in different subdirs must NOT collide (nested corpus:
    # OR107/E/2021/...); flat files keep the historical stem-only id.
    from janasunani.pipeline.ticket import doc_id_from_relpath

    a = doc_id_from_relpath("AN063/E/2021/00001_complaint_x.pdf")
    b = doc_id_from_relpath("OR107/E/2021/00001_complaint_x.pdf")
    assert a != b
    assert a == "AN063/E/2021/00001_complaint_x"
    assert doc_id_from_relpath("CMO2021_complaint_y.jpeg") == "CMO2021_complaint_y"


def test_page_type_model_resolves_to_dvc_mirror(tmp_path):
    # Provenance rule: the mirrored local copy wins; HF repo only as fallback;
    # explicit config overrides everything.
    from janasunani.pipeline.stages.page_type_classifier import _resolve_model_id

    local = tmp_path / "page_type_classifier" / "vit_type_classifier"
    cfg = PipelineConfig(input_dir=tmp_path, db_path=tmp_path / "p.db", models_dir=tmp_path)
    assert _resolve_model_id(cfg) == "DPIC-Pipeline/vit_type_classifier"  # no mirror
    local.mkdir(parents=True)
    (local / "config.json").write_text("{}")
    assert _resolve_model_id(cfg) == str(local)  # mirror present
    cfg2 = PipelineConfig(
        input_dir=tmp_path, db_path=tmp_path / "p.db", models_dir=tmp_path,
        page_type_model_id="explicit/override",
    )
    assert _resolve_model_id(cfg2) == "explicit/override"


def test_partial_json_reingest_preserves_existing_grievances(tmp_path):
    """A sample JSON missing a ticket must not NULL a previously ingested
    grievance (which would silently un-categorize the document)."""
    import json

    pytest.importorskip("pandas")
    from janasunani.pipeline.stages.categorizer.ingest_grievances import ingest_grievances

    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO pages (doc_id, page_number, full_path, page_id, ticket_number)"
            " VALUES ('D1', 1, 'D1.pdf', 'p1', 'T1')"
        )
        con.commit()

    full = tmp_path / "full.json"
    full.write_text(json.dumps({"ticket_no": {"0": "T1"}, "grievance": {"0": "need water"}}))
    ingest_grievances(db_path=db, complaints_json=full)

    sample = tmp_path / "sample.json"  # T1 absent
    sample.write_text(json.dumps({"ticket_no": {"0": "T2"}, "grievance": {"0": "other"}}))
    ingest_grievances(db_path=db, complaints_json=sample)

    with sqlite3.connect(db) as con:
        got = con.execute("SELECT grievance FROM documents WHERE doc_id='D1'").fetchone()
    assert got == ("need water",)  # preserved, not clobbered to NULL


def test_cli_parses_stage_subset_and_engine():
    args = build_parser().parse_args(
        ["run", "--stages", "ocr_extraction", "--ocr-engine", "pytesseract"]
    )
    assert args.stages == ["ocr_extraction"]
    assert args.ocr_engine == "pytesseract"
    # every CLI-selectable stage must be a real stage
    assert set(STAGE_ORDER) >= set(args.stages)


def test_sarvam_engine_is_disabled_unless_explicitly_enabled():
    args = build_parser().parse_args(["run", "--ocr-engine", "sarvam"])
    assert args.ocr_engine == "sarvam"
    assert args.enable_sarvam is False

    enabled = build_parser().parse_args(["run", "--ocr-engine", "sarvam", "--enable-sarvam"])
    assert enabled.enable_sarvam is True


def test_cli_rejects_enabled_sarvam_cross_machine_sharding(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "janasunani-pipeline",
            "run",
            "--ocr-engine",
            "sarvam",
            "--enable-sarvam",
            "--num-workers",
            "2",
        ],
    )
    monkeypatch.setattr(
        "janasunani.pipeline.cli.run_pipeline",
        lambda _config: pytest.fail("pipeline must not start"),
    )

    with pytest.raises(SystemExit, match="--num-workers 1"):
        main()


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
