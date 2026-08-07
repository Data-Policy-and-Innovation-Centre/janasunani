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
    "IN_BANK_ACCOUNT": "[ACCOUNT]",
    "IN_SCHEME_ID": "[ID]",
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
    "IN_BANK_ACCOUNT": "BANK_ACCOUNT",
    "BANK_ACCOUNT": "BANK_ACCOUNT",
    "IN_SCHEME_ID": "SCHEME_ID",
    "SCHEME_ID": "SCHEME_ID",
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


# Runs of two or more ALL-CAPS words. Government correspondence writes names
# this way constantly ("APPLICANT SMT SUNITA DEVI"), and spaCy's NER leans on
# capitalisation as a feature, so all-caps defeats it: 53 of the 228 NAME
# spans it missed on the n50 gold are all-caps (#92).
_CAPS_RUN_RE = re.compile(r"\b[A-Z][A-Z'.-]*(?:\s+[A-Z][A-Z'.-]*)+\b")


def _soften_caps(text: str) -> str:
    """Title-case ALL-CAPS runs so the NER can see them as names.

    **Length-preserving by construction**, which is what makes it safe: every
    replacement is `str.title()` of the same span, so offsets carry over 1:1
    and the caller can analyze this copy while redacting the original. Same
    contract as :func:`_ascii_digits`, and the reason both are applied to a
    throwaway copy rather than to the text anyone reads.

    Only runs of two or more capitalised words are touched. A single
    all-caps token is as likely to be an acronym (BDO, PHED, ATR) as a name,
    and title-casing those would create names out of department codes.
    """
    return _CAPS_RUN_RE.sub(lambda m: m.group(0).title(), text)


_engines: tuple | None = None


# Identifier classes with no distinctive shape of their own (#139). Each maps
# a set of context words to the digit lengths that class plausibly takes.
#
# Lengths are deliberately generous. Indian bank account numbers run 9-18
# digits across banks; ration, job-card and registration numbers vary by
# scheme and district. Over-redaction is the documented safe failure direction
# here -- an extra [ACCOUNT] where a case number stood is visible and
# harmless, an un-redacted account number is not.
_IDENTIFIER_CLASSES = (
    (
        "IN_BANK_ACCOUNT",
        re.compile(
            r"(?:a/?c|acc(?:oun)?t|account|bank|ifsc|passbook|khata)",
            re.IGNORECASE,
        ),
        (9, 18),
    ),
    (
        "IN_SCHEME_ID",
        re.compile(
            r"(?:ration|job\s*card|jobcard|mgnrega|nrega|registration|regd|"
            r"epic|voter|udise|pension|scholarship|beneficiary|applicant)",
            re.IGNORECASE,
        ),
        (8, 18),
    ),
)

# A bare run this long is an identifier whatever it is called. Nothing in a
# grievance subject line is legitimately a 14+ digit number except an account
# or a scheme id, and requiring a keyword would miss every one written without
# a label. Below this length the keyword is required, or case numbers would be
# redacted wholesale.
_BARE_ACCOUNT_MIN = 14
_BARE_ACCOUNT_MAX = 18

_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{8,18}(?!\d)")

# How far back to look for the context word.
#
# The keyword is matched anywhere in this window rather than adjacent to the
# digits. Requiring adjacency missed 48 of the 304 identifiers left after the
# first pass over Sambalpur 2024: real text separates the two ("ration card
# issued 2019, number 12345678901"), and no adjacency rule survives contact
# with how people actually write.
#
# 40 characters is roughly one clause -- long enough to span "bank account
# number is", short enough that an unrelated earlier mention does not leak in.
_CONTEXT_WINDOW = 40

# ...but a keyword in the window is not enough on its own, because a case or
# letter number can sit in the same clause as a scheme word ("ration shop
# complaint, letter no 12345678901"). The same citation convention the PHONE
# postfilter uses (#55/#91) wins over the keyword when it immediately precedes
# the digits. Cited numbers are being quoted, not identified with.
_CITATION_BEFORE_RE = re.compile(
    r"(letter|case|file|order|reference|ref|memo|receipt|regd)\.?\s*"
    r"(no|number)?\.?\s*[:\-]?\s*$",
    re.IGNORECASE,
)

