"""Pricing module — frozen ROADMAP §5.5 rate card."""

import pytest

from janasunani.evaluation import pricing


def test_vision_rates_frozen():
    assert pricing.VISION_DIGITISE_RUPEES_PER_PAGE == 0.50
    assert pricing.VISION_EXTRACT_RUPEES_PER_PAGE == 1.00
    assert pricing.VISION_BOTH_RUPEES_PER_PAGE == 1.50
    assert pricing.VISION_DIGITISE_RATE == 0.50
    assert pricing.VISION_EXTRACT_RATE == 1.00
    assert pricing.VISION_BOTH_RATE == 1.50
    assert pricing.VISION_BOTH_RUPEES_PER_PAGE == pricing.VISION_DIGITISE_RUPEES_PER_PAGE + pricing.VISION_EXTRACT_RUPEES_PER_PAGE


def test_105b_rates_frozen():
    assert pricing.SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS == 29.28
    assert pricing.SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS == 73.20
    assert pricing.SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS == 10.98
    assert pricing.SARVAM_105B_INPUT_RUPEES_PER_M_TOKENS == 29.28
    assert pricing.SARVAM_105B_OUTPUT_RUPEES_PER_M_TOKENS == 73.20
    assert pricing.SARVAM_105B_INPUT_RUPEES_PER_1K_TOKENS == pytest.approx(0.02928)
    assert pricing.SARVAM_105B_OUTPUT_RUPEES_PER_1K_TOKENS == pytest.approx(0.0732)


def test_local_cost_zero():
    assert pricing.LOCAL_API_COST_RUPEES == 0.0
    assert pricing.LOCAL_COST == 0.0
    assert pricing.local_cost() == 0.0
    assert pricing.LOCAL_API_COST_RUPEES_PER_PAGE == 0.0


def test_vision_rate_for_arm():
    assert pricing.vision_rate_for_arm("digitise") == 0.50
    assert pricing.vision_rate_for_arm("digitize") == 0.50
    assert pricing.vision_rate_for_arm("extract") == 1.00
    assert pricing.vision_rate_for_arm("both") == 1.50
    assert pricing.vision_rate_for_arm("Digitise") == 0.50
    assert pricing.vision_rate_for_arm("BOTH") == 1.50
    with pytest.raises(ValueError):
        pricing.vision_rate_for_arm("unknown")


def test_vision_cost():
    assert pricing.vision_cost(0, "digitise") == 0.0
    assert pricing.vision_cost(10, "digitise") == pytest.approx(5.0)
    assert pricing.vision_cost(10, "extract") == pytest.approx(10.0)
    assert pricing.vision_cost(10, "both") == pytest.approx(15.0)
    assert pricing.vision_cost(500, "digitise") == pytest.approx(250.0)
    assert pricing.vision_cost(500, "both") == pytest.approx(750.0)
    with pytest.raises(ValueError):
        pricing.vision_cost(-1, "digitise")


def test_text_cost():
    assert pricing.text_cost(input_tokens=1_000_000) == pytest.approx(29.28)
    assert pricing.text_cost(output_tokens=1_000_000) == pytest.approx(73.20)
    assert pricing.text_cost(cached_tokens=1_000_000) == pytest.approx(10.98)
    assert pricing.text_cost(input_tokens=1_000_000, output_tokens=1_000_000) == pytest.approx(102.48)
    assert pricing.text_cost(input_tokens=275_000_000) == pytest.approx(8052.0, rel=1e-4)
    with pytest.raises(ValueError):
        pricing.text_cost(input_tokens=-1)


def test_cost_per_doc():
    assert pricing.cost_per_doc(pages=10, arm="digitise") == pytest.approx(5.0)
    assert pricing.cost_per_doc(pages=10, arm="extract") == pytest.approx(10.0)
    assert pricing.cost_per_doc(pages=10, arm="both") == pytest.approx(15.0)
    assert pricing.cost_per_doc(pages=2, arm="digitise", input_tokens=500_000) == pytest.approx(1.0 + 14.64)
    assert pricing.cost_per_doc(pages=0, arm="digitise", input_tokens=1_000_000) == pytest.approx(29.28)
    assert pricing.cost_per_doc(pages=5, arm="digitise", input_tokens=0, output_tokens=0) == pytest.approx(2.5)


def test_cost_per_1k_tokens():
    assert pricing.cost_per_1k_tokens(1000, kind="input") == pytest.approx(0.02928)
    assert pricing.cost_per_1k_tokens(1000, kind="output") == pytest.approx(0.0732)
    assert pricing.cost_per_1k_tokens(1000, kind="cached") == pytest.approx(0.01098)
    with pytest.raises(ValueError):
        pricing.cost_per_1k_tokens(-1, kind="input")
    with pytest.raises(ValueError):
        pricing.cost_per_1k_tokens(100, kind="bogus")


def test_pricing_table():
    tbl = pricing.pricing_table()
    assert tbl["vision_digitise_rupees_per_page"] == 0.50
    assert tbl["vision_extract_rupees_per_page"] == 1.00
    assert tbl["vision_both_rupees_per_page"] == 1.50
    assert tbl["sarvam_105b_input_rupees_per_million_tokens"] == 29.28
    assert tbl["sarvam_105b_output_rupees_per_million_tokens"] == 73.20
    assert tbl["local_api_cost_rupees"] == 0.0
