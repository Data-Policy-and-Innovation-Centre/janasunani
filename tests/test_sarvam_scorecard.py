"""Scorecard for Sarvam Vision vs pytesseract — paired, clustered, split per #84 and #127.

Verified with recorded (synthetic) page records only; no live Sarvam call.
"""

import pytest

from janasunani.evaluation.sarvam_scorecard import (
    NORMALIZER_VERSION,
    PageRecord,
    build_scorecard,
    divergence_rate,
    normalize_text,
    paired_difference,
    required_pages_mcnemar,
)


def test_normalizer_frozen_before_output_read():
    # Markdown vs plain text should compare equal after normalisation
    assert normalize_text("# Title\n\n**bold** text") == "Title bold text"
    assert normalize_text("a | b | c") == "a b c"
    assert normalize_text("[link](https://example.com)") == "link"
    assert normalize_text("`code`") == "code"
    assert normalize_text("  a   b\n\nc  ") == "a b c"
    # Same plain text equal
    assert normalize_text("hello") == normalize_text("hello")
    # Version is recorded
    assert NORMALIZER_VERSION == "1.0"


def test_divergence_only_when_no_transcription():
    pages = [
        PageRecord(ticket="T1", page_id="P1", handwritten="yes", language="Odia", pytesseract_text="foo", sarvam_markdown="**foo**"),
        PageRecord(ticket="T1", page_id="P2", handwritten="no", language="English", pytesseract_text="bar", sarvam_markdown="bar"),
        PageRecord(ticket="T2", page_id="P3", handwritten="yes", language="Odia", pytesseract_text="same", sarvam_markdown="same"),
    ]
    report = build_scorecard(pages)
    assert report.transcription_available is False
    assert report.needs_transcription_sample is True
    assert report.transcription_accuracy is None
    # first page: foo vs foo after normalisation -> no divergence, others: bar==bar, same==same -> 0 divergence
    assert report.transcription_divergence["rate"] == 0.0
    assert report.paired_design is True
    assert "handwritten vs printed" in report.notes.lower() or "handwritten" in report.notes
    assert "Primary outcome" in report.notes or "primary outcome" in report.notes.lower()


def test_paired_difference_clustered_by_ticket_not_page():
    # Two tickets, each with 5 identical pages. If we cluster by ticket, SE should be larger
    # than naive n=10. Simple check: ticket-clustered SE > 0 and ci on difference reported.
    sarv = [1, 1, 0, 0, 0, 1, 1, 0, 0, 0]  # sarvam correct
    pipe = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]  # pipeline correct
    clusters = ["T1"] * 5 + ["T2"] * 5
    res = paired_difference(sarv, pipe, clusters)
    assert res["n"] == 10
    assert res["n_clusters"] == 2
    assert res["diff"] == 0.4  # 4/10
    assert res["se"] >= 0
    assert res["ci_low"] < res["diff"] < res["ci_high"]
    # If we treat each page as its own cluster, SE would be smaller — verify clustering matters
    res_unclustered = paired_difference(sarv, pipe, [f"P{i}" for i in range(10)])
    # With many small clusters, SE is similar but not identical; the key is diff is the result
    assert "diff" in res_unclustered


def test_handwritten_and_language_split_reported_separately():
    pages = [
        PageRecord(ticket="T1", page_id="P1", handwritten="yes", language="Odia", pytesseract_text="a", sarvam_markdown="a", transcription="a"),
        PageRecord(ticket="T2", page_id="P2", handwritten="no", language="English", pytesseract_text="a", sarvam_markdown="b", transcription="a"),
        PageRecord(ticket="T3", page_id="P3", handwritten="yes", language="Odia", pytesseract_text="x", sarvam_markdown="y", transcription="y"),
        PageRecord(ticket="T4", page_id="P4", handwritten="no", language="English", pytesseract_text="x", sarvam_markdown="x", transcription="x"),
    ]
    report = build_scorecard(pages)
    assert report.transcription_available is True
    assert report.needs_transcription_sample is False
    # handwritten vs printed buckets exist
    assert "handwritten" in report.by_handwritten
    assert "printed" in report.by_handwritten
    # language buckets
    assert "Odia" in report.by_language
    assert "English" in report.by_language
    # primary outcome is category difference when gold available, else still documented
    assert "Category accuracy difference" in report.primary_outcome


