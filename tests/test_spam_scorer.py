"""Bounded spam scorer — contract tests (Unit 2b)."""

import pathlib

from janasunani.pipeline import spam
from janasunani.pipeline.spam import (
    SPAM_REASONS,
    SPAM_VERSION,
    _parse_slice,
    score_spam,
)


def test_score_bounded_and_reason_valid():
    cases = [
        "",
        "hi",
        "test",
        "The hand pump in our village has been broken for two months and we request repair.",
        "no specific grievance",
        "repeat this phrase " * 30,
        "A" * 500,
    ]
    for text in cases:
        s = score_spam(text)
        assert 0.0 <= s.spam_score <= 1.0, f"score out of bounds for {text!r}"
        assert s.spam_reason in SPAM_REASONS
        assert s.method == SPAM_VERSION
        assert s.evidence


def test_repetition_collapse_flagged():
    collapsed_text = "repeat this phrase " * 40
    s = score_spam(collapsed_text, is_repetition_collapsed=True)
    assert s.spam_reason == "repetition_collapse"
    assert s.spam_score >= 0.5
    # Evidence must record the collapse observation
    assert any(e.kind == "repetition_collapse" and e.observed is True for e in s.evidence)


def test_length_too_short_flagged():
    s = score_spam("hi")
    assert s.spam_reason == "length_too_short"
    assert s.spam_score >= 0.5
    s2 = score_spam("a b")
    assert s2.spam_reason == "length_too_short"


def test_low_signal_no_grievance():
    s = score_spam("no specific grievance")
    assert s.spam_reason == "low_signal_no_grievance"
    assert s.spam_score >= 0.5


def test_low_signal_details_inadequate():
    # Short/vague but not empty and not generic test token
    s = score_spam("need help")
    assert s.spam_reason in {"low_signal_details_inadequate", "length_too_short", "low_signal_no_grievance"}
    # A slightly longer but still short text should map to details inadequate
    s2 = score_spam("road broken")
    assert s2.spam_reason in {"low_signal_details_inadequate", "length_too_short"}


def test_clean_long_grievance():
    long_text = (
        "The drinking water supply in our village has been disrupted for over a month. "
        "The hand pump installed last year is broken and the pipeline has not been "
        "repaired despite multiple complaints to the block office. Residents are "
        "forced to fetch water from a distant pond. Kindly arrange urgent repair."
    )
    s = score_spam(long_text)
    assert s.spam_reason == "clean"
    assert s.spam_score < 0.5
    assert 0.0 <= s.spam_score <= 1.0


def test_duplicate_family_never_scored_as_spam():
    # Legitimate grievance prose that happens to be a duplicate should score clean,
    # not as spam. The scorer must not flag duplicate-like legitimate text.
    duplicate_like = (
        "Respected sir, the road from our village to the block headquarters has been "
        "damaged for six months and school children face difficulty. Kindly repair it."
    )
    s = score_spam(duplicate_like)
    # Must be clean / low score — never spam
    assert s.spam_score < 0.5
    assert s.spam_reason == "clean"


def test_not_within_purview_never_scored_as_spam():
    # A legitimate grievance discarded as "not within purview" is a routing
    # failure, not spam. Its text is still legitimate and must score clean.
    purview_like = (
        "I applied for a central government pension scheme and my application is pending "
        "with the central office. The state grievance cell may not have jurisdiction but "
        "the grievance itself is well-formed and detailed."
    )
    s = score_spam(purview_like)
    assert s.spam_score < 0.5
    assert s.spam_reason == "clean"


def test_evidence_present():
    s = score_spam("hello world this is a test of the emergency broadcast system with enough words to be clean")
    assert any(e.kind == "repetition_collapse" for e in s.evidence)
    assert any(e.kind == "char_len" for e in s.evidence)
    assert any(e.kind == "word_count" for e in s.evidence)


def test_never_reads_grievance_raw():
    src = pathlib.Path(spam.__file__).read_text(encoding="utf-8")
    # Guard: scorer must never touch the raw grievance column
    assert "complaints.grievance" not in src
    assert "Complaint.grievance" not in src
    assert "grievance_raw" not in src.lower()


def test_parse_slice():
    d, y = _parse_slice("Sambalpur/2024")
    assert d == "Sambalpur" and y == 2024
    # also via CLI pathtested elsewhere


def test_is_repetition_collapsed_param():
    text = "ordinary grievance text with enough length to be clean and not collapsed"
    s_true = score_spam(text, is_repetition_collapsed=True)
    s_false = score_spam(text, is_repetition_collapsed=False)
    assert s_true.spam_reason == "repetition_collapse"
    assert s_false.spam_reason == "clean"


def test_spam_score_v1_version():
    s = score_spam("clean text " * 10)
    assert s.method == "spam-v1-bounded"
