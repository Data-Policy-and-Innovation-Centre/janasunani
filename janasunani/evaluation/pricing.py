"""Pricing constants for the benchmark cost harness — single source.

Roadmap source: ``docs/ROADMAP.md`` §5.5 (checked 2026-08-07 against the
Sarvam dashboard billing page). Values are frozen here so every cost
column in the benchmark report and every MLflow benchmark run uses the
same arithmetic.

* Vision ``digitise`` (OCR → Markdown) and ``extract`` (schema → JSON)
  are **separate endpoints and bill separately**. ``both`` is the sum.
* Sarvam-105B token pricing is tiered; the benchmark uses the
  pay-as-you-go input / output rates (cached rate kept for planning).
* The local pipeline has **no API cost** — the report shows wall-clock
  seconds/doc and an optional compute-cost only when an instance hourly
  rate is configured.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Vision — per page (ROADMAP Table §5.5, verified 2026-08-07)
# ---------------------------------------------------------------------------
VISION_DIGITISE_RUPEES_PER_PAGE: float = 0.50
VISION_EXTRACT_RUPEES_PER_PAGE: float = 1.00
VISION_BOTH_RUPEES_PER_PAGE: float = 1.50

# Aliases for consumers that prefer shorter names
VISION_DIGITISE_RATE: float = VISION_DIGITISE_RUPEES_PER_PAGE
VISION_EXTRACT_RATE: float = VISION_EXTRACT_RUPEES_PER_PAGE
VISION_BOTH_RATE: float = VISION_BOTH_RUPEES_PER_PAGE

# ---------------------------------------------------------------------------
# Sarvam-105B text — per 1M tokens (ROADMAP Table §5.5)
# Withdrawn 30B rates are not retained. Tiers are pay-as-you-go.
# ---------------------------------------------------------------------------
SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS: float = 29.28
SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS: float = 73.20
SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS: float = 10.98

# Short aliases
SARVAM_105B_INPUT_RUPEES_PER_M_TOKENS: float = SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS
SARVAM_105B_OUTPUT_RUPEES_PER_M_TOKENS: float = SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS
SARVAM_105B_CACHED_RUPEES_PER_M_TOKENS: float = SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS

# Per-1k derived rates (convenience — same arithmetic, more readable docs)
SARVAM_105B_INPUT_RUPEES_PER_1K_TOKENS: float = SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS / 1000.0
SARVAM_105B_OUTPUT_RUPEES_PER_1K_TOKENS: float = SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS / 1000.0
SARVAM_105B_CACHED_RUPEES_PER_1K_TOKENS: float = SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS / 1000.0

# ---------------------------------------------------------------------------
# Local pipeline — no API cost
# ---------------------------------------------------------------------------
LOCAL_API_COST_RUPEES: float = 0.0
LOCAL_API_COST_RUPEES_PER_PAGE: float = 0.0
LOCAL_COST: float = LOCAL_API_COST_RUPEES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_VISION_ARMS = {"digitise", "extract", "both"}


def vision_rate_for_arm(arm: str) -> float:
    """Rate per page for a Vision arm.

    Args:
        arm: ``digitise`` (₹0.50), ``extract`` (₹1.00), or ``both`` (₹1.50).

    Raises:
        ValueError: for unknown arm names.
    """
    normalized = arm.strip().lower().replace("-", "_")
    if normalized in {"digitise", "digitize"}:
        return VISION_DIGITISE_RUPEES_PER_PAGE
    if normalized == "extract":
        return VISION_EXTRACT_RUPEES_PER_PAGE
    if normalized == "both":
        return VISION_BOTH_RUPEES_PER_PAGE
    raise ValueError(f"unknown Vision arm {arm!r}; expected one of {_VALID_VISION_ARMS}")


def vision_cost(pages: int, arm: str = "digitise") -> float:
    """Vision cost for *pages* pages on the given *arm*.

    Returns ``pages * rate_per_page``. ``pages`` must be >= 0.
    """
    if pages < 0:
        raise ValueError("pages must be >= 0")
    return float(pages) * vision_rate_for_arm(arm)


def text_cost(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """Token cost on Sarvam-105B (₹ / 1M tokens).

    Args:
        input_tokens: billable input tokens.
        output_tokens: billable output tokens.
        cached_tokens: prompt-cached tokens (₹10.98 / 1M).

    All counts must be >= 0. Token counts come from the tokenizer;
    for OCR-only benchmarks this term is zero and the report shows
    ``₹/page`` only.
    """
    if input_tokens < 0 or output_tokens < 0 or cached_tokens < 0:
        raise ValueError("token counts must be >= 0")
    return (
        input_tokens / 1_000_000.0 * SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS
        + output_tokens / 1_000_000.0 * SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS
        + cached_tokens / 1_000_000.0 * SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS
    )


def text_cost_per_1k(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """Alias that reports the same cost but documents per-1k-token use."""
    return text_cost(input_tokens, output_tokens, cached_tokens)


def cost_per_doc(
    pages: int,
    arm: str = "digitise",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    """Per-document cost matching the plan's formula::

        cost_doc = pages × rate_per_page
                 + (input_tokens + output_tokens) / 1M × rate_per_1M

    For OCR-only benchmarks the token term is zero and the result is
    exactly ``pages × rate_per_page``. For text-only (105B) arms set
    ``pages=0``.
    """
    return vision_cost(pages, arm=arm) + text_cost(input_tokens, output_tokens, cached_tokens)


def cost_per_1k_tokens(
    tokens: int,
    kind: str = "input",
) -> float:
    """Cost for *tokens* at the per-1k rate for one token kind.

    Args:
        tokens: number of tokens.
        kind: ``input`` / ``output`` / ``cached``.
    """
    if tokens < 0:
        raise ValueError("tokens must be >= 0")
    k = kind.strip().lower()
    if k == "input":
        return tokens / 1000.0 * SARVAM_105B_INPUT_RUPEES_PER_1K_TOKENS
    if k == "output":
        return tokens / 1000.0 * SARVAM_105B_OUTPUT_RUPEES_PER_1K_TOKENS
    if k in {"cached", "cache"}:
        return tokens / 1000.0 * SARVAM_105B_CACHED_RUPEES_PER_1K_TOKENS
    raise ValueError(f"unknown token kind {kind!r}; expected input/output/cached")


def local_cost() -> float:
    """API cost for the local pipeline — always zero."""
    return LOCAL_API_COST_RUPEES


def pricing_table() -> dict[str, float]:
    """Return the frozen rate card as a dict (for JSON embedding)."""
    return {
        "vision_digitise_rupees_per_page": VISION_DIGITISE_RUPEES_PER_PAGE,
        "vision_extract_rupees_per_page": VISION_EXTRACT_RUPEES_PER_PAGE,
        "vision_both_rupees_per_page": VISION_BOTH_RUPEES_PER_PAGE,
        "sarvam_105b_input_rupees_per_million_tokens": SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS,
        "sarvam_105b_output_rupees_per_million_tokens": SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS,
        "sarvam_105b_cached_rupees_per_million_tokens": SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS,
        "sarvam_105b_input_rupees_per_1k_tokens": SARVAM_105B_INPUT_RUPEES_PER_1K_TOKENS,
        "sarvam_105b_output_rupees_per_1k_tokens": SARVAM_105B_OUTPUT_RUPEES_PER_1K_TOKENS,
        "local_api_cost_rupees": LOCAL_API_COST_RUPEES,
    }


__all__ = [
    "VISION_DIGITISE_RUPEES_PER_PAGE",
    "VISION_EXTRACT_RUPEES_PER_PAGE",
    "VISION_BOTH_RUPEES_PER_PAGE",
    "VISION_DIGITISE_RATE",
    "VISION_EXTRACT_RATE",
    "VISION_BOTH_RATE",
    "SARVAM_105B_INPUT_RUPEES_PER_MILLION_TOKENS",
    "SARVAM_105B_OUTPUT_RUPEES_PER_MILLION_TOKENS",
    "SARVAM_105B_CACHED_RUPEES_PER_MILLION_TOKENS",
    "SARVAM_105B_INPUT_RUPEES_PER_M_TOKENS",
    "SARVAM_105B_OUTPUT_RUPEES_PER_M_TOKENS",
    "SARVAM_105B_CACHED_RUPEES_PER_M_TOKENS",
    "SARVAM_105B_INPUT_RUPEES_PER_1K_TOKENS",
    "SARVAM_105B_OUTPUT_RUPEES_PER_1K_TOKENS",
    "SARVAM_105B_CACHED_RUPEES_PER_1K_TOKENS",
    "LOCAL_API_COST_RUPEES",
    "LOCAL_API_COST_RUPEES_PER_PAGE",
    "LOCAL_COST",
    "vision_rate_for_arm",
    "vision_cost",
    "text_cost",
    "text_cost_per_1k",
    "cost_per_doc",
    "cost_per_1k_tokens",
    "local_cost",
    "pricing_table",
]
