from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from janasunani.inference.ocr import OcrQualityError, OcrResult
from janasunani.inference.service import (
    UNSUPPORTED_LANGUAGE_SUMMARY,
    InferenceInputError,
    PipelineGrievanceProcessor,
    _guard_page_type_predict,
    _PageTypeModelError,
)
from janasunani.routing.rules import RuleRouter
from janasunani.serving.schemas import DuplicateReview, SpamReview, TriageResult
from janasunani.serving.triage import TriageUnavailableError


@dataclass
class Span:
    entity: str
    start: int
    end: int
    score: float


class RecordingCategorizer:
    instances = 0

    def __init__(self, category: str = "Water Supply") -> None:
        type(self).instances += 1
        self.category = category
        self.inputs: list[str] = []

    def predict(self, text: str) -> str:
        self.inputs.append(text)
        return self.category


class RecordingSummarizer:
    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1
        self.inputs: list[str] = []

    def summarize(self, text: str) -> str:
        self.inputs.append(text)
        return f"summary: {text}"


def _redact(text: str) -> str:
    return text.replace("9876543210", "[PHONE]").replace("Ramesh", "[NAME]")


def _detect(text: str) -> list[Span]:
    spans = []
    for value, entity in (("Ramesh", "NAME"), ("9876543210", "PHONE")):
        start = text.find(value)
        if start >= 0:
            spans.append(Span(entity, start, start + len(value), 0.99))
    return spans


def _processor(
    *,
    ocr=None,
    categorizer=None,
    summarizer=None,
    english=True,
    language="en",
    triage_provider=None,
):
    return PipelineGrievanceProcessor(
        ocr=ocr
        or (lambda _document_bytes, _document_name: OcrResult("unused", 1, [])),
        redact=_redact,
        detect_pii=_detect,
        categorizer=categorizer or RecordingCategorizer(),
        summarizer=summarizer or RecordingSummarizer(),
        router=RuleRouter(),
        is_english_compatible=lambda _text: english,
        detect_language=lambda _text: language,
        triage_provider=triage_provider,
        now=lambda: datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
    )


def _process(processor, **overrides):
    values = {
        "grievance_id": "g1",
        "ticket_no": "JS1",
        "text": "Ramesh reports that the village pump is broken. Call 9876543210.",
        "district": "Cuttack",
    }
    values.update(overrides)
    return processor.process(**values)


def test_typed_text_maps_pii_and_feeds_only_redacted_text_to_models():
    categorizer = RecordingCategorizer()
    summarizer = RecordingSummarizer()
    processor = _processor(categorizer=categorizer, summarizer=summarizer)

    result = _process(processor)

    expected = "[NAME] reports that the village pump is broken. Call [PHONE]."
    assert result.extraction.source == "text"
    assert result.extraction.extracted_text.startswith("Ramesh")
    assert result.redaction.redacted_text == expected
    assert [entity.model_dump() for entity in result.redaction.entities] == [
        {"entity": "NAME", "start": 0, "end": 6},
        {"entity": "PHONE", "start": 53, "end": 63},
    ]
    assert categorizer.inputs == [expected]
    assert summarizer.inputs == [expected]
    assert result.summary == f"summary: {expected}"
    assert result.classification.language == "en"
    assert result.classification.category == "Water Supply"
    assert result.routing.method == "rules"
    assert result.routing.dept == "Rural Water Supply & Sanitation"


def test_live_triage_provider_receives_only_redacted_text():
    class RecordingTriageProvider:
        def __init__(self):
            self.calls = []

        def assess(self, **values):
            self.calls.append(values)
            return TriageResult(
                duplicate_review=DuplicateReview(
                    decision="abstained",
                    reason="The redacted submission is too short to compare.",
                ),
                spam=SpamReview(
                    decision="abstained",
                    reason_code="live_review_disabled_pending_redacted_adjudication",
                ),
            )

    provider = RecordingTriageProvider()
    result = _process(_processor(triage_provider=provider))

    assert provider.calls == [
        {
            "redacted_text": "[NAME] reports that the village pump is broken. Call [PHONE].",
            "district": "Cuttack",
            "submitted_on": datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
        }
    ]
    assert "Ramesh" not in provider.calls[0]["redacted_text"]
    assert "9876543210" not in provider.calls[0]["redacted_text"]
    assert result.triage.duplicate_review.decision == "abstained"


