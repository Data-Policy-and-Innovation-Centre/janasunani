"""Corpus scan — grep-shaped PII over redacted text.

The invariant: after redaction no mobile, Aadhaar, PAN or non-government
email shape survives in the slice redacted text. This tests the
helper that powers the "55,544 scale" claim, not the gold recall.

Runs without the ``pii`` extra (regex helpers are stdlib-only). One
test exercises the parquet helper via a tiny fixture.
"""

from __future__ import annotations

import pytest

from janasunani.evaluation.pii_scorecard import (
    assert_zero_shaped_pii,
    contains_shaped_pii,
    find_shaped_pii,
    scan_corpus_parquet,
    scan_texts,
)


# ---------------------------------------------------------------------------
# Shaped-PII regex unit — each entity class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "call 9876543210 now",
        "mobile +91 98765 43210 please",
        "Reach me at 09876543210 for updates",
        "Contact on 98765 43210 anytime",
        "Contact on 98765-43210 anytime",
        "My number is 9876 543 210, please call",  # 4-3-3
        "My number is 987 654 3210, please call",  # 3-3-4
    ],
)
def test_mobile_shapes_detected(text: str):
    found = find_shaped_pii(text)
    assert found["PHONE"], f"expected PHONE in {text!r} got {found}"
    assert not found["AADHAAR"]
    assert not found["PAN"]


@pytest.mark.parametrize(
    "text",
    [
        "Aadhaar 2345 6789 0123 attached",
        "aadhaar 234567890123 mismatch",
        "UID 3456-7890-1234",
    ],
)
def test_aadhaar_shapes_detected(text: str):
    found = find_shaped_pii(text)
    assert found["AADHAAR"], f"expected AADHAAR in {text!r} got {found}"


@pytest.mark.parametrize(
    "text",
    [
        "PAN ABCDE1234F attached",
        "my pan is FGHIJ5678K",
    ],
)
def test_pan_shapes_detected(text: str):
    found = find_shaped_pii(text)
    assert found["PAN"], f"expected PAN in {text!r} got {found}"


@pytest.mark.parametrize(
    "address",
    [
        "citizen@gmail.com",
        "citizen@yahoo.co.in",
        "user@hotmail.com",
        "ramesh.k@example.com",
        "citizen@example.in",
    ],
)
def test_non_government_emails_flagged(address: str):
    found = find_shaped_pii(f"contact {address} for help")
    assert found["EMAIL"] == [address]


@pytest.mark.parametrize(
    "address",
    [
        "officer@nic.in",
        "bdo.khordha@nic.in",
        "officer@rb.nic.in",
        "officer@odisha.gov.in",
        "officer@pmo.gov.in",
        "OFFICER@NIC.IN",
        "officer@mil.in",
        "officer@station.mil.in",
    ],
)
def test_government_emails_not_flagged(address: str):
    found = find_shaped_pii(f"contact {address} for help")
    assert found["EMAIL"] == [], f"gov {address!r} must not be flagged, got {found}"


@pytest.mark.parametrize(
    "text",
    [
        "The hearing was held on 06.05.2025 at the block office.",
        "Order dated 06-05-2025 was not implemented.",
        "Application dated 12/03/2024 was rejected.",
        "water supply in ward 7 has been broken for 3 weeks",
        "Grievance about water supply. Name [NAME] Phone [PHONE] please help",
        "",
        "[PHONE] [AADHAAR] [PAN] [EMAIL]",
        "ratan tata",  # no shape
    ],
)
def test_clean_text_has_no_shaped_pii(text: str):
    assert find_shaped_pii(text) == {"PHONE": [], "AADHAAR": [], "PAN": [], "EMAIL": []}
    assert not contains_shaped_pii(text)


def test_contains_shaped_pii_handles_none():
    assert not contains_shaped_pii(None)
    assert not contains_shaped_pii("")


