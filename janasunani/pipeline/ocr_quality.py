"""OCR output quality heuristics.

The DSI technical report identified one signature failure mode for
DeepSeek-OCR: repetition collapse, where generation gets stuck in a loop and
the same phrase fills the page. Their measured rule — top-trigram share > 0.5
=> erroneous — only fires when a *single word* repeats, because a looping
k-word phrase spreads its occurrences over k distinct trigrams (top share
tends to 1/k). The detector here generalizes it: the *repeated-trigram share*
(fraction of trigram occurrences whose trigram appears more than once) is
~1.0 for a loop of any phrase length and ~0 for prose, so the same 0.5
threshold separates cleanly. `top_trigram_share` is kept as DSI reported it,
for the failure-reason message and comparability with their numbers.

Lives at the light `janasunani.pipeline` level (like `ticket.py`), not inside
the ocr_extraction package, because that package's __init__ pulls the heavy
stage stack — this module must stay importable in CI with no extras.
"""

from __future__ import annotations

from collections import Counter

# Above this repeated-trigram share the extraction is discarded as collapsed
# (threshold carried over from DSI's "> 0.5 => erroneous" rule).
TRIGRAM_COLLAPSE_THRESHOLD = 0.5

# Collapse produces long looping output; legitimately terse pages (stamps,
# short headers) can repeat a phrase without being failures, so texts shorter
# than this many words are never flagged.
MIN_WORDS_FOR_COLLAPSE_CHECK = 20


def _trigram_counts(text: str) -> Counter:
    words = text.split()
    return Counter(zip(words, words[1:], words[2:]))


def top_trigram_share(text: str) -> float:
    """Share of the text's word-trigram occurrences taken by the single most
    common trigram (the metric as the DSI report defined it).

    0.0 for texts with fewer than 3 words (no trigrams to measure).
    """
    counts = _trigram_counts(text)
    if not counts:
        return 0.0
    return max(counts.values()) / counts.total()


def repeated_trigram_share(text: str) -> float:
    """Share of the text's word-trigram occurrences whose trigram occurs more
    than once. ~1.0 when a phrase of any length is looping, ~0 for prose.

    0.0 for texts with fewer than 3 words.
    """
    counts = _trigram_counts(text)
    if not counts:
        return 0.0
    repeated = sum(c for c in counts.values() if c > 1)
    return repeated / counts.total()


def is_repetition_collapsed(
    text: str, threshold: float = TRIGRAM_COLLAPSE_THRESHOLD
) -> bool:
    """True when the text looks like a repetition-collapsed OCR output."""
    if len(text.split()) < MIN_WORDS_FOR_COLLAPSE_CHECK:
        return False
    return repeated_trigram_share(text) > threshold
