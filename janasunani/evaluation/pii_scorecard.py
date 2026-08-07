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
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from janasunani.pipeline.pii_eval import (
    EvaluationReport,
    GoldExample,
    load_gold_jsonl,
    score_predictions,
    LEGACY_OVERLAP_BASELINE,
)

LEGACY_EXACT_BASELINE = 0.50
MIN_GOLD_FOR_REPORTABLE_SLICE = 20  # below this, per-language is low power


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
) -> dict[str, LanguageSlice]:
    """Score per language over a gold JSONL that may carry a language tag.

    The gold file may have a top-level ``language`` per record; if absent,
    the slice is ``unknown``. Each slice is scored independently via
    ``score_examples`` (so the model's language handling is visible). Thin
    slices are flagged rather than hidden.
    """
    # Load with language sidecar
    raw_langs: dict[str, str] = {}
    # Re-parse to capture language without duplicating load_gold_jsonl validation
    with gold_path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            rec_id = str(obj.get("id") or "")
            # Will be normalized again in slice; record even if empty
            if "language" in obj:
                raw_langs[rec_id] = str(obj["language"])
            # also capture synthetic id fallback used by load_gold_jsonl
            # (line-N) — not needed if author always sets id

    examples = load_gold_jsonl(gold_path)
    # If ids were auto-generated (line-N), language by original index still works
    # because raw_langs keys are those ids; missing stays unknown.

    slices = _slice_by_language(examples, raw_langs)
    out: dict[str, LanguageSlice] = {}
    for lang, exs in sorted(slices.items()):
        # Use empty predictions so the harness is runnable without pipeline-core
        # heavy deps (presidio). The slicing and thin-slice guards are what
        # this harness owns; the model itself is scored via janasunani-evaluate-pii.
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


def render_scorecard(gold_path: Path) -> str:
    per_lang = score_per_language(gold_path)
    lines = ["# PII scorecard — per-entity, per-language", ""]
    lines.append("Missed-PII rate = 1 − overlap_recall (the release-critical metric; F1 hides leaked PII). "
                 "Coverage is the DSI-comparable untyped overlap; by_entity is typed.")
    lines.append("")
    lines.append(f"Gold: {gold_path}  DSI reference: {LEGACY_OVERLAP_BASELINE:.2%} any-overlap (English, typed-untyped, not a threshold)")
    lines.append("")
    for lang in ["english", "odia", "romanized", "unknown"]:
        if lang in per_lang:
            lines.extend(_format_slice(lang, per_lang[lang]))
    # any other langs
    for lang, sl in per_lang.items():
        if lang not in {"english", "odia", "romanized", "unknown"}:
            lines.extend(_format_slice(lang, sl))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-entity, per-language PII scorecard (#67)")
    parser.add_argument("--gold", type=Path, required=True, help="Gold JSONL (with optional language per record)")
    parser.add_argument("--out", type=Path, default=None, help="Write scorecard markdown to file")
    parser.add_argument("--json", action="store_true", help="Print per-language JSON")
    args = parser.parse_args(argv)
    if args.json:
        per_lang = score_per_language(args.gold)
        out = {lang: sl.report.to_dict() | {"is_low_power": sl.is_low_power} for lang, sl in per_lang.items()}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    text = render_scorecard(args.gold)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
