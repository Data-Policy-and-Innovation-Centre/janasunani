"""Presidio PII stage: real redaction on realistic grievance text.

Runs the actual analyzer/anonymizer (no mocks); skips only where the
pipeline-core extra (presidio + spaCy model) isn't installed, e.g. CI.
"""

import sqlite3

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_sm")

from janasunani.pipeline.config import PipelineConfig  # noqa: E402
from janasunani.pipeline.db import initialize_database  # noqa: E402
from janasunani.pipeline.stages.pii_tagger import (  # noqa: E402
    detect_pii_spans,
    is_government_email,
    redact_text,
    run_pii_tagger,
)


def test_indian_pii_patterns_redacted():
    text = (
        "My name is Ramesh Kumar, mobile +91 98765 43210, aadhaar 2345 6789 0123, "
        "PAN ABCDE1234F, email ramesh.k@example.com. The water supply in ward 7 "
        "has been broken for 3 weeks."
    )
    red = redact_text(text)
    assert "98765" not in red and "[PHONE]" in red
    assert "6789" not in red and "[AADHAAR]" in red
    assert "ABCDE1234F" not in red and "[PAN]" in red
    assert "ramesh.k@example.com" not in red and "[EMAIL]" in red
    # the complaint substance survives
    assert "water supply" in red and "ward 7" in red
    # ward number is not a phone/aadhaar
    assert "3 weeks" in red


def test_odia_digit_numbers_redacted():
    """Numbers written in Odia numerals must not escape: Presidio's \\d is
    Unicode-aware but the [6-9]/[2-9] anchors are ASCII-only, so detection
    runs on a digit-normalized copy (offsets carry over 1:1)."""
    # ୯୮୭୬୫୪୩୨୧୦ = 9876543210
    text = "ମୋ ନମ୍ବର ୯୮୭୬୫୪୩୨୧୦ please call. The year ୨୦୨୪ saw no repairs."
    red = redact_text(text)
    assert "୯୮୭୬୫୪୩୨୧୦" not in red and "[PHONE]" in red
    # redaction replaces spans in the ORIGINAL text: surrounding Odia script
    # and the non-PII Odia year are untouched, not transliterated
    assert "ମୋ ନମ୍ବର" in red
    assert "୨୦୨୪" in red


def test_no_token_window_truncation():
    """The legacy CRF silently dropped text past 512 tokens. The whole page
    must survive, with PII at the far end still redacted."""
    filler = "The drainage overflow near the market has not been cleared. " * 400
    text = filler + "Contact me on 9876543210."
    red = redact_text(text)
    assert "9876543210" not in red and "[PHONE]" in red
    assert len(red) > len(filler) * 0.95  # nothing truncated


def test_stage_covers_mixed_language_pages(tmp_path):
    """LIKE '%English%': mixed 'English, Odia' pages get redacted too
    (the legacy equality filter skipped them entirely)."""
    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO pages (doc_id, page_number, full_path, page_id, language, extracted_text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("D1", 1, "D1.pdf", "p1", "English", "call 9876543210 about roads"),
                ("D2", 1, "D2.pdf", "p2", "English, Odia", "ମୋ ନମ୍ବର 9123456789 please help"),
                ("D3", 1, "D3.pdf", "p3", "Odia", "କେବଳ ଓଡ଼ିଆ ପାଠ୍ୟ"),
            ],
        )
        con.commit()

    run_pii_tagger(
        PipelineConfig(input_dir=tmp_path, db_path=db, models_dir=tmp_path)
    )

    with sqlite3.connect(db) as con:
        got = dict(con.execute("SELECT page_id, redacted_text FROM pages").fetchall())
    assert "[PHONE]" in got["p1"] and "9876543210" not in got["p1"]
    assert "[PHONE]" in got["p2"] and "9123456789" not in got["p2"]  # mixed covered
    assert got["p3"] is None  # pure Odia: out of scope (as before)


def test_rerun_is_idempotent_and_terminates(tmp_path):
    db = tmp_path / "pipeline.sqlite"
    initialize_database(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO pages (doc_id, page_number, full_path, page_id, language, extracted_text)"
            " VALUES ('D1', 1, 'D1.pdf', 'p1', 'English', 'no pii here at all')"
        )
        con.commit()

    cfg = PipelineConfig(input_dir=tmp_path, db_path=db, models_dir=tmp_path)
    run_pii_tagger(cfg)
    run_pii_tagger(cfg)  # second run: nothing pending, returns immediately

    with sqlite3.connect(db) as con:
        (red,) = con.execute("SELECT redacted_text FROM pages").fetchone()
    assert red == "no pii here at all"


# --- #55: dates and file numbers must not be tagged PHONE ------------------