# Odia and wider Indian surnames (#183).
#
# NAME is the one entity with no pattern recognizer behind it: it comes solely
# from en_core_web_sm PERSON, which is trained on English-language news and is
# demonstrably weak here. Measured on the live path, 40 probes over 10 Odia
# names in 4 sentence framings: 42% missed outright, a further 5% partially
# redacted (given name left exposed, which still identifies). A Western
# control name in the identical sentence was caught where the Odia one was
# not.
#
# This is a closed-vocabulary problem far more than a model problem. Odia
# surnames are a small, public, highly distinctive set, so a gazetteer catches
# what the model does not without touching citizen data to build it.
#
# The span deliberately covers the WHOLE name, walking back over preceding
# capitalised tokens ("Ramesh Kumar Sahoo", not just "Sahoo"). Redacting the
# surname alone is the partial-redaction failure above, not a fix for it.
_INDIAN_SURNAMES = frozenset(
    {
        "sahoo", "sahu", "patra", "nayak", "naik", "behera", "rout", "routray",
        "mohanty", "jena", "das", "dash", "swain", "mishra", "misra", "panda",
        "pradhan", "barik", "sethi", "majhi", "bhoi", "meher", "parida",
        "pattnaik", "patnaik", "mahapatra", "acharya", "biswal", "tripathy",
        "tripathi", "kar", "ghadei", "sabar", "murmu", "hembram", "soren",
        "tudu", "kisan", "gouda", "gountia", "sagar", "bag", "bal", "behra",
        "khatua", "lenka", "mallik", "malik", "nanda", "ojha", "padhi",
        "pani", "rath", "samal", "senapati", "singh", "sinha", "tarai",
        "thakur", "bhue", "digal", "pradhani", "harijan", "bhatra",
        "kumar", "prasad", "chandra", "charan", "ranjan",
    }
)

# A capitalised token, allowing an internal apostrophe or hyphen.
_CAP_TOKEN = r"[A-Z][a-z'\-]+"

# Surnames that are also scheme-name words. "Pradhan" is a real Odia surname
# and the first word of "Pradhan Mantri <scheme>", which appears constantly in
# a corpus about housing and pensions. Redacting it there removes the scheme
# the grievance is about and leaves the officer a sentence they cannot act on.
# Keyed on the token that follows, so the surname still redacts everywhere else.
_SURNAME_SCHEME_FOLLOWERS = {
    "pradhan": {"mantri"},
}

# Name-introducing phrases. The misses concentrate here: the "Applicant:"
# framing missed 7 of 10 against 2 of 10 for a bare subject position, because
# a label followed by a colon gives the model no grammatical subject to latch
# onto. Matching the introducer and redacting only what follows keeps the
# sentence readable for the officer, the same rule the identifier recognizer
# follows for "account no.".
#
# Deliberately excludes a bare "name" and a bare "from". Both over-redact on
# real grievance text: "Name of the scheme is Pradhan Mantri Awas Yojana"
# loses the scheme, and "the road from Sambalpur to Bargarh" loses the place.
# Neither costs recall, because a person named after either introducer is
# still caught by the surname trigger.
_NAME_INTRODUCER_RE = re.compile(
    r"(?:my\s+name\s+is"
    r"|name\s+of\s+(?:the\s+)?(?:applicant|complainant|petitioner|deponent)"
    r"|applicant|complainant|petitioner|deponent"
    r"|submitted\s+by|filed\s+by|signed\s+by"
    r"|yours\s+(?:faithfully|sincerely|truly))"
    r"\s*[:\-]?\s+",
    re.IGNORECASE,
)

# Titles that reliably precede a person name.
_TITLE_WORDS = frozenset(
    {"shri", "sri", "smt", "smit", "mr", "mrs", "ms", "dr", "prof", "kumari", "km"}
)
_NAME_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_TITLE_WORDS)) + r")\.?\s+",
    re.IGNORECASE,
)


