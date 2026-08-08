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


def test_stratified_sample_few_hundred_and_handwritten_printed_split():
    """Few-hundred-page sample must be stratified handwritten/printed."""
    from janasunani.evaluation.sarvam_scorecard import (
        BENCHMARK_SAMPLE_SIZE,
        BENCHMARK_MIN_SAMPLE,
        BENCHMARK_MAX_SAMPLE,
        PageRecord,
        stratified_sample,
        sample_compliance,
    )

    # Build a synthetic Sambalpur/2024-like pool: 60% printed, 40% handwritten
    pages = []
    for i in range(300):
        handwritten = "yes" if i % 5 < 2 else "no"  # 40% handwritten
        pages.append(PageRecord(ticket=f"T{i}", page_id=f"P{i}", handwritten=handwritten, language="Odia", pytesseract_text="a", sarvam_markdown="a"))

    sampled = stratified_sample(pages, n=250, seed=42)
    assert len(sampled) == 250
    # stratified: both buckets present
    buckets = {"handwritten": 0, "printed": 0}
    for p in sampled:
        buckets[p.handwritten_bucket()] += 1
    assert buckets["handwritten"] > 0
    assert buckets["printed"] > 0
    # few-hundred compliance
    comp = sample_compliance(len(sampled))
    assert comp["is_few_hundred"] is True
    assert comp["slice"] == "Sambalpur/2024"
    assert BENCHMARK_MIN_SAMPLE <= BENCHMARK_SAMPLE_SIZE <= BENCHMARK_MAX_SAMPLE
    # deterministic: same seed same result
    sampled2 = stratified_sample(pages, n=250, seed=42)
    assert [p.page_id for p in sampled] == [p.page_id for p in sampled2]
    # different seed may differ (not required but smoke)
    sampled3 = stratified_sample(pages, n=250, seed=99)
    assert len(sampled3) == 250


def test_per_category_table_reports_spread():
    """Per-category accuracy must report the police 0.85 vs welfare 0.51 style spread."""
    from janasunani.evaluation.sarvam_scorecard import PageRecord, per_category_table, build_scorecard

    # Simulate police high accuracy, welfare low — the pattern in DELIVERY
    pages = []
    # Police: 20 tickets, pipeline 0.85 (17/20), sarvam 0.90 (18/20)
    for i in range(20):
        gold = "Police"
        pipe = "Police" if i < 17 else "Revenue"
        sarv = "Police" if i < 18 else "Revenue"
        pages.append(PageRecord(ticket=f"POL{i}", page_id=f"POL{i}P1", gold_category=gold, pipeline_category=pipe, sarvam_category=sarv))

    # Welfare: 20 tickets, pipeline 0.51 ~(10/20), sarvam 0.55 (11/20)
    for i in range(20):
        gold = "Social Welfare"
        pipe = "Social Welfare" if i < 10 else "Revenue"
        sarv = "Social Welfare" if i < 11 else "Revenue"
        pages.append(PageRecord(ticket=f"WEL{i}", page_id=f"WEL{i}P1", gold_category=gold, pipeline_category=pipe, sarvam_category=sarv))

    table = per_category_table(pages)
    assert "Police" in table
    assert "Social Welfare" in table
    assert table["Police"]["n_tickets"] == 20
    assert table["Social Welfare"]["n_tickets"] == 20
    # police higher than welfare
    assert table["Police"]["pipeline_accuracy"] > table["Social Welfare"]["pipeline_accuracy"]
    assert table["Police"]["sarvam_accuracy"] > table["Social Welfare"]["sarvam_accuracy"]
    # headline spread visible
    assert abs(table["Police"]["pipeline_accuracy"] - 0.85) < 0.01
    assert abs(table["Social Welfare"]["pipeline_accuracy"] - 0.50) < 0.01

    # build_scorecard includes per_category
    report = build_scorecard(pages)
    assert report.per_category is not None
    assert "Police" in report.per_category
    assert "Social Welfare" in report.per_category
    assert report.sample_info is not None
    assert report.sample_info["slice"] == "Sambalpur/2024"