@pytest.mark.parametrize(
    "text",
    [
        "The hearing was held on 06.05.2025 at the block office.",
        "Order dated 06-05-2025 was not implemented.",
        "Application dated 12/03/2024 was rejected.",
    ],
)
def test_dates_are_not_tagged_phone(text):
    assert not [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert redact_text(text) == text


def test_bare_file_number_in_letter_context_is_not_tagged_phone():
    text = "Letter no. 1234567890 dated 12.03.2024 refers."
    assert not [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "My mobile is 9876543210, please call.",
        "Reach me at +91 98765 43210 today.",
        "Call 09876543210 for updates.",
        "Contact on 98765 43210 anytime.",
        "Contact on 98765-43210 anytime.",
        # Codex review on #91 (P1): removing the built-in PhoneRecognizer
        # left only the bare/5-5 IN_MOBILE pattern, so these two common
        # groupings escaped redaction entirely.
        "My number is 9876 543 210, please call.",  # 4-3-3
        "My number is 987 654 3210, please call.",  # 3-3-4
    ],
)
def test_mobiles_still_detected_across_formats(text):
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    red = redact_text(text)
    assert "9876543210" not in red and "[PHONE]" in red


@pytest.mark.parametrize(
    "text",
    [
        "Office 0674 2536789 during hours.",
        "Office 0674-2536789 during hours.",
        # Codex review on #91 (P2): these separator styles escaped every
        # PHONE recognizer once the built-in was removed.
        "Office (0674) 2536789 during hours.",
        "Office 0674/2536789 during hours.",
        "Office 0674 253 6789 during hours.",
    ],
)
def test_landline_still_detected(text):
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    red = redact_text(text)
    assert "0674" not in red and "6789" not in red and "[PHONE]" in red


def test_split_subscriber_landline_also_matches_new_mobile_pattern_harmlessly():
    """Recognizer interaction found while broadening formats for the Codex
    review on #91: IN_LANDLINE's split-subscriber pattern and IN_MOBILE's
    new 3-3-4 pattern (added for '987 654 3210') both match
    '0674 253 6789' on the exact same span -- the STD code doubles as a
    valid leading digit + trunk-zero mobile shape. Both normalize to PHONE
    and land on identical (start, end), so detect_pii_spans's dict-keyed
    dedup collapses them to one span (keeping the higher score) and
    redact_text produces a single [PHONE], not a double replacement."""
    text = "Office 0674 253 6789 during hours."
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    assert redact_text(text) == "Office [PHONE] during hours."


def test_aadhaar_unaffected_by_phone_changes():
    text = "Aadhaar number 2345 6789 0123 attached."
    spans = detect_pii_spans(text)
    assert any(s.entity == "AADHAAR" for s in spans)
    assert not [s for s in spans if s.entity == "PHONE"]
    red = redact_text(text)
    assert "[AADHAAR]" in red and "6789" not in red


# --- #55 (Codex review on #91, P2): the zero-prefixed STD-code shape is ----
# structurally identical to a zero-prefixed file/case/order number, and
# shape alone cannot tell them apart (a real Delhi STD code, 011, starts
# with the same digit "1" a lot of file-number shapes do -- there is no
# "implausible leading digit" rule to hang an exclusion on). We deliberately
# keep matching this shape -- over-redaction is the safe failure direction
# -- and only suppress it when the text itself names it as a citation via
# the standard "Letter/Case/File/Order/Reference/Memo No." convention. That
# is a context check, not a confidence threshold: #55 already established
# that a genuine landline and these false positives score identically
# (0.40 against the old built-in), so no score-based cut could do this.


@pytest.mark.parametrize(
    "text",
    [
        "The number is 0123-4567890 for reference.",
        "The number is 0123 4567890 for reference.",
    ],
)
def test_zero_prefixed_reference_shape_without_marker_is_over_redacted(text):
    """Deliberate trade-off, not a Codex-endorsed fix: without a written
    citation marker immediately before it, this shape is indistinguishable
    from a real landline, so it is still redacted."""
    spans = [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    assert len(spans) == 1
    assert "[PHONE]" in redact_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Letter No. 0123-4567890 dated 12.03.2024 refers.",
        "Letter No. 0123 4567890 dated 12.03.2024 refers.",
        "Case No. 0674-2536789 refers to the earlier complaint.",
        "File No: 0674 2536789 is attached.",
        "Order No. 0674-2536789 was issued yesterday.",
        "Reference No. 0674-2536789 is cited above.",
        "Ref. No. 0674-2536789 is cited above.",
        "Memo No. 0674-2536789 dated last week.",
    ],
)
def test_reference_number_marker_suppresses_phone_tag(text):
    """The one reliable, non-score signal that a zero-prefixed digit run is
    a citation rather than a callback number: it is introduced by the
    standard government-correspondence convention."""
    assert not [s for s in detect_pii_spans(text) if s.entity == "PHONE"]
    # spaCy NER may separately (mis)tag the marker phrase itself as a NAME
    # (unrelated to #55/#56); what matters here is that PHONE never fires.
    assert "[PHONE]" not in redact_text(text)


