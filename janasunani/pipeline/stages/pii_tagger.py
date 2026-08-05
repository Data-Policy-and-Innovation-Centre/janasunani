"""PII redaction stage — Presidio-based.

Replaces the lost DSI Transformer-CRF (weights + training data were Box-only
and unrecoverable after the team disbanded; see docs/ROADMAP.md). The stage
contract is unchanged:

    pages.extracted_text -> pages.redacted_text

Detection is Presidio's analyzer with pattern recognizers tuned for Indian
grievances — mobile numbers, landlines, Aadhaar, PAN, email — plus spaCy NER
for person names in English text. Improvements over the legacy model:

  - no token window: whole pages are analyzed, so nothing past the first
    512 tokens silently escapes redaction;
  - mixed-language pages ("English, Odia") are included — number/id patterns
    are script-agnostic (the legacy stage skipped them entirely);
  - explainable hits (each redaction is a named recognizer, not a model
    logit) — appropriate for government data;
  - work is paged through SQL in bounded batches (no whole-corpus DataFrame).

Everything runs in-process: citizen text is never sent to an external
service. Detected spans are replaced with typed tokens ([NAME], [PHONE],
[AADHAAR], [PAN], [EMAIL]) so downstream summarize/categorize keep sentence
structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect

# Pages fetched + written per loop iteration (bounded memory).
DB_BATCH_SIZE = 200

# entity -> replacement token
#
# PHONE_NUMBER (Presidio's built-in PhoneRecognizer, backed by the
# `phonenumbers` library) is deliberately absent: it treats dotted/hyphenated
# dates and bare 10-digit file numbers as plausible phone numbers (#55). Our
# own IN_MOBILE/IN_LANDLINE recognizers below are the sole source of PHONE
# hits, so detection is entirely ours and testable.
ENTITY_TOKENS = {
    "PERSON": "[NAME]",
    "IN_MOBILE": "[PHONE]",
    "IN_LANDLINE": "[PHONE]",
    "IN_AADHAAR": "[AADHAAR]",
    "IN_PAN": "[PAN]",
    "EMAIL_ADDRESS": "[EMAIL]",
}

ENTITY_ALIASES = {
    "PERSON": "NAME",
    "NAME": "NAME",
    "PHONE_NUMBER": "PHONE",
    "IN_MOBILE": "PHONE",
    "IN_LANDLINE": "PHONE",
    "PHONE": "PHONE",
    "IN_AADHAAR": "AADHAAR",
    "AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "PAN": "PAN",
    "EMAIL_ADDRESS": "EMAIL",
    "EMAIL": "EMAIL",
}

# Government domains whose addresses are published contact details of public
# officers, not citizen PII (#56, maintainer decision 2026-07-27). This is
# our own rule, not the Public Suffix List: `nic.in`/`gov.in`/`ac.in`/`co.in`
# happen to be PSL entries in their own right, which made tldextract parse a
# bare "@nic.in" address as an empty registrable domain and made Presidio's
# EmailRecognizer discard it as invalid -- an accident that only covered part
# of the domain space (subdomains like `rb.nic.in` were still redacted) and
# that a PSL/tldextract update could silently flip.
_GOVERNMENT_EMAIL_SUFFIXES = ("nic.in", "gov.in", "mil.in")


def is_government_email(address: str) -> bool:
    """True if ``address`` is an official government email (#56).

    Matches the domain itself and any subdomain of it (``rb.nic.in``,
    ``pmo.gov.in``), case-insensitively. Not PII for redaction purposes.
    """
    if "@" not in address:
        return False
    domain = address.strip().lower().rsplit("@", 1)[-1]
    if not domain:
        return False
    return any(
        domain == suffix or domain.endswith(f".{suffix}")
        for suffix in _GOVERNMENT_EMAIL_SUFFIXES
    )


# A dotted/hyphenated/slash date (DD.MM.YYYY and its variants) is shaped
# enough like a run of phone digits that Presidio's built-in PhoneRecognizer
# used to tag it PHONE (#55). Applied to every PHONE-normalized candidate
# regardless of which recognizer produced it, so it stays a guard even if a
# future recognizer or the built-in is reintroduced.
_DATE_SHAPE_RE = re.compile(r"^\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}$")


def _is_date_shaped(candidate: str) -> bool:
    return bool(_DATE_SHAPE_RE.match(candidate.strip()))


# A zero-prefixed, single-separator digit run (STD code + subscriber number)
# is structurally identical to a zero-prefixed file/case/order/letter/memo
# number -- see the long comment on the landline recognizer for why shape
# alone cannot tell them apart (#55 review on #91). The one reliable signal
# left is the written citation convention itself: government correspondence
# labels these numbers "Letter No. 0674-2536789", "Case No.", "File No.",
# "Order No.", "Reference No.", "Memo No." immediately before the digits.
# This is a context check, not a confidence threshold: it looks at the text,
# not the recognizer's score.
_REFERENCE_NUMBER_CONTEXT_RE = re.compile(
    r"\b(?:letters?|cases?|files?|orders?|references?|refs?|memos?)\.?\s*no\.?\s*[:#-]?\s*$",
    re.IGNORECASE,
)
# Comfortably covers "Reference No: " (14 chars) with room to spare, without
# reaching back far enough to catch an unrelated marker word earlier in the
# sentence.
_REFERENCE_CONTEXT_WINDOW = 30


def _preceded_by_reference_marker(analyzed_text: str, start: int) -> bool:
    window = analyzed_text[max(0, start - _REFERENCE_CONTEXT_WINDOW) : start]
    return bool(_REFERENCE_NUMBER_CONTEXT_RE.search(window))


@dataclass(frozen=True)
class PIISpan:
    entity: str
    start: int
    end: int
    score: float = 0.0


# Devanagari (U+0966) and Odia (U+0B66) decimal digits -> ASCII. Presidio's
# regexes are Unicode-aware for \d but the anchoring classes ([6-9], [2-9])
# are ASCII-only, so a mobile/Aadhaar number written in Odia numerals would
# escape entirely. One codepoint maps to one ASCII char, so the translation
# is length-preserving: spans found on the normalized copy apply unchanged
# to the original text.
_INDIC_DIGITS_TO_ASCII = str.maketrans(
    {base + i: str(i) for base in (0x0966, 0x0B66) for i in range(10)}
)


def _ascii_digits(text: str) -> str:
    return text.translate(_INDIC_DIGITS_TO_ASCII)


_engines: tuple | None = None


def _get_engines():
    """Build (analyzer, anonymizer) once per process. Heavy imports live here
    so importing this module stays light (lazy-import philosophy)."""
    global _engines
    if _engines is not None:
        return _engines

    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    analyzer = AnalyzerEngine(
        nlp_engine=provider.create_engine(), supported_languages=["en"]
    )

    # Indian mobile: optional +91/0 prefix, then 10 digits starting 6-9.
    # Three explicit groupings, each with its own named Pattern rather than
    # one digit-by-digit-permissive regex: bare/5-5 ("9876543210" /
    # "98765 43210"), 4-3-3 ("9876 543 210"), and 3-3-4 ("987 654 3210") --
    # all attested real formats (Codex review on #91). A fully permissive
    # "separator before any digit" version was tried and rejected: it also
    # matched STD-code-shaped landlines like "0674 2536789" over their
    # *entire* span (a leading "0" plus a 6-9 digit plus 9 more digits with
    # one internal split is exactly that shape) for no coverage gain, since
    # both already redact as PHONE. These three fixed-group patterns are far
    # more restrictive, but one harmless overlap remains: 3-3-4 and the
    # landline's split-subscriber pattern both match "0674 253 6789" on the
    # same span (see test_split_subscriber_landline_also_matches_new_mobile_
    # pattern_harmlessly) -- normalization plus dict-keyed dedup in
    # detect_pii_spans, and Presidio's own overlap handling in
    # redact_text, collapse it to one [PHONE], so this is a documented,
    # tested non-issue rather than a silent one. Digit look-arounds keep
    # these patterns from firing inside longer numbers (e.g. Aadhaar).
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_MOBILE",
            name="in_mobile_recognizer",
            patterns=[
                Pattern(
                    "in_mobile_bare_or_5_5",
                    r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)",
                    0.6,
                ),
                Pattern(
                    "in_mobile_4_3_3",
                    r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)",
                    0.6,
                ),
                Pattern(
                    "in_mobile_3_3_4",
                    r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{2}[\s-]\d{3}[\s-]\d{4}(?!\d)",
                    0.6,
                ),
            ],
            context=["mobile", "phone", "contact", "call", "whatsapp"],
        )
    )
    # Aadhaar: 12 digits (first digit 2-9), usually spaced 4-4-4. Privacy-first:
    # redact on shape alone; context words only boost confidence.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_AADHAAR",
            name="in_aadhaar_recognizer",
            patterns=[
                Pattern(
                    "in_aadhaar",
                    r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)",
                    0.5,
                )
            ],
            context=["aadhaar", "aadhar", "uid", "uidai"],
        )
    )
    # PAN: AAAAA9999A.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_PAN",
            name="in_pan_recognizer",
            patterns=[Pattern("in_pan", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.6)],
            context=["pan", "income tax", "permanent account"],
        )
    )
    # Landline: STD code (2-4 digits after the leading 0) plus a 6-8 digit
    # subscriber number, e.g. Bhubaneswar "0674 2536789". Presidio's built-in
    # PhoneRecognizer used to be the only thing catching these, at the same
    # low confidence as its date/file-number false positives (#55); this
    # recognizer is ours so a real landline scores on a pattern we own and
    # test. Three named patterns cover the separator styles reported on the
    # #91 review: bare space/hyphen (original), an optional wrapping
    # paren pair plus "/" as an additional separator, and a split
    # subscriber number ("0674 253 6789").
    #
    # Known, accepted ambiguity: this shape (0 + STD-length digits + one
    # separator + subscriber-length digits) is structurally identical to a
    # zero-prefixed file/order/case number, e.g. "0123-4567890" -- exactly
    # the false-positive class #55 was filed about, and #55's own evidence
    # table lists this shape (`9999-9999999`) among the false positives.
    # We deliberately do NOT try to exclude it by digit-shape, e.g. by
    # requiring the STD code's first significant digit to be in some
    # "plausible" range: Delhi's real STD code is 011 (first significant
    # digit "1"), so any such range would either admit the same file-number
    # shapes it's meant to reject or reject real metro landlines -- shape
    # alone cannot decide this case. Redacting is the safe failure direction
    # (#55's own framing), so we keep matching and erring toward
    # over-redaction here. What we do add is a context guard, not a
    # confidence threshold: `_preceded_by_reference_marker` below drops the
    # match when it is immediately preceded by a written reference-number
    # convention ("Letter No.", "Case No.", "File No.", "Order No.",
    # "Reference No.", "Memo No."), which is the common, reliable textual
    # signal that this is a citation, not a callback number.
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_LANDLINE",
            name="in_landline_recognizer",
            patterns=[
                Pattern(
                    "in_landline_bare",
                    r"(?<!\d)0\d{2,4}[\s-]\d{6,8}(?!\d)",
                    0.6,
                ),
                Pattern(
                    "in_landline_parens_or_slash",
                    r"(?<!\d)\(?0\d{2,4}\)?[\s/-]\d{6,8}(?!\d)",
                    0.6,
                ),
                Pattern(
                    "in_landline_split_subscriber",
                    r"(?<!\d)0\d{2,4}[\s-]\d{3}[\s-]\d{4}(?!\d)",
                    0.6,
                ),
            ],
            context=["landline", "office", "std code", "telephone"],
        )
    )

    _engines = (analyzer, AnonymizerEngine())
    return _engines


def _postfilter(analyzed_text: str, results: list) -> list:
    """Drop analyzer results that are out of scope for our redaction policy.

    Applied identically inside ``redact_text`` and ``detect_pii_spans`` so
    the two never disagree about what counts as PII (#55, #56):

    - PHONE-normalized hits whose matched text is date-shaped (dotted,
      hyphenated, or slash-separated) rather than a real phone number.
    - PHONE-normalized hits immediately preceded by a written
      reference-number convention ("Letter No.", "Case No.", ...): the
      zero-prefixed STD-code shape is structurally identical to a
      zero-prefixed file number and shape alone can't separate them (see
      the landline recognizer comment), but this textual citation pattern
      is a reliable signal the digits are being cited, not dialed.
    - Any hit overlapping a government-domain email address, which is
      published contact information, not citizen PII. Not just the
      EMAIL_ADDRESS hit itself: spaCy's NER recognizer independently tags
      the same span PERSON (e.g. "officer@rb.nic.in" reads as a name to
      en_core_web_sm), and a policy that exempted the email label but left
      an overlapping NAME hit standing would still redact the address.
    """
    government_ranges = [
        (result.start, result.end)
        for result in results
        if normalize_entity(result.entity_type) == "EMAIL"
        and is_government_email(analyzed_text[result.start : result.end])
    ]

    def _overlaps_government_email(result) -> bool:
        return any(
            max(result.start, start) < min(result.end, end)
            for start, end in government_ranges
        )

    filtered = []
    for result in results:
        entity = normalize_entity(result.entity_type)
        candidate = analyzed_text[result.start : result.end]
        if entity == "PHONE" and (
            _is_date_shaped(candidate)
            or _preceded_by_reference_marker(analyzed_text, result.start)
        ):
            continue
        if _overlaps_government_email(result):
            continue
        filtered.append(result)
    return filtered


def redact_text(text: str) -> str:
    """Redact PII in ``text``, replacing each hit with its typed token."""
    from presidio_anonymizer.entities import OperatorConfig

    analyzer, anonymizer = _get_engines()
    # Analyze the digit-normalized copy (Indic numerals -> ASCII, same length),
    # anonymize the original: offsets carry over 1:1.
    analyzed_text = _ascii_digits(text)
    results = analyzer.analyze(
        text=analyzed_text, language="en", entities=list(ENTITY_TOKENS)
    )
    results = _postfilter(analyzed_text, results)
    if not results:
        return text
    operators = {
        entity: OperatorConfig("replace", {"new_value": token})
        for entity, token in ENTITY_TOKENS.items()
    }
    return anonymizer.anonymize(
        text=text, analyzer_results=results, operators=operators
    ).text


def normalize_entity(entity: str) -> str:
    """Normalize Presidio/gold labels into eval categories."""
    return ENTITY_ALIASES.get(entity.upper(), entity.upper())


def detect_pii_spans(text: str) -> list[PIISpan]:
    """Return detected PII spans with normalized entity labels.

    This is used by the offline PII evaluator. It shares the same Presidio
    analyzer as ``redact_text`` so the eval measures the exact production
    recognizers, not a parallel implementation.
    """
    analyzer, _ = _get_engines()
    analyzed_text = _ascii_digits(text)
    results = analyzer.analyze(
        text=analyzed_text, language="en", entities=list(ENTITY_TOKENS)
    )
    results = _postfilter(analyzed_text, results)
    spans: dict[tuple[str, int, int], PIISpan] = {}
    for result in results:
        entity = normalize_entity(result.entity_type)
        key = (entity, result.start, result.end)
        score = float(getattr(result, "score", 0.0) or 0.0)
        current = spans.get(key)
        if current is None or score > current.score:
            spans[key] = PIISpan(
                entity=entity,
                start=result.start,
                end=result.end,
                score=score,
            )
    return sorted(spans.values(), key=lambda span: (span.start, span.end, span.entity))


def run_pii_tagger(config: PipelineConfig) -> None:
    """Fill ``pages.redacted_text`` from ``pages.extracted_text``."""
    total = 0
    while True:
        batch = _load_pending_batch(config.db_path, DB_BATCH_SIZE)
        if not batch:
            break
        updates = [(redact_text(text), page_id) for page_id, text in batch]
        _write_redactions(config.db_path, updates)
        total += len(updates)
        logger.info(f"pii_tagger: redacted {total} page(s) so far")

    if total == 0:
        logger.info("pii_tagger: nothing to do")
    else:
        logger.success(f"pii_tagger done: redacted={total}")


def _load_pending_batch(db_path: Path, limit: int) -> list[tuple[str, str]]:
    """Next batch of pages with English content and no redaction yet.

    LIKE '%English%' (not equality) so mixed "English, Odia" pages are
    covered — number/id patterns apply regardless of script.
    """
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT page_id, extracted_text
            FROM pages
            WHERE language LIKE '%English%'
              AND extracted_text IS NOT NULL
              AND extracted_text != ''
              AND (redacted_text IS NULL OR redacted_text = '')
            ORDER BY doc_id, page_number
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [(r["page_id"], r["extracted_text"]) for r in rows]


def _write_redactions(db_path: Path, updates: list[tuple[str, str]]) -> None:
    with connect(db_path) as connection:
        connection.executemany(
            "UPDATE pages SET redacted_text = ? WHERE page_id = ?",
            updates,
        )
        connection.commit()