def test_mixed_content_reports_all_entities():
    text = "call 9876543210, aadhaar 2345 6789 0123, PAN ABCDE1234F, email x@gmail.com"
    found = find_shaped_pii(text)
    assert found["PHONE"] == ["9876543210"]
    assert found["AADHAAR"] == ["2345 6789 0123"]
    assert found["PAN"] == ["ABCDE1234F"]
    assert found["EMAIL"] == ["x@gmail.com"]


# ---------------------------------------------------------------------------
# scan_texts — aggregation over many redacted texts
# ---------------------------------------------------------------------------


def test_scan_texts_zero_on_clean_fixtures():
    clean = [
        "Water supply broken since June. Please help. [NAME] [PHONE] redacted",
        "Pension not received for three months. Contact [EMAIL] done",
        "Street light not working in ward 7. No PII here.",
        None,
        "",
        "Grievance about water supply. Name [NAME] Phone [PHONE] Aadhaar [AADHAAR]",
    ]
    result = scan_texts(clean)
    assert result.total_texts == 6
    assert result.texts_with_pii == 0
    assert result.by_entity == {"PHONE": 0, "AADHAAR": 0, "PAN": 0, "EMAIL": 0}
    assert result.to_dict()["passed"] is True
    # assert helper must not raise
    assert_zero_shaped_pii(clean)


def test_scan_texts_counts_leaky_fixtures():
    texts = [
        "call me on 9876543210 about water",  # PHONE
        "aadhaar 2345 6789 0123 mismatch",  # AADHAAR
        "PAN ABCDE1234F attached",  # PAN
        "email citizen@gmail.com here",  # EMAIL non-gov
        "contact officer@nic.in for status",  # gov — clean
        "no pii here",
    ]
    result = scan_texts(texts)
    assert result.total_texts == 6
    assert result.texts_with_pii == 4
    assert result.by_entity["PHONE"] == 1
    assert result.by_entity["AADHAAR"] == 1
    assert result.by_entity["PAN"] == 1
    assert result.by_entity["EMAIL"] == 1
    assert not result.to_dict()["passed"]
    with pytest.raises(AssertionError, match="shaped PII survived"):
        assert_zero_shaped_pii(texts)
    # also via result object
    with pytest.raises(AssertionError):
        assert_zero_shaped_pii(result)


def test_scan_texts_samples_are_bounded():
    texts = [f"leak {9876543200 + i}" for i in range(10)]
    result = scan_texts(texts, sample_limit=2)
    assert len(result.examples["PHONE"]) == 2


def test_assert_zero_passes_on_empty():
    assert_zero_shaped_pii([])
    assert_zero_shaped_pii([None, "", "[NAME] no digits"])


# ---------------------------------------------------------------------------
# Parquet helper — tiny fixture (polars or pyarrow)
# ---------------------------------------------------------------------------


def test_scan_corpus_parquet_zero_and_leaky(tmp_path):
    try:
        import polars as pl
    except ImportError:
        pytest.skip("polars not installed")

    # clean parquet
    clean_path = tmp_path / "clean.parquet"
    pl.DataFrame(
        {
            "grievance_redacted": [
                "Water supply broken. [NAME] [PHONE]",
                "Pension pending. [EMAIL] done",
                "No PII at all",
            ]
        }
    ).write_parquet(clean_path)
    clean = scan_corpus_parquet(clean_path, column="grievance_redacted")
    assert clean.texts_with_pii == 0
    assert clean.total_texts == 3
    assert_zero_shaped_pii(clean)

    # leaky parquet
    leaky_path = tmp_path / "leaky.parquet"
    pl.DataFrame(
        {
            "grievance_redacted": [
                "call 9876543210 now",
                "clean line",
                "aadhaar 2345 6789 0123 here",
            ]
        }
    ).write_parquet(leaky_path)
    leaky = scan_corpus_parquet(leaky_path, column="grievance_redacted")
    assert leaky.texts_with_pii == 2
    assert leaky.by_entity["PHONE"] == 1
    assert leaky.by_entity["AADHAAR"] == 1
    with pytest.raises(AssertionError):
        assert_zero_shaped_pii(leaky)
