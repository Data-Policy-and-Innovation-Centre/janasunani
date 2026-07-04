"""PII redaction stage — Presidio-based.

Replaces the lost DSI Transformer-CRF (weights + training data were Box-only
and unrecoverable after the team disbanded; see docs/ROADMAP.md). The stage
contract is unchanged:

    pages.extracted_text -> pages.redacted_text

Detection is Presidio's analyzer with pattern recognizers tuned for Indian
grievances — mobile numbers, Aadhaar, PAN, email — plus spaCy NER for person
names in English text. Improvements over the legacy model:

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

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from janasunani.pipeline.config import PipelineConfig
from janasunani.pipeline.db import connect

# Pages fetched + written per loop iteration (bounded memory).
DB_BATCH_SIZE = 200

# entity -> replacement token
ENTITY_TOKENS = {
    "PERSON": "[NAME]",
    "PHONE_NUMBER": "[PHONE]",
    "IN_MOBILE": "[PHONE]",
    "IN_AADHAAR": "[AADHAAR]",
    "IN_PAN": "[PAN]",
    "EMAIL_ADDRESS": "[EMAIL]",
}

ENTITY_ALIASES = {
    "PERSON": "NAME",
    "NAME": "NAME",
    "PHONE_NUMBER": "PHONE",
    "IN_MOBILE": "PHONE",
    "PHONE": "PHONE",
    "IN_AADHAAR": "AADHAAR",
    "AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "PAN": "PAN",
    "EMAIL_ADDRESS": "EMAIL",
    "EMAIL": "EMAIL",
}


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

    # Indian mobile: optional +91/0 prefix, then 10 digits starting 6-9,
    # allowing the common "98765 43210" split. Digit look-arounds keep it
    # from firing inside longer numbers (e.g. Aadhaar).
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="IN_MOBILE",
            name="in_mobile_recognizer",
            patterns=[
                Pattern(
                    "in_mobile",
                    r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)",
                    0.6,
                )
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

    _engines = (analyzer, AnonymizerEngine())
    return _engines


def redact_text(text: str) -> str:
    """Redact PII in ``text``, replacing each hit with its typed token."""
    from presidio_anonymizer.entities import OperatorConfig

    analyzer, anonymizer = _get_engines()
    # Analyze the digit-normalized copy (Indic numerals -> ASCII, same length),
    # anonymize the original: offsets carry over 1:1.
    results = analyzer.analyze(
        text=_ascii_digits(text), language="en", entities=list(ENTITY_TOKENS)
    )
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
    results = analyzer.analyze(
        text=_ascii_digits(text), language="en", entities=list(ENTITY_TOKENS)
    )
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