# --- #56: government email addresses are not PII ----------------------------


@pytest.mark.parametrize(
    "address",
    [
        "officer@nic.in",
        "bdo.khordha@nic.in",
        "officer@rb.nic.in",
        "officer@odisha.gov.in",
        "officer@pmo.gov.in",
        "OFFICER@NIC.IN",
    ],
)
def test_government_emails_are_not_redacted(address):
    assert is_government_email(address)
    text = f"Please contact {address} for grievance status."
    assert redact_text(text) == text
    assert not [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]


@pytest.mark.parametrize(
    "address",
    [
        "citizen@gmail.com",
        "citizen@yahoo.co.in",
        "citizen@hotmail.com",
        "citizen@example.in",
    ],
)
def test_citizen_emails_still_redacted(address):
    assert not is_government_email(address)
    text = f"Please contact {address} for grievance status."
    red = redact_text(text)
    assert address not in red and "[EMAIL]" in red
    spans = [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]
    assert len(spans) == 1
    assert text[spans[0].start : spans[0].end] == address


def test_government_email_exclusion_is_not_a_psl_accident():
    """Regression guard: the old behaviour rode on tldextract parsing a bare
    "@nic.in"/"@gov.in" address as an empty registrable domain -- an accident
    that only covered part of the domain space (subdomains like
    "rb.nic.in" were still redacted, and "mil.in" isn't a PSL suffix at all,
    so a PSL-driven rule would never protect it). Our predicate must exclude
    all three uniformly regardless of what tldextract/the PSL says."""
    assert is_government_email("officer@mil.in")
    assert is_government_email("officer@station.mil.in")
    text = "Contact officer@mil.in or officer@rb.nic.in for status."
    assert redact_text(text) == text
    assert not [s for s in detect_pii_spans(text) if s.entity == "EMAIL"]


def test_redact_text_and_detect_pii_spans_agree_on_government_email():
    """The two entry points must never diverge (#56): one gates production
    redaction, the other gates the eval scorecard."""
    text = "Contact officer@nic.in or citizen@gmail.com for details."

    red = redact_text(text)
    spans = detect_pii_spans(text)
    email_spans = [s for s in spans if s.entity == "EMAIL"]

    assert "officer@nic.in" in red  # government address: untouched
    assert "citizen@gmail.com" not in red and "[EMAIL]" in red  # citizen: redacted
    assert len(email_spans) == 1
    assert text[email_spans[0].start : email_spans[0].end] == "citizen@gmail.com"


class TestSchemeAndAccountIdentifiers:
    """#139. Found in the Sambalpur 2024 pass: 579 digit runs of 11-18 chars
    survived because nothing looked for them. In a corpus about pensions,
    rations and scholarships, removing a citizen's name while leaving their
    bank account is not a redaction.

    Synthetic numbers only.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "my account no. 123456789012345 not credited",
            "bank a/c 987654321098 pension pending",
            "account number is 123456789012 wrong",
            "khata no 1234567890123 blocked",
        ],
    )
    def test_context_anchored_account_numbers_are_redacted(self, text):
        assert "[ACCOUNT]" in redact_text(text)

    @pytest.mark.parametrize(
        "text",
        [
            "ration card no 12345678901 not working",
            "my ration card 12345678901 blocked",
            "job card number 98765432101 wrong",
            "registration no 123456789012 rejected",
        ],
    )
    def test_context_anchored_scheme_ids_are_redacted(self, text):
        assert "[ID]" in redact_text(text)

    def test_a_long_bare_run_is_redacted_without_a_keyword(self):
        """Nothing in a grievance subject is legitimately a 14+ digit number
        except an account or scheme id, and requiring a keyword would miss
        every one written without a label."""
        assert "[ACCOUNT]" in redact_text("12345678901234567 credited late")

    @pytest.mark.parametrize(
        "text",
        [
            "Letter No. 1234567890 dated 05.06.2024",
            "case no 12345678 pending review",
        ],
    )
    def test_cited_reference_numbers_are_not_redacted(self, text):
        """Below the bare-run threshold a keyword is required, or case and
        letter numbers would be redacted wholesale."""
        assert "[ACCOUNT]" not in redact_text(text)
        assert "[ID]" not in redact_text(text)

    def test_the_surrounding_words_survive(self):
        """The span covers the digits only. Redacting 'account no.' along with
        the number would destroy the sentence for the officer reading it."""
        out = redact_text("my account no. 123456789012345 not credited")
        assert "account no." in out
        assert "not credited" in out

    def test_aadhaar_still_wins_on_its_own_shape(self):
        assert "[AADHAAR]" in redact_text("aadhaar 234567890123 mismatch")

    def test_a_mobile_is_still_a_phone_not_an_identifier(self):
        out = redact_text("call me on 9876543210 about the water supply")
        assert "[PHONE]" in out
        assert "[ACCOUNT]" not in out and "[ID]" not in out


class TestLandlineShapesFromTheSambalpurScan:
    """#120. Two shapes the n50 gold and the 55,544-row Sambalpur scan both
    showed surviving after PHONE_NUMBER was dropped in #55."""

    def test_std_written_without_the_leading_zero(self):
        assert "[PHONE]" in redact_text("std 674-2536789 office")

    def test_split_six_four_rather_than_at_the_std_boundary(self):
        assert "[PHONE]" in redact_text("landline 025612 3456 ward")

    def test_the_citation_guard_still_protects_reference_numbers(self):
        """These shapes are structurally identical to zero-prefixed file
        numbers, so the textual citation signal is what separates them."""
        assert "[PHONE]" not in redact_text("Letter No. 1234567890 dated 05.06.2024")
        assert "[PHONE]" not in redact_text("case no 123-4567890 pending")

    def test_dates_are_still_not_phones(self):
        assert "[PHONE]" not in redact_text("on 06.05.2025 we filed the complaint")