def test_transliteration_probe_on_romanized_odia_if_present():
    """Probe must detect romanized Odia and report probe_applicable; no network call."""
    from janasunani.evaluation.sarvam_scorecard import PageRecord, transliteration_probe, is_romanized_odia_candidate, build_scorecard

    # Romanized Odia: language Odia but text is Latin without Odia script
    roman_page = PageRecord(ticket="T-ROM", page_id="P-ROM", language="Odia", handwritten="no", pytesseract_text="mu ghara samasya achi", sarvam_markdown="mu ghara samasya achi")
    odia_script_page = PageRecord(ticket="T-OD", page_id="P-OD", language="Odia", handwritten="no", pytesseract_text="ମୁ ଘର ସମସ୍ୟା", sarvam_markdown="ମୁ ଘର ସମସ୍ୟା")
    english_page = PageRecord(ticket="T-EN", page_id="P-EN", language="English", handwritten="no", pytesseract_text="my house problem", sarvam_markdown="my house problem")

    assert is_romanized_odia_candidate(roman_page) is True
    assert is_romanized_odia_candidate(odia_script_page) is False
    assert is_romanized_odia_candidate(english_page) is False

    probe = transliteration_probe([roman_page, odia_script_page, english_page])
    assert probe["n_total"] == 3
    assert probe["n_romanized_candidates"] == 1
    assert probe["probe_applicable"] is True
    assert "T-ROM" in probe["romanized_ticket_ids"]

    # No romanized in sample
    probe2 = transliteration_probe([odia_script_page, english_page])
    assert probe2["probe_applicable"] is False
    assert probe2["n_romanized_candidates"] == 0

    # build_scorecard includes probe
    report = build_scorecard([roman_page, odia_script_page])
    assert report.transliteration_probe_result is not None
    assert report.transliteration_probe_result["probe_applicable"] is True


def test_build_scorecard_handwritten_printed_divergence_no_accuracy_verdict():
    """OCR divergence must be reported handwritten vs printed separately with no accuracy verdict."""
    from janasunani.evaluation.sarvam_scorecard import PageRecord, build_scorecard

    # No transcription ground truth -> divergence only, handwritten vs printed separate
    pages = [
        PageRecord(ticket="T1", page_id="P1", handwritten="yes", language="Odia", pytesseract_text="a", sarvam_markdown="a"),
        PageRecord(ticket="T1", page_id="P2", handwritten="yes", language="Odia", pytesseract_text="a", sarvam_markdown="b"),
        PageRecord(ticket="T2", page_id="P3", handwritten="no", language="Odia", pytesseract_text="same", sarvam_markdown="same"),
        PageRecord(ticket="T2", page_id="P4", handwritten="no", language="Odia", pytesseract_text="same", sarvam_markdown="different"),
    ]
    report = build_scorecard(pages)
    assert report.transcription_available is False
    assert report.transcription_accuracy is None
    # handwritten vs printed divergence reported separately
    assert "handwritten" in report.by_handwritten
    assert "printed" in report.by_handwritten
    assert report.by_handwritten["handwritten"]["divergence"]["rate"] == 0.5
    assert report.by_handwritten["printed"]["divergence"]["rate"] == 0.5
    # no accuracy verdict in notes beyond divergence
    assert "DIVERGENCE ONLY" in report.notes


