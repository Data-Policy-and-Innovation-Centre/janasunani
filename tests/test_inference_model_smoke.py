from __future__ import annotations

import os
from pathlib import Path

import pytest

from janasunani.config import MODELS_DIR
from janasunani.inference.service import build_processor


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
