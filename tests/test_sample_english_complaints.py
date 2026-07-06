"""Language gate of scripts/sample_english_complaints.py.

The script is a single file in scripts/ (not a package), so it's loaded via
importlib. langdetect lives in the pipeline extras — skip where absent (CI).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("langdetect")

_SCRIPT = Path(__file__).parents[1] / "scripts" / "sample_english_complaints.py"
spec = importlib.util.spec_from_file_location("sample_english_complaints", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
# dataclass creation resolves the defining module through sys.modules
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_english_subject_accepted():
    assert mod.is_english(
        "The water supply in our ward has been broken for three weeks "
        "and nobody from the block office has come to repair it."
    )


def test_odia_script_rejected():
    assert not mod.is_english(
        "ଆମ ଗାଁର ପାଣି ସମସ୍ୟା ବିଷୟରେ ଅଭିଯୋଗ କରୁଛି ଦୟାକରି ସମାଧାନ କରନ୍ତୁ"
    )


def test_mixed_script_rejected():
    assert not mod.is_english(
        "Complaint regarding ପାଣି ସମସ୍ୟା in our village please resolve the matter"
    )


def test_romanized_odia_rejected():
    # No English function words — the stopword gate rejects it regardless of
    # what langdetect guesses.
    assert not mod.is_english(
        "mo ghara pakhare nala safai heu nahin bahut asubidha "
        "hauchi daya kari samadhana karantu"
    )


def test_short_subject_rejected():
    assert not mod.is_english("water problem")


# --- document gates (pure verdict logic; per-page judgements injected) ---


def test_english_letter_with_sparse_stamp_page_accepted():
    verdict = mod.assess_document(
        ["English", "English", "Sparse"], ["Letter", "Text Only", "Misc/Not Sure"]
    )
    assert verdict.ok
    assert verdict.english_share == pytest.approx(2 / 3)


def test_any_odia_dominant_page_rejects():
    # The strict rule: one Odia page poisons the document even if the rest
    # is English (this is what the earlier share-based rule got wrong).
    verdict = mod.assess_document(
        ["English", "English", "Odia"], ["Letter", "Letter", "Letter"]
    )
    assert not verdict.ok
    assert "Odia-dominant" in verdict.reason


def test_all_sparse_document_rejected():
    verdict = mod.assess_document(["Sparse", "Sparse"], ["Letter", "Letter"])
    assert not verdict.ok
    assert "no confidently-English page" in verdict.reason


def test_pii_only_document_rejected():
    # An Aadhaar/voter-ID scan: English pages but only noise-class page types.
    verdict = mod.assess_document(["English"], ["Identification"])
    assert not verdict.ok
    assert "no substantive page" in verdict.reason


def test_unreadable_document_rejected():
    verdict = mod.assess_document([], [])
    assert not verdict.ok
    assert verdict.english_share == 0.0


def test_corrupt_file_rejects_instead_of_crashing(tmp_path):
    # The bucket holds corrupt uploads (bad bytes behind a .jpeg name); a
    # --n 50 run crashed on one before assess() caught the decode error.
    # __new__ skips the ViT load — the file fails to open before any model
    # or OCR code is reached, which is exactly what the guard covers.
    pytest.importorskip("PIL")
    gates = object.__new__(mod.DocumentGates)
    corrupt = tmp_path / "DEPT2025_complaint.jpeg"
    corrupt.write_bytes(b"<html>Error: upload failed</html>")

    verdict = gates.assess(corrupt)

    assert not verdict.ok
    assert "unreadable document" in verdict.reason


def test_corrupt_pdf_rejects_instead_of_crashing(tmp_path):
    pytest.importorskip("pdf2image")
    gates = object.__new__(mod.DocumentGates)
    corrupt = tmp_path / "DEPT2025_complaint.pdf"
    corrupt.write_bytes(b"not a pdf")

    verdict = gates.assess(corrupt)

    assert not verdict.ok
    assert "unreadable document" in verdict.reason