class _IndianIdentifierRecognizer:
    """Bank account and scheme-identifier numbers, anchored on context.

    Subclasses Presidio's EntityRecognizer at construction time rather than at
    import, so this module stays importable without presidio installed (the
    lazy-import philosophy the rest of the file follows).
    """

    def __new__(cls):
        from presidio_analyzer import EntityRecognizer, RecognizerResult

        class _Impl(EntityRecognizer):
            def __init__(self) -> None:
                super().__init__(
                    supported_entities=[name for name, _, _ in _IDENTIFIER_CLASSES],
                    name="in_identifier_recognizer",
                    supported_language="en",
                )

            def load(self) -> None:  # pragma: no cover - presidio hook
                pass

            def analyze(self, text, entities, nlp_artifacts=None):
                results = []
                for match in _DIGIT_RUN_RE.finditer(text):
                    start, end = match.start(), match.end()
                    length = end - start
                    before = text[max(0, start - _CONTEXT_WINDOW) : start]

                    entity = None
                    score = 0.0
                    cited = _CITATION_BEFORE_RE.search(before) is not None
                    for name, context_re, (low, high) in _IDENTIFIER_CLASSES:
                        if low <= length <= high and context_re.search(before) and not cited:
                            entity, score = name, 0.7
                            break
                    if entity is None and _BARE_ACCOUNT_MIN <= length <= _BARE_ACCOUNT_MAX:
                        entity, score = "IN_BANK_ACCOUNT", 0.6

                    if entity is None or (entities and entity not in entities):
                        continue
                    results.append(
                        RecognizerResult(
                            entity_type=entity, start=start, end=end, score=score
                        )
                    )
                return results

        return _Impl()


def _indian_name_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans for Indian person names en_core_web_sm misses.

    Two independent triggers, both of which yield the full name:

    * a capitalised token run ending in a known surname;
    * a name-introducing phrase or title, followed by a capitalised run.

    Pure text in, offsets out, so it is testable without presidio installed.
    """
    spans: list[tuple[int, int]] = []
    token_re = re.compile(_CAP_TOKEN)
    tokens = [(m.start(), m.end(), m.group()) for m in token_re.finditer(text)]

    # Trigger 1: a run of capitalised tokens ending in a known surname.
    for index, (start, end, token) in enumerate(tokens):
        if token.lower() not in _INDIAN_SURNAMES:
            continue
        followers = _SURNAME_SCHEME_FOLLOWERS.get(token.lower())
        if followers:
            next_word = re.match(r"\s+([A-Za-z]+)", text[end:])
            if next_word and next_word.group(1).lower() in followers:
                continue
        first = start
        back = index - 1
        # Walk back over adjacent capitalised tokens, separated by single
        # spaces only, so a new sentence cannot be absorbed into the name.
        while back >= 0 and len(tokens) > back:
            prev_start, prev_end, prev_token = tokens[back]
            if text[prev_end:start].strip() or (start - prev_end) > 1:
                break
            # Keep the honorific outside the span: "Shri [NAME]" reads, and a
            # title identifies nobody on its own.
            if prev_token.lower() in _TITLE_WORDS:
                break
            first = prev_start
            start = prev_start
            back -= 1
        spans.append((first, end))

    # Trigger 2: an introducer or title, then up to four capitalised tokens.
    for pattern in (_NAME_INTRODUCER_RE, _NAME_TITLE_RE):
        for match in pattern.finditer(text):
            cursor = match.end()
            run_start = None
            run_end = None
            for _ in range(4):
                token_match = token_re.match(text, cursor)
                if token_match is None:
                    break
                if run_start is None:
                    run_start = token_match.start()
                run_end = token_match.end()
                cursor = token_match.end()
                if cursor < len(text) and text[cursor] == " ":
                    cursor += 1
                else:
                    break
            if run_start is not None and run_end is not None:
                spans.append((run_start, run_end))

    return _merge_spans(spans)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union overlapping or touching spans, so one name yields one result."""
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


