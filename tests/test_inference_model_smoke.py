from __future__ import annotations

import os
from pathlib import Path

import pytest

from janasunani.config import MODELS_DIR
from janasunani.inference.service import build_processor

_DEMO_FIXTURE = Path(__file__).parent / "fixtures" / "demo_letter.png"


@pytest.mark.skipif(
    not os.environ.get("JANASUNANI_RUN_MODEL_SMOKE"),
    reason="set JANASUNANI_RUN_MODEL_SMOKE=1 to run local real models",
)
def test_real_models_process_synthetic_english_text():
    models_dir = Path(os.environ.get("JANASUNANI_MODELS_DIR", MODELS_DIR))
    required = (
        models_dir / "categorizer" / "config.json",
        models_dir / "categorizer" / "label_encoder_ROS_wDOCS_english.pkl",
        models_dir / "page_type_classifier" / "vit_type_classifier" / "config.json",
    )
    if not all(path.is_file() for path in required):
        pytest.skip("local DVC model artifacts are not available")

    processor = build_processor(models_dir)
    result = processor.process(
        grievance_id="synthetic-smoke",
        ticket_no="JSSYNTHETIC",
        text=(
            "The public road beside our synthetic village school has several "
            "large potholes and becomes impassable after rain. Please arrange "
            "an inspection and repair so the school bus can travel safely."
        ),
        district="Cuttack",
    )

    assert result.extraction.extracted_text
    assert result.redaction.redacted_text
    assert result.classification.category
    assert result.summary
    assert result.routing.method != "mock"
    assert result.routing.method in {"learned", "rules", "fallback"}
    # Triage contract: bounded spam score must be present and numeric
    assert result.triage.spam.spam_score is not None
    assert 0.0 <= result.triage.spam.spam_score <= 1.0
    assert result.triage.spam.spam_reason in {
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    }
    assert result.triage.duplicate_review.decision in {
        "matched",
        "no_match",
        "abstained",
        "not_indexed",
        "unavailable",
    }


@pytest.mark.skipif(
    not os.environ.get("JANASUNANI_RUN_MODEL_SMOKE"),
    reason="set JANASUNANI_RUN_MODEL_SMOKE=1 to run local real models",
)
def test_real_models_process_document_sample():
    """Opt-in document path via committed synthetic fixture; no PII, no data/."""
    models_dir = Path(os.environ.get("JANASUNANI_MODELS_DIR", MODELS_DIR))
    required = (
        models_dir / "categorizer" / "config.json",
        models_dir / "categorizer" / "label_encoder_ROS_wDOCS_english.pkl",
        models_dir / "page_type_classifier" / "vit_type_classifier" / "config.json",
    )
    if not all(path.is_file() for path in required):
        pytest.skip("local DVC model artifacts are not available")
    if not _DEMO_FIXTURE.is_file():
        pytest.skip("synthetic document fixture tests/fixtures/demo_letter.png not found")

    processor = build_processor(models_dir)
    document_bytes = _DEMO_FIXTURE.read_bytes()
    result = processor.process(
        grievance_id="synthetic-doc-smoke",
        ticket_no="JSSYNTHETIC-DOC",
        document_name="demo_letter.png",
        document_bytes=document_bytes,
        district="Sambalpur",
    )

    assert result.extraction.source == "document"
    assert result.extraction.extracted_text
    assert result.redaction.redacted_text
    assert result.classification.category
    assert result.summary
    assert result.routing.method != "mock"
    assert result.routing.method in {"learned", "rules", "fallback"}
    # Triage spam score is bounded and never mock
    assert result.triage.spam.spam_score is not None
    assert 0.0 <= result.triage.spam.spam_score <= 1.0
    assert result.triage.spam.spam_reason in {
        "low_signal_details_inadequate",
        "low_signal_no_grievance",
        "repetition_collapse",
        "length_too_short",
        "clean",
    }
    assert result.triage.duplicate_review.decision in {
        "matched",
        "no_match",
        "abstained",
        "not_indexed",
        "unavailable",
    }