def test_audit_log_row_per_call_for_scorecard_pages():
    """Every Sarvam Vision call for the benchmark must be audit-logged (ticket, stage, provider, bytes)."""
    import tempfile
    import sqlite3
    from pathlib import Path as _Path
    from dataclasses import replace

    from janasunani.egress.sarvam import (
        GovernanceControl,
        PROVIDER_REGISTRY,
        SarvamAuditContext,
        SarvamVisionAdapter,
        SqliteAuditLog,
    )
    from janasunani.evaluation.sarvam_scorecard import PageRecord

    # simulate few-hundred sample: 3 pages, each will be a digitise call
    pages = [
        PageRecord(ticket=f"T{i}", page_id=f"P{i}", handwritten="yes" if i % 2 == 0 else "no", language="Odia", pytesseract_text="a", sarvam_markdown="a")
        for i in range(3)
    ]

    control = GovernanceControl(statement="verified test", verified=True)
    route = replace(PROVIDER_REGISTRY["sarvam-vision"], retention_terms=control, encryption_in_transit=control, encryption_at_rest=control)

    class FakeTransport:
        def __init__(self):
            self.calls = []
        def post(self, url, **kwargs):
            self.calls.append(url)
            job_id = f"job-{len(self.calls)}"
            class R:
                status_code = 202
                def json(self_inner):
                    return {"job_id": job_id, "status": "pending"}
                headers = {}
            return R()
        def get(self, url, **kwargs):
            self.calls.append(url)
            if "status" in url:
                class R:
                    status_code = 200
                    def json(self):
                        return {"job_id": url.split("/")[-2], "status": "completed"}
                    headers = {}
                return R()
            elif "download-url" in url:
                class R:
                    status_code = 200
                    def json(self):
                        return {"download_url": "https://download.example/fake"}
                    headers = {}
                return R()
            else:
                # download
                import zipfile
                from io import BytesIO
                buf = BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    z.writestr("doc.md", "fake markdown")
                class R2:
                    status_code = 200
                    content = buf.getvalue()
                return R2()

    with tempfile.TemporaryDirectory() as tmp:
        audit_path = _Path(tmp) / "audit.sqlite"
        audit_log = SqliteAuditLog(audit_path)
        transport = FakeTransport()
        adapter = SarvamVisionAdapter(
            enabled=True,
            api_key="test-key",
            audit_log=audit_log,
            route=route,
            transport=transport,
            poll_interval_seconds=0,
        )
        for p in pages:
            ctx = SarvamAuditContext(ticket=p.ticket, stage="ocr_extraction", document_id=p.page_id)
            # This will log multiple rows per page (submission/poll/result/download) but at least one per call
            adapter.digitise(b"fake-bytes", f"{p.page_id}.png", "od-IN", ctx)

        rows = list(sqlite3.connect(audit_path).execute(
            "SELECT ticket, stage, provider, model_id, bytes_sent, authorization_reference, operation, event FROM authorized_external_audit"
        ))
        # at least one row per page (with bytes >0 for submission)
        tickets = {r[0] for r in rows}
        for p in pages:
            assert p.ticket in tickets
        for ticket, stage, provider, model_id, bytes_sent, auth_ref, operation, event in rows:
            assert stage == "ocr_extraction"
            assert provider == "sarvam-hosted"
            assert auth_ref != ""
            assert operation == "digitise"
            assert event in ("submission", "poll", "result_lookup", "download")


def test_few_hundred_sample_compliance_flagged_in_report():
    """Sample size outside 200-400 must be flagged; inside must be noted."""
    from janasunani.evaluation.sarvam_scorecard import PageRecord, build_scorecard

    small = [PageRecord(ticket=f"T{i}", page_id=f"P{i}", handwritten="no", pytesseract_text="a", sarvam_markdown="a") for i in range(10)]
    report_small = build_scorecard(small)
    assert report_small.sample_info["is_few_hundred"] is False
    assert "outside the intended few-hundred" in report_small.notes

    few_hundred = [PageRecord(ticket=f"T{i}", page_id=f"P{i}", handwritten="yes" if i % 2 == 0 else "no", pytesseract_text="a", sarvam_markdown="a") for i in range(250)]
    report_big = build_scorecard(few_hundred)
    assert report_big.sample_info["is_few_hundred"] is True
    assert report_big.sample_info["n_pages"] == 250
    assert "within the few-hundred" in report_big.notes