def test_default_live_triage_is_explicitly_abstained_pending_validation():
    result = _process(_processor())

    assert result.triage.duplicate is None
    assert result.triage.duplicate_review.decision == "not_indexed"
    assert result.triage.duplicate_review.reason
    # Now wired to bounded scorer: long enough legitimate text scores clean with a bounded score
    assert result.triage.spam.spam_score is not None
    assert 0.0 <= result.triage.spam.spam_score <= 1.0
    assert result.triage.spam.spam_reason in {
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    }
    assert result.triage.spam.evidence[0].kind == "repetition_collapse"


def test_triage_provider_outage_is_nonblocking_and_explicit():
    class FailingTriageProvider:
        def assess(self, **_values):
            raise TriageUnavailableError("database password must not reach the user")

    result = _process(_processor(triage_provider=FailingTriageProvider()))

    assert result.status == "Submitted"
    assert result.triage.duplicate is None
    assert result.triage.duplicate_review.decision == "unavailable"
    assert "password" not in (result.triage.duplicate_review.reason or "")
    assert result.triage.spam.decision == "abstained"
    assert result.triage.spam.reason_code == "advisory_provider_unavailable"


def test_pdf_preserves_all_ocr_but_gates_models_to_class_one_pages():
    def ocr(_bytes, _name):
        return OcrResult(
            full_text=(
                "Ramesh needs a road repair.\n\n"
                "IDENTITY 9876543210\n\n"
                "The damaged road blocks the school bus."
            ),
            pages=3,
            per_page=[
                ("Ramesh needs a road repair.", "Letter"),
                ("IDENTITY 9876543210", "Identification"),
                ("The damaged road blocks the school bus.", "Text Only"),
            ],
        )
    categorizer = RecordingCategorizer(category="Roads & Bridges")
    summarizer = RecordingSummarizer()
    processor = _processor(
        ocr=ocr,
        categorizer=categorizer,
        summarizer=summarizer,
    )

    result = _process(
        processor,
        text=None,
        document_name="complaint.pdf",
        document_bytes=b"%PDF-synthetic",
    )

    model_input = (
        "[NAME] needs a road repair.\n\n"
        "The damaged road blocks the school bus."
    )
    assert result.extraction.model_dump() == {
        "source": "document",
        "extracted_text": (
            "Ramesh needs a road repair.\n\n"
            "IDENTITY 9876543210\n\n"
            "The damaged road blocks the school bus."
        ),
        "ocr_model": "pytesseract",
        "pages": 3,
    }
    assert "IDENTITY [PHONE]" in result.redaction.redacted_text
    assert categorizer.inputs == [model_input]
    assert summarizer.inputs == [model_input]
    assert "IDENTITY" not in categorizer.inputs[0]
    assert result.routing.method == "rules"
    assert result.routing.dept == "Works"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"text": "   "},
        {"text": "valid", "document_name": "x.pdf", "document_bytes": b"x"},
        {"document_name": "x.pdf"},
        {"document_bytes": b"x"},
        {"document_name": "x.pdf", "document_bytes": b""},
        {"document_name": "x.txt", "document_bytes": b"x"},
    ],
)
def test_rejects_invalid_input_combinations(values):
    with pytest.raises(InferenceInputError):
        _processor().process(grievance_id="g", ticket_no="JS", **values)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (ValueError("bad PDF"), "corrupt or unsupported"),
        (OcrQualityError("collapsed"), "quality check"),
    ],
)
def test_rejects_corrupt_and_quality_rejected_documents(failure, message):
    def failing_ocr(_bytes, _name):
        raise failure

    with pytest.raises(InferenceInputError, match=message):
        _process(
            _processor(ocr=failing_ocr),
            text=None,
            document_name="bad.pdf",
            document_bytes=b"bad",
        )


def test_page_type_model_bug_is_not_reported_as_corrupt_document():
    """A page-type predictor bug surfaces mid-OCR as `_PageTypeModelError`
    (see `_guard_page_type_predict`), which is neither `ValueError` nor
    `IndexError` nor a named renderer/parser exception -- it must propagate
    unchanged (a 5xx upstream), not be reported as a 422 corrupt document."""

    def failing_ocr(_bytes, _name):
        raise _PageTypeModelError("id2label lookup failed")

    with pytest.raises(_PageTypeModelError, match="id2label lookup failed"):
        _process(
            _processor(ocr=failing_ocr),
            text=None,
            document_name="bad.pdf",
            document_bytes=b"bad",
        )


@pytest.mark.parametrize("failure", [ValueError("bad tensor shape"), IndexError(3)])
def test_guard_page_type_predict_reclassifies_predictor_failures(failure):
    def broken_predict(_image):
        raise failure

    guarded = _guard_page_type_predict(broken_predict)

    with pytest.raises(_PageTypeModelError):
        guarded(object())