class _IndianNameRecognizer:
    """Person names the English NER model misses (#183).

    Same construction trick as _IndianIdentifierRecognizer: subclasses
    EntityRecognizer at call time so this module imports without presidio.
    """

    def __new__(cls):
        from presidio_analyzer import EntityRecognizer, RecognizerResult

        class _Impl(EntityRecognizer):
            def __init__(self) -> None:
                super().__init__(
                    supported_entities=["PERSON"],
                    name="in_name_recognizer",
                    supported_language="en",
                )

            def load(self) -> None:  # pragma: no cover - presidio hook
                pass

            def analyze(self, text, entities, nlp_artifacts=None):
                if entities and "PERSON" not in entities:
                    return []
                return [
                    RecognizerResult(
                        entity_type="PERSON", start=start, end=end, score=0.6
                    )
                    for start, end in _indian_name_spans(text)
                ]

        return _Impl()


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
    # Bank accounts and government scheme identifiers (#139).
    #
    # Found in the Sambalpur 2024 redaction pass: 579 digit runs of 11-18
    # characters survived, because nothing looked for them. ENTITY_TOKENS
    # covered NAME/PHONE/EMAIL/AADHAAR/PAN, and Aadhaar is exactly 12 digits,
    # so every other identifier length passed straight through. In a corpus
    # about pensions, rations and scholarships, the numbers citizens quote are
    # account numbers, ration cards and job cards -- removing someone's name
    # while leaving their bank account is not a redaction.
    #
    # These are shape-PLUS-context problems, unlike Aadhaar and PAN which have
    # a distinctive shape of their own. A bare 11-digit run could be a job card
    # or a case number and nothing about the digits distinguishes them, so the
    # context word is load-bearing rather than a confidence nudge -- which is
    # why this is a custom recognizer instead of a PatternRecognizer with a
    # `context` list. Presidio's `context` only boosts a score; here its
    # absence must mean no match at all.
    #
    # The returned span covers the DIGITS ONLY. Matching the keyword too would
    # redact "account no." along with the number and destroy the sentence for
    # the officer reading it.
    analyzer.registry.add_recognizer(_IndianIdentifierRecognizer())
    # Indian person names (#183). Additive to en_core_web_sm PERSON: overlapping
    # spans from the two are merged by the anonymizer, so this only ever widens
    # coverage. See _indian_name_spans for the measured gap it closes.
    analyzer.registry.add_recognizer(_IndianNameRecognizer())
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
                # #120: two shapes the Sambalpur scan and the n50 gold both
                # showed surviving. Written without the leading 0 ("674-2536789"
                # for Bhubaneswar), and split 6-4 rather than at the STD
                # boundary ("025612 3456"). Both are separator-bearing, so
                # they cannot be confused with a bare mobile, and the
                # citation guard in _postfilter still exempts "Letter No."
                # style references that happen to take the same shape.
                Pattern(
                    "in_landline_no_leading_zero",
                    r"(?<!\d)[1-9]\d{2,3}[\s-]\d{6,8}(?!\d)",
                    0.55,
                ),
                Pattern(
                    "in_landline_split_six_four",
                    r"(?<!\d)0\d{4,5}[\s-]\d{4}(?!\d)",
                    0.55,
                ),
            ],
            context=["landline", "office", "std code", "telephone"],
        )
    )

    _engines = (analyzer, AnonymizerEngine())
    return _engines


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Shrink a span to its first and last non-whitespace character.

    Never widens and never crosses a non-whitespace character, so what the
    recognizer identified is untouched -- only the padding around it goes.
    Returns a degenerate span when the extent is entirely whitespace; the
    caller drops those.
    """
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


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

    # Trim whitespace off both ends of every surviving span (#121).
    #
    # Recognizers return extents that sometimes run past the entity into
    # surrounding space or a newline -- 11 of 567 spans on the n50 gold. Two
    # consequences, and the second is the one a reader sees: a span containing
    # whitespace can never be exact-matched by a gold that does not, which
    # depresses exact_recall for free; and redaction replaces the whole
    # extent, so a span swallowing a trailing newline emits "[NAME]" where
    # "[NAME]\n" belonged, silently joining two lines of a citizen's grievance.
    #
    # Done here, at the end, for the reason #56 established: this is the one
    # place both redact_text and detect_pii_spans pass through, so the two
    # cannot disagree about what a span covers. Done *after* the checks above
    # so they still see the extents the recognizers actually produced.
    trimmed = []
    for result in filtered:
        start, end = _trim_span(analyzed_text, result.start, result.end)
        if start >= end:
            # Entirely whitespace. Nothing to redact and nothing to score.
            continue
        result.start, result.end = start, end
        trimmed.append(result)
    return trimmed


def redact_text(text: str) -> str:
    """Redact PII in ``text``, replacing each hit with its typed token."""
    from presidio_anonymizer.entities import OperatorConfig

    analyzer, anonymizer = _get_engines()
    # Analyze the digit-normalized copy (Indic numerals -> ASCII, same length),
    # anonymize the original: offsets carry over 1:1.
    analyzed_text = _soften_caps(_ascii_digits(text))
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
    analyzed_text = _soften_caps(_ascii_digits(text))
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
