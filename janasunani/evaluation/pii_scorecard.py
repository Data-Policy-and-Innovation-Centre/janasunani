"""Per-entity, per-language PII scorecard (issue #67).

Wraps :mod:`janasunani.pipeline.pii_eval` to report missed-PII rate by
entity and by language, not aggregate (ROADMAP §5.1: false-negative rate is
release-critical, F1 hides leaked PII).

Reads the gold JSONL that #15 produces (``data/external/pii_gold.jsonl``,
DVC-tracked). Each record may carry a ``language`` key: ``en``/``or``/
``roman`` (normalised to ``english``/``odia``/``romanized``). If absent,
the score groups it under ``unknown`` rather than guessing. DSI's 80.56%
any-overlap / 50% exact are reported as *reference* and English-only, not
as thresholds (issue #67).

Thin slices (< 20 gold spans) are flagged ``low_power`` and the fallback is
to report English only per DELIVERY.md — the scorecard says so rather than
publishing a number with no power behind it.

Also provides :func:`find_shaped_pii` / :func:`scan_texts` /
:func:`scan_corpus_parquet` — grep-shaped PII audit over slice redacted
text. These are the helpers that power the "55,544 scale" claim: after
redaction no mobile, Aadhaar, PAN or non-government email shape survives.
Unlike the gold recall they need no labels, only the recognizer shapes.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from janasunani.pipeline.pii_eval import (
    EvaluationReport,
    GoldExample,
    load_gold_jsonl,
    score_predictions,
    LEGACY_OVERLAP_BASELINE,
)

LEGACY_EXACT_BASELINE = 0.50
MIN_GOLD_FOR_REPORTABLE_SLICE = 20  # below this, per-language is low power

# ---------------------------------------------------------------------------
# Language slicing
# ---------------------------------------------------------------------------


def _normalize_language(raw: object | None) -> str:
    if not isinstance(raw, str):
        return "unknown"
    v = raw.strip().lower()
    if v in {"en", "english", "eng"}:
        return "english"
    if v in {"or", "odia", "oriya", "odia_script", "odia-script"}:
        return "odia"
    if v in {"roman", "romanized", "romanized_odia", "romanized-odia", "translit"}:
        return "romanized"
    return "unknown"


@dataclass(frozen=True)
class LanguageSlice:
    language: str
    report: EvaluationReport
    is_low_power: bool


def _slice_by_language(
    examples: list[GoldExample],
    raw_langs: dict[str, str],
) -> dict[str, list[GoldExample]]:
    out: dict[str, list[GoldExample]] = defaultdict(list)
    for ex in examples:
        lang = _normalize_language(raw_langs.get(ex.id))
        out[lang].append(ex)
    return dict(out)


def score_per_language(
    gold_path: Path,
    baseline_overlap: float = LEGACY_OVERLAP_BASELINE,
    *,
    require_analyzer: bool = False,
) -> dict[str, LanguageSlice]:
    """Score per language over a gold JSONL that may carry a language tag.

    The gold file may have a top-level ``language`` per record; if absent,
    the slice is ``unknown``. Each slice is scored independently via
    ``score_examples`` (so the model's language handling is visible). Thin
    slices are flagged rather than hidden.

    Wires the live Presidio recognizers (``detect_pii_spans``) through
    :func:`janasunani.pipeline.pii_eval.score_examples` — the same path
    ``janasunani-evaluate-pii`` uses — so the table reports what production
    actually redacts. Falls back to empty predictions only when the analyzer
    cannot be imported (e.g. a轻量 env without the ``pii`` extra), keeps the
    slicing / thin-slice guards testable without heavy deps.
    """
    raw_langs: dict[str, str] = {}
    with gold_path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            rec_id = str(obj.get("id") or "")
            if "language" in obj:
                raw_langs[rec_id] = str(obj["language"])

    examples = load_gold_jsonl(gold_path)
    slices = _slice_by_language(examples, raw_langs)
    out: dict[str, LanguageSlice] = {}
    for lang, exs in sorted(slices.items()):
        try:
            from janasunani.pipeline.pii_eval import score_examples

            rep = score_examples(exs, baseline_overlap_recall=baseline_overlap)
        except Exception:
            if require_analyzer:
                raise
            rep = score_predictions(exs, {}, baseline_overlap_recall=baseline_overlap)
        gold_total = rep.overall.gold
        low = gold_total < MIN_GOLD_FOR_REPORTABLE_SLICE
        out[lang] = LanguageSlice(language=lang, report=rep, is_low_power=low)
    return out


def _format_slice(name: str, s: LanguageSlice) -> list[str]:
    rep = s.report
    flag = " (low power — <20 spans, not reportable alone)" if s.is_low_power else ""
    lines = [f"## {name}{flag}", ""]
    lines.append(f"gold={rep.overall.gold} predicted={rep.overall.predicted} "
                 f"coverage_overlap_recall={rep.coverage.overlap_recall:.3f} "
                 f"overall_overlap_recall={rep.overall.overlap_recall:.3f}")
    lines.append("")
    lines.append("entity  gold  predicted  overlap_hits  exact_hits  overlap_recall  exact_recall  missed_rate")
    for ent, m in sorted(rep.by_entity.items()):
        missed = 1 - m.overlap_recall if m.gold else 0
        lines.append(f"{ent:6}  {m.gold:4}  {m.predicted:9}  {m.overlap_hits:12}  {m.exact_hits:10}  {m.overlap_recall:14.3f}  {m.exact_recall:12.3f}  {missed:.3f}")
    lines.append(f"OVERALL {rep.overall.gold} {rep.overall.predicted} {rep.overall.overlap_hits} {rep.overall.exact_hits} {rep.overall.overlap_recall:.3f} {rep.overall.exact_recall:.3f} {1-rep.overall.overlap_recall:.3f}")
    lines.append(f"COVERAGE overlap_recall={rep.coverage.overlap_recall:.3f} exact_recall={rep.coverage.exact_recall:.3f}")
    lines.append(f"DSI reference (English-only): {LEGACY_OVERLAP_BASELINE:.2%} any-overlap, {LEGACY_EXACT_BASELINE:.2%} exact — not a threshold")
    if s.is_low_power:
        lines.append("→ Thin slice: DELIVERY.md fallback is to report redaction results for English only until Odia slice thickens.")
    lines.append("")
    return lines


def render_scorecard(gold_path: Path, *, require_analyzer: bool = False) -> str:
    per_lang = score_per_language(gold_path, require_analyzer=require_analyzer)
    lines = ["# PII scorecard — per-entity, per-language", ""]
    lines.append("Missed-PII rate = 1 − overlap_recall (the release-critical metric; F1 hides leaked PII). "
                 "Coverage is the DSI-comparable untyped overlap; by_entity is typed.")
    lines.append("")
    lines.append(f"Gold: {gold_path}  DSI reference: {LEGACY_OVERLAP_BASELINE:.2%} any-overlap (English, typed-untyped, not a threshold)")
    lines.append("")
    for lang in ["english", "odia", "romanized", "unknown"]:
        if lang in per_lang:
            lines.extend(_format_slice(lang, per_lang[lang]))
    for lang, sl in per_lang.items():
        if lang not in {"english", "odia", "romanized", "unknown"}:
            lines.extend(_format_slice(lang, sl))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Corpus scan — grep-shaped PII over redacted text
# ---------------------------------------------------------------------------

# Mobile shapes mirror the three Presidio IN_MOBILE patterns (pii_tagger.py).
_MOBILE_RES = [
    re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9]\d{2}[\s-]\d{3}[\s-]\d{4}(?!\d)"),
]
_AADHAAR_RE = re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_EMAIL_FIND_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Government email suffixes — mirrors pii_tagger._GOVERNMENT_EMAIL_SUFFIXES.
_GOV_SUFFIXES = ("nic.in", "gov.in", "mil.in")


def _is_government_email(address: str) -> bool:
    try:
        from janasunani.pipeline.stages.pii_tagger import is_government_email as _gov

        return _gov(address)
    except Exception:
        if "@" not in address:
            return False
        domain = address.strip().lower().rsplit("@", 1)[-1]
        if not domain:
            return False
        return any(domain == s or domain.endswith(f".{s}") for s in _GOV_SUFFIXES)


def find_shaped_pii(text: str) -> dict[str, list[str]]:
    """Return shaped PII matches in *text* by entity.

    Keys: ``PHONE`` (mobile shapes), ``AADHAAR``, ``PAN``, ``EMAIL``
    (non-government only). A redacted text must return an empty dict for
    every key — that is the corpus-scan invariant.
    """
    if not text:
        return {"PHONE": [], "AADHAAR": [], "PAN": [], "EMAIL": []}
    phones: list[str] = []
    seen: set[tuple[int, int]] = set()
    for pat in _MOBILE_RES:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if span not in seen:
                seen.add(span)
                phones.append(m.group(0).strip())
    aadhaars = [m.group(0).strip() for m in _AADHAAR_RE.finditer(text)]
    pans = [m.group(0).strip() for m in _PAN_RE.finditer(text)]
    emails: list[str] = []
    for m in _EMAIL_FIND_RE.finditer(text):
        addr = m.group(0)
        if not _is_government_email(addr):
            emails.append(addr)
    return {"PHONE": phones, "AADHAAR": aadhaars, "PAN": pans, "EMAIL": emails}


def contains_shaped_pii(text: str | None) -> bool:
    """True if *text* still contains any shaped PII (mobile/Aadhaar/PAN/non-gov email)."""
    if not text:
        return False
    found = find_shaped_pii(text)
    return any(len(v) > 0 for v in found.values())


@dataclass(frozen=True)
class CorpusScanResult:
    total_texts: int
    texts_with_pii: int
    by_entity: dict[str, int]
    # up to a few examples per entity for diagnostics, never more than total
    examples: dict[str, list[str]]

    def to_dict(self) -> dict:
        return {
            "total_texts": self.total_texts,
            "texts_with_pii": self.texts_with_pii,
            "by_entity": dict(self.by_entity),
            "examples": {k: list(v) for k, v in self.examples.items()},
            "passed": self.texts_with_pii == 0,
        }


def scan_texts(
    texts: Iterable[str | None],
    *,
    sample_limit: int = 3,
) -> CorpusScanResult:
    """Scan an iterable of redacted texts for shaped PII.

    Counts how many texts still carry a shape and how many times each
    entity shape appears (one text may contain multiple shapes). Collects up
    to *sample_limit* example strings per entity for diagnostics.
    """
    total = 0
    with_pii = 0
    by_entity: dict[str, int] = {"PHONE": 0, "AADHAAR": 0, "PAN": 0, "EMAIL": 0}
    examples: dict[str, list[str]] = {"PHONE": [], "AADHAAR": [], "PAN": [], "EMAIL": []}
    for text in texts:
        total += 1
        if text is None:
            continue
        found = find_shaped_pii(text)
        has_any = any(len(v) > 0 for v in found.values())
        if has_any:
            with_pii += 1
        for ent in by_entity:
            hits = found.get(ent, [])
            if hits:
                by_entity[ent] += len(hits)
                for h in hits:
                    if len(examples[ent]) < sample_limit:
                        examples[ent].append(h)
    return CorpusScanResult(
        total_texts=total,
        texts_with_pii=with_pii,
        by_entity=by_entity,
        examples=examples,
    )


def scan_corpus_parquet(
    parquet_path: Path,
    *,
    column: str = "grievance_redacted",
    sample_limit: int = 3,
) -> CorpusScanResult:
    """Scan a Parquet file's redacted-text column for shaped PII.

    ``column`` defaults to ``grievance_redacted`` (the slice redaction of
    ``complaints.grievance``) but callers may pass ``redacted_text`` for
    ``pages.parquet`` or any other text column. Reads via ``polars`` when
    available, falling back to ``pyarrow`` so the helper works in both the
    ``pii`` and ``pipeline-core`` envs.
    """
    # Try polars first (fast, project dependency of many extras)
    try:
        import polars as pl  # type: ignore

        df = pl.scan_parquet(parquet_path).select(column).collect()
        texts = df[column].to_list()
        return scan_texts(texts, sample_limit=sample_limit)
    except Exception as exc:  # pragma: no cover — fallback path
        # pyarrow fallback: no polars, or column missing, surface the error
        try:
            import pyarrow.parquet as pq  # type: ignore

            table = pq.read_table(parquet_path, columns=[column])
            col = table.column(column).to_pylist()
            return scan_texts(col, sample_limit=sample_limit)
        except Exception:
            raise exc


def assert_zero_shaped_pii(
    texts: Iterable[str | None] | CorpusScanResult,
) -> None:
    """Assert that no shaped PII survives in *texts*.

    Accepts either an iterable of strings or a precomputed
    :class:`CorpusScanResult`. Raises :class:`AssertionError` with per-entity
    counts and examples when the invariant is violated.
    """
    result = texts if isinstance(texts, CorpusScanResult) else scan_texts(texts)
    if result.texts_with_pii != 0:
        raise AssertionError(
            f"shaped PII survived redaction: {result.texts_with_pii}/{result.total_texts} texts "
            f"with hits by_entity={result.by_entity} examples={result.examples}"
        )


def format_corpus_scan(result: CorpusScanResult) -> str:
    lines = [
        "# Corpus scan — shaped PII over redacted text",
        "",
        f"total_texts={result.total_texts} texts_with_pii={result.texts_with_pii} passed={result.texts_with_pii == 0}",
        f"by_entity: PHONE={result.by_entity.get('PHONE', 0)} AADHAAR={result.by_entity.get('AADHAAR', 0)} "
        f"PAN={result.by_entity.get('PAN', 0)} EMAIL(non-gov)={result.by_entity.get('EMAIL', 0)}",
        "",
    ]
    if result.texts_with_pii == 0:
        lines.append("✓ No mobile, Aadhaar, PAN or non-government email shape survived redaction.")
    else:
        lines.append(f"✗ {result.texts_with_pii} text(s) still contain shaped PII:")
        for ent, exs in result.examples.items():
            if exs:
                lines.append(f"  {ent}: {exs[:3]}")
    lines.append("")
    lines.append("Shapes: mobile PHONE (6-9 led 10-digit, 3 groupings incl. +91/0), "
                 "AADHAAR (12-digit 2-9 led, 4-4-4), PAN ([A-Z]{5}\\d{4}[A-Z]), "
                 "EMAIL (non-gov only; nic.in/gov.in/mil.in excluded by policy #56).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-entity, per-language PII scorecard (#67)")
    parser.add_argument("--gold", type=Path, default=None, help="Gold JSONL (with optional language per record)")
    parser.add_argument("--out", type=Path, default=None, help="Write scorecard markdown to file")
    parser.add_argument("--json", action="store_true", help="Print per-language JSON")
    parser.add_argument("--json-out", type=Path, help="Write per-language aggregate JSON")
    parser.add_argument(
        "--require-analyzer",
        action="store_true",
        help="Fail instead of emitting zero-recall evidence when the live analyzer fails",
    )
    parser.add_argument("--corpus", type=Path, default=None, help="Parquet file to scan for shaped PII (e.g. data/interim/complaints.parquet)")
    parser.add_argument("--corpus-column", type=str, default="grievance_redacted", help="Text column in the corpus parquet (default: grievance_redacted)")
    parser.add_argument("--corpus-sample-limit", type=int, default=3, help="Max examples per entity in corpus scan output")
    args = parser.parse_args(argv)

    # Corpus scan mode
    if args.corpus is not None:
        if args.json:
            result = scan_corpus_parquet(args.corpus, column=args.corpus_column, sample_limit=args.corpus_sample_limit)
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            return 0 if result.texts_with_pii == 0 else 1
        result = scan_corpus_parquet(args.corpus, column=args.corpus_column, sample_limit=args.corpus_sample_limit)
        text = format_corpus_scan(result)
        if args.out:
            args.out.write_text(text)
        print(text)
        return 0 if result.texts_with_pii == 0 else 1

    if args.gold is None:
        parser.error("--gold is required unless --corpus is given")
    if args.json or args.json_out:
        per_lang = score_per_language(
            args.gold, require_analyzer=args.require_analyzer
        )
        out = {lang: sl.report.to_dict() | {"is_low_power": sl.is_low_power} for lang, sl in per_lang.items()}
        encoded = json.dumps(out, indent=2, sort_keys=True) + "\n"
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(encoded, encoding="utf-8")
        if args.json:
            print(encoded, end="")
            return 0
        if not args.out:
            return 0
    text = render_scorecard(args.gold, require_analyzer=args.require_analyzer)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
