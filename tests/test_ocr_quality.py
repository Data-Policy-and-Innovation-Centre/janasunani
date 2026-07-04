"""Repetition-collapse heuristic for DeepSeek OCR output (ocr_quality).

Pure-function tests — the module is deliberately stdlib-only so these run in
CI without any pipeline extras installed.
"""

from janasunani.pipeline.ocr_quality import (
    MIN_WORDS_FOR_COLLAPSE_CHECK,
    is_repetition_collapsed,
    repeated_trigram_share,
    top_trigram_share,
)

NORMAL_PAGE = (
    "To the District Collector, Khordha. Respected sir, I am writing to "
    "bring to your notice that the hand pump in our village has been out "
    "of order for three months and repeated complaints to the block office "
    "have produced no action. Kindly look into the matter urgently."
)


def test_normal_page_not_collapsed():
    assert not is_repetition_collapsed(NORMAL_PAGE)
    assert repeated_trigram_share(NORMAL_PAGE) < 0.25
    assert top_trigram_share(NORMAL_PAGE) < 0.25


def test_single_word_collapse_detected():
    # The case DSI's raw top-trigram rule catches.
    page = "9999 " * 60
    assert top_trigram_share(page) == 1.0
    assert is_repetition_collapsed(page)


def test_phrase_loop_collapse_detected():
    # A looping k-word phrase spreads over k distinct trigrams, so the DSI
    # top-trigram share plateaus at ~1/k — this is exactly why the detector
    # uses the repeated-trigram share instead.
    page = "the said land is " * 40
    assert top_trigram_share(page) < 0.3
    assert repeated_trigram_share(page) == 1.0
    assert is_repetition_collapsed(page)


def test_short_repetitive_text_never_flagged():
    # Terse stamps/headers can legitimately repeat; below the word floor the
    # check must not fire even at 100% repeated share.
    stamp = "no objection certificate " * 3
    assert len(stamp.split()) < MIN_WORDS_FOR_COLLAPSE_CHECK
    assert repeated_trigram_share(stamp) > 0.5
    assert not is_repetition_collapsed(stamp)


def test_degenerate_inputs():
    assert top_trigram_share("") == 0.0
    assert repeated_trigram_share("") == 0.0
    assert top_trigram_share("two words") == 0.0
    assert not is_repetition_collapsed("")