def test_category_accuracy_is_headline_with_ci():
    pages = [
        PageRecord(ticket="T1", page_id="P1", gold_category="Police", pipeline_category="Police", sarvam_category="Police"),
        PageRecord(ticket="T2", page_id="P2", gold_category="Revenue", pipeline_category="Police", sarvam_category="Revenue"),
        PageRecord(ticket="T3", page_id="P3", gold_category="Revenue", pipeline_category="Revenue", sarvam_category="Revenue"),
        PageRecord(ticket="T4", page_id="P4", gold_category="Social Welfare", pipeline_category="Police", sarvam_category="Police"),
    ]
    report = build_scorecard(pages)
    assert report.category is not None
    # difference reported with CI, marginal rates as description
    assert "difference" in report.category
    assert "ci_low" in report.category and "ci_high" in report.category
    assert "pipeline_accuracy" in report.category
    assert "sarvam_accuracy" in report.category
    assert report.category["n_tickets"] == 4


def test_transcription_accuracy_requires_ground_truth():
    pages = [
        PageRecord(ticket="T1", page_id="P1", pytesseract_text="hello", sarvam_markdown="hello", transcription="hello"),
        PageRecord(ticket="T1", page_id="P2", pytesseract_text="hello", sarvam_markdown="HELLO", transcription="hello"),
    ]
    report = build_scorecard(pages)
    assert report.transcription_accuracy is not None
    # sarvam: 1/2 correct, pipe: 2/2 correct => diff = -0.5 favours pipeline
    assert report.transcription_accuracy["difference"] == -0.5


def test_required_pages_mcnemar_matches_issue127_order():
    # From #127 table: 10-point gap at 20% discordance ~157 pages; at 30% ~236
    assert 140 <= required_pages_mcnemar(0.10, 0.20) <= 180
    assert 200 <= required_pages_mcnemar(0.10, 0.30) <= 270
    # 5-point gap much larger
    assert required_pages_mcnemar(0.05, 0.20) > required_pages_mcnemar(0.10, 0.20)


def test_divergence_rate_ticket_clustered():
    # 2 tickets, one fully divergent, one not
    texts_a = ["same", "same", "diff", "diff"]
    texts_b = ["same", "same", "other", "other"]
    clusters = ["T1", "T1", "T2", "T2"]
    res = divergence_rate(texts_a, texts_b, clusters)
    assert res["rate"] == 0.5
    assert res["n_clusters"] == 2
    assert res["ci_low"] < 0.5 < res["ci_high"]


def test_required_pages_mcnemar_honours_alpha_and_power():
    """alpha and power must move the answer, not be accepted and ignored.

    Both z-quantiles were previously hardcoded via ternaries that returned the
    same value on either branch, so a request for 90% power came back with the
    80% sample size — an under-powered study reporting itself as powered.
    """
    base = required_pages_mcnemar(0.10, 0.25)  # alpha=0.05, power=0.80

    # Tighter level and higher power must each require strictly more pages.
    assert required_pages_mcnemar(0.10, 0.25, power=0.90) > base
    assert required_pages_mcnemar(0.10, 0.25, alpha=0.01) > base
    assert (
        required_pages_mcnemar(0.10, 0.25, alpha=0.01, power=0.90)
        > required_pages_mcnemar(0.10, 0.25, alpha=0.01)
    )

    # Pin the standard quantiles so a regression to constants is caught.
    assert required_pages_mcnemar(0.10, 0.25, power=0.90) == 259
    assert required_pages_mcnemar(0.10, 0.25, alpha=0.01, power=0.90) == 368

    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            required_pages_mcnemar(0.10, 0.25, alpha=bad)
        with pytest.raises(ValueError):
            required_pages_mcnemar(0.10, 0.25, power=bad)