class TestIdentifierContextIsAClauseNotAnAdjacency:
    """Requiring the keyword adjacent to the digits missed 48 of the 304
    identifiers left after the first Sambalpur pass. Real text separates them,
    and no adjacency rule survives contact with how people write."""

    @pytest.mark.parametrize(
        "text",
        [
            "ration card issued 2019, number 12345678901 blocked",
            "my account details, the number 123456789012 is wrong",
            "pension not received, id 12345678901 since March",
        ],
    )
    def test_a_keyword_earlier_in_the_clause_still_anchors(self, text):
        out = redact_text(text)
        assert "[ACCOUNT]" in out or "[ID]" in out

    @pytest.mark.parametrize(
        "text",
        [
            "ration shop complaint, letter no 12345678901 pending",
            "case no 12345678901 about pension arrears",
            "file no 123456789012 regarding scholarship",
        ],
    )
    def test_a_cited_number_wins_over_a_keyword_in_the_same_clause(self, text):
        """A case or letter number can share a clause with a scheme word. The
        citation convention immediately before the digits means they are being
        quoted, not identified with."""
        out = redact_text(text)
        assert "[ACCOUNT]" not in out and "[ID]" not in out

    def test_the_bare_long_run_rule_is_unaffected_by_citation(self):
        """14+ digits is an identifier whatever precedes it: no case number is
        that long, so the citation guard must not suppress this class."""
        assert "[ACCOUNT]" in redact_text("12345678901234567 credited late")


class TestSpanBoundariesAreTrimmed:
    """#121. Recognizers return extents that sometimes run past the entity into
    surrounding whitespace -- 11 of 567 spans on the n50 gold. Trimmed in
    _postfilter, the one place both redact_text and detect_pii_spans pass
    through, so the two cannot disagree about what a span covers (#56)."""

    def test_no_predicted_span_carries_whitespace(self):
        text = "Please contact Rajesh Patnaik \nat 9876543210 about the water"
        for span in detect_pii_spans(text):
            surface = text[span.start : span.end]
            assert surface == surface.strip(), (span.entity, repr(surface))

    def test_redaction_leaves_the_newline_outside_the_token(self):
        """A span swallowing a trailing newline emits '[PHONE]' where
        '[PHONE]\\n' belonged, silently joining two lines of a grievance."""
        text = "call 9876543210 \nabout the water supply"
        out = redact_text(text)
        assert "\n" in out
        assert "[PHONE]" in out

    def test_a_whitespace_only_span_is_dropped(self):
        """Nothing to redact and nothing to score."""
        from janasunani.pipeline.stages.pii_tagger import _trim_span

        start, end = _trim_span("   ", 0, 3)
        assert start >= end

    def test_trimming_never_widens_or_crosses_content(self):
        from janasunani.pipeline.stages.pii_tagger import _trim_span

        text = "  Ravi  "
        start, end = _trim_span(text, 0, len(text))
        assert text[start:end] == "Ravi"

    def test_both_paths_agree_after_trimming(self):
        """The invariant #56 established: redact_text and detect_pii_spans must
        never disagree about what counts as PII or how far it extends."""
        text = "Sunita Devi  filed on 9876543210 "
        spans = detect_pii_spans(text)
        out = redact_text(text)
        assert len(spans) == out.count("[NAME]") + out.count("[PHONE]")
