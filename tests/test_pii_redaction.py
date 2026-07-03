"""Presidio PII stage: real redaction on realistic grievance text.

Runs the actual analyzer/anonymizer (no mocks); skips only where the
pipeline-core extra (presidio + spaCy model) isn't installed, e.g. CI.
"""

import sqlite3

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_sm")

from janasunani.pipeline.config import PipelineConfig  # noqa: E402
from janasunani.pipeline.db import initialize_database  # noqa: E402
from janasunani.pipeline.stages.pii_tagger import redact_text, run_pii_tagger  # noqa: E402


def test_indian_pii_patterns_redacted():
    text = (
        "My name is Ramesh Kumar, mobile +91 98765 43210, aadhaar 2345 6789 0123, "
        "PAN ABCDE1234F, email ramesh.k@example.com. The water supply in ward 7 "
        "has been broken for 3 weeks."
    )
    red = redact_text(text)
    assert "98765" not in red and "[PHONE]" in red
    assert "6789" not in red and "[AADHAAR]" in red
    assert "ABCDE1234F" not in red and "[PAN]" in red
    assert "ramesh.k@example.com" not in red and "[EMAIL]" in red
    # the complaint substance survives
    assert "water supply" in red and "ward 7" in red
    # ward number is not a phone/aadhaar
    assert "3 weeks" in red


def test_no_token_window_truncation():
    """The legacy CRF silently dropped text past 512 tokens. The whole page
    must survive, with PII at the far end still redacted."""
    filler = "The drainage overflow near the market has not been cleared. " * 400
    text = filler + "Contact me on 9876543210."
    red = redact_text(text)
    assert "9876543210" not in red and "[PHONE]" in red
    assert len(red) > len(filler) * 0.95  # nothing truncated


def test_stage_covers_mixed_language_pages(tmp_path):
    """LIKE '%English%': mixed 'English, Odia' pages get redacted too
    (the legacy equality filter skipped them entirely)."""
    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO pages (doc_id, page_number, full_path, page_id, language, extracted_text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("D1", 1, "D1.pdf", "p1", "English", "call 9876543210 about roads"),
                ("D2", 1, "D2.pdf", "p2", "English, Odia", "ମୋ ନମ୍ବର 9123456789 please help"),
                ("D3", 1, "D3.pdf", "p3", "Odia", "କେବଳ ଓଡ଼ିଆ ପାଠ୍ୟ"),
            ],
        )
        con.commit()

    run_pii_tagger(
        PipelineConfig(input_dir=tmp_path, db_path=db, models_dir=tmp_path)
    )

    with sqlite3.connect(db) as con:
        got = dict(con.execute("SELECT page_id, redacted_text FROM pages").fetchall())
    assert "[PHONE]" in got["p1"] and "9876543210" not in got["p1"]
    assert "[PHONE]" in got["p2"] and "9123456789" not in got["p2"]  # mixed covered
    assert got["p3"] is None  # pure Odia: out of scope (as before)


def test_rerun_is_idempotent_and_terminates(tmp_path):
    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO pages (doc_id, page_number, full_path, page_id, language, extracted_text)"
            " VALUES ('D1', 1, 'D1.pdf', 'p1', 'English', 'no pii here at all')"
        )
        con.commit()

    cfg = PipelineConfig(input_dir=tmp_path, db_path=db, models_dir=tmp_path)
    run_pii_tagger(cfg)
    run_pii_tagger(cfg)  # second run: nothing pending, returns immediately

    with sqlite3.connect(db) as con:
        (red,) = con.execute("SELECT redacted_text FROM pages").fetchone()
    assert red == "no pii here at all"