def test_guard_page_type_predict_passes_through_successful_predictions():
    guarded = _guard_page_type_predict(lambda _image: "Letter")

    assert guarded(object()) == "Letter"


@pytest.mark.parametrize(
    ("ocr_result", "message"),
    [
        (OcrResult("  ", 1, [("  ", "Letter")]), "produced no text"),
        (OcrResult("partial", 50, [("partial", "Letter")], True), "page limit"),
        (
            OcrResult("identity", 1, [("identity", "Identification")]),
            "no grievance-bearing pages",
        ),
        (OcrResult("letter", 1, [("  ", "Letter")]), "no grievance-bearing pages"),
    ],
)
def test_rejects_unusable_ocr_results(ocr_result, message):
    with pytest.raises(InferenceInputError, match=message):
        _process(
            _processor(ocr=lambda _bytes, _name: ocr_result),
            text=None,
            document_name="bad.pdf",
            document_bytes=b"bad",
        )


def test_non_english_is_uncategorized_and_uses_routing_fallback():
    categorizer = RecordingCategorizer()
    summarizer = RecordingSummarizer()
    processor = _processor(
        categorizer=categorizer,
        summarizer=summarizer,
        english=False,
        language="or",
    )

    result = _process(processor, text="ମୋ ଗାଁର ରାସ୍ତା ଭାଙ୍ଗି ଯାଇଛି।")

    assert categorizer.inputs == []
    assert summarizer.inputs == []
    assert result.classification.category == "Uncategorized"
    assert result.classification.language == "or"
    assert result.summary == UNSUPPORTED_LANGUAGE_SUMMARY
    assert result.routing.method == "fallback"


def test_non_english_pdf_skips_bart_summary_on_class_one_pages():
    """(Codex P2 re-review on PR #25) The same language gate that skips the
    English-only categorizer must also skip BART summarization -- on the
    document path this means the gated class-1 pages that feed the model
    text source, not just the typed-text path."""

    def ocr(_bytes, _name):
        return OcrResult(
            full_text="ମୋ ଗାଁର ରାସ୍ତା ଭାଙ୍ଗି ଯାଇଛି।",
            pages=1,
            per_page=[("ମୋ ଗାଁର ରାସ୍ତା ଭାଙ୍ଗି ଯାଇଛି।", "Letter")],
        )

    categorizer = RecordingCategorizer()
    summarizer = RecordingSummarizer()
    processor = _processor(
        ocr=ocr,
        categorizer=categorizer,
        summarizer=summarizer,
        english=False,
        language="or",
    )

    result = _process(
        processor,
        text=None,
        document_name="complaint.pdf",
        document_bytes=b"%PDF-synthetic",
    )

    assert categorizer.inputs == []
    assert summarizer.inputs == []
    assert result.classification.category == "Uncategorized"
    assert result.summary == UNSUPPORTED_LANGUAGE_SUMMARY
    assert result.routing.method == "fallback"


def test_english_pdf_still_gets_real_bart_summary():
    """Guards the happy path byte-for-byte: English-compatible document text
    must still reach BART and get the real (non-fallback) summary."""

    def ocr(_bytes, _name):
        return OcrResult(
            full_text="Ramesh needs a road repair.",
            pages=1,
            per_page=[("Ramesh needs a road repair.", "Letter")],
        )

    categorizer = RecordingCategorizer(category="Roads & Bridges")
    summarizer = RecordingSummarizer()
    processor = _processor(
        ocr=ocr,
        categorizer=categorizer,
        summarizer=summarizer,
        english=True,
        language="en",
    )

    result = _process(
        processor,
        text=None,
        document_name="complaint.pdf",
        document_bytes=b"%PDF-synthetic",
    )

    model_input = "[NAME] needs a road repair."
    assert summarizer.inputs == [model_input]
    assert result.summary == f"summary: {model_input}"
    assert result.classification.category == "Roads & Bridges"


def test_warm_components_are_constructed_once_and_reused():
    RecordingCategorizer.instances = 0
    RecordingSummarizer.instances = 0
    categorizer = RecordingCategorizer()
    summarizer = RecordingSummarizer()
    processor = _processor(categorizer=categorizer, summarizer=summarizer)

    _process(processor)
    _process(processor, grievance_id="g2", ticket_no="JS2")

    assert RecordingCategorizer.instances == 1
    assert RecordingSummarizer.instances == 1
    assert len(categorizer.inputs) == 2
    assert len(summarizer.inputs) == 2
