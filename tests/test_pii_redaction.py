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
from janasunani.pipeline.stages.pii_tagger import (  # noqa: E402
    detect_pii_spans,
    is_government_email,
    redact_text,
    run_pii_tagger,
)


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


def test_odia_digit_numbers_redacted():
    """Numbers written in Odia numerals must not escape: Presidio's \\d is
    Unicode-aware but the [6-9]/[2-9] anchors are ASCII-only, so detection
    runs on a digit-normalized copy (offsets carry over 1:1)."""
    # ୯୮୭୬୫୪୩୨୧୦ = 9876543210
    text = "ମୋ ନମ୍ବର ୯୮୭୬୫୪୩୨୧୦ please call. The year ୨୦୨୪ saw no repairs."
    red = redact_text(text)
    assert "୯୮୭୬୫୪୩୨୧୦" not in red and "[PHONE]" in red
    # redaction replaces spans in the ORIGINAL text: surrounding Odia script
    # and the non-PII Odia year are untouched, not transliterated
    assert "ମୋ ନମ୍ବର" in red
    assert "୨୦୨୪" in red


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


# --- #55: dates and file numbers must not be tagged PHONE ------------------


@pytest.mark.parametrize(
    "text",
    [
        "The hearing was held on 06.05.2025 at the block office.",
        "Order dated 06-05-2025 was not implemented.",
        "Application dated 12/03/2024 was rejected.",
    ],
)
def test_dates_are_not_tagged_phone(text):
    assert not [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert redact_text(text) == text


def test_bare_file_number_in_letter_context_is_not_tagged_phone():
    text = "Letter no. 1234567890 dated 12.03.2024 refers."
    assert not [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "My mobile is 9876543210, please call.",
        "Reach me at +91 98765 43210 today.",
        "Call 09876543210 for updates.",
        "Contact on 98765 43210 anytime.",
        "Contact on 98765-43210 anytime.",
    ],
)
def test_mobiles_still_detected_across_formats(text):
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    red = redact_text(text)
    assert "9876543210" not in red and "[PHONE]" in red


def test_landline_still_detected():
    text = "Office 0674 2536789 during hours."
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    red = redact_text(text)
    assert "0674" not in red and "2536789" not in red and "[PHONE]" in red


def test_aadhaar_unaffected_by_phone_changes():
    text = "Aadhaar number 2345 6789 0123 attached."
    spans = detect_pii_spans(text)
    assert any(s.entity == "AADHAAR" for s in spans)
    assert not [s for s in spans if s.entity == "PHONE"]
    red = redact_text(text)
    assert "[AADHAAR]" in red and "6789" not in red


# --- #56: government email addresses are not PII ----------------------------


@pytest.mark.parametrize(
    "address",
    [
        "officer@nic.in",
        "bdo.khordha@nic.in",
        "officer@rb.nic.in",
        "officer@odisha.gov.in",
        "officer@pmo.gov.in",
        "OFFICER@NIC.IN",
    ],
)
def test_government_emails_are_not_redacted(address):
    assert is_government_email(address)
    text = f"Please contact {address} for grievance status."
    assert redact_text(text) == text
    assert not [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]


@pytest.mark.parametrize(
    "address",
    [
        "citizen@gmail.com",
        "citizen@yahoo.co.in",
        "citizen@hotmail.com",
        "citizen@example.in",
    ],
)
def test_citizen_emails_still_redacted(address):
    assert not is_government_email(address)
    text = f"Please contact {address} for grievance status."
    red = redact_text(text)
    assert address not in red and "[EMAIL]" in red
    spans = [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]
    assert len(spans) == 1
    assert text[spans[0].start : spans[0].end] == address


def test_government_email_exclusion_is_not_a_psl_accident():
    """Regression guard: the old behaviour rode on tldextract parsing a bare
    "@nic.in"/"@gov.in" address as an empty registrable domain -- an accident
    that only covered part of the domain space (subdomains like
    "rb.nic.in" were still redacted, and "mil.in" isn't a PSL suffix at all,
    so a PSL-driven rule would never protect it). Our predicate must exclude
    all three uniformly regardless of what tldextract/the PSL says."""
    assert is_government_email("officer@mil.in")
    assert is_government_email("officer@station.mil.in")
    text = "Contact officer@mil.in or officer@rb.nic.in for status."
    assert redact_text(text) == text
    assert not [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]


def test_redact_text_and_detect_pii_spans_agree_on_government_email():
    """The two entry points must never diverge (#56): one gates production
    redaction, the other gates the eval scorecard."""
    text = "Contact officer@nic.in or citizen@gmail.com for details."

    red = redact_text(text)
    spans = detect_pii_spans(text)
    email_spans = [s for s in spans if s.entity == "EMAIL"]

    assert "officer@nic.in" in red  # government address: untouched
    assert "citizen@gmail.com" not in red and "[EMAIL]" in red  # citizen: redacted
    assert len(email_spans) == 1
    assert text[email_spans[0].start : email_spans[0].end] == "citizen@gmail.com"
