"""Offline evaluation for the Presidio PII redaction stage.

Gold data is intentionally external to the repo. The CLI reads a JSONL file
only when explicitly invoked; each line must contain complaint/page text and
gold spans:

    {"id": "page-1", "text": "...", "entities": [
      {"start": 0, "end": 12, "entity": "NAME"}
    ]}

``entity`` may also be supplied as ``label``, ``type``, or ``entity_type``.
Offsets are Python-style character offsets with an exclusive ``end``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from janasunani.pipeline.stages.pii_tagger import (
    PIISpan,
    detect_pii_spans,
    normalize_entity,
)

LEGACY_OVERLAP_BASELINE = 0.8056


@dataclass(frozen=True)
class GoldExample:
    id: str
    text: str
    entities: tuple[PIISpan, ...]


@dataclass(frozen=True)
class EntityMetrics:
    gold: int
    predicted: int
    overlap_hits: int
    exact_hits: int

    @property
    def overlap_recall(self) -> float:
        return self.overlap_hits / self.gold if self.gold else 0.0

    @property
    def exact_recall(self) -> float:
        return self.exact_hits / self.gold if self.gold else 0.0

    def to_dict(self) -> dict[str, float | int]:
        data = asdict(self)
        data["overlap_recall"] = self.overlap_recall
        data["exact_recall"] = self.exact_recall
        return data


@dataclass(frozen=True)
class EvaluationReport:
    by_entity: dict[str, EntityMetrics]
    overall: EntityMetrics
    baseline_overlap_recall: float

    @property
    def passed_baseline(self) -> bool:
        return self.overall.overlap_recall >= self.baseline_overlap_recall

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall.to_dict(),
            "by_entity": {
                entity: metrics.to_dict()
                for entity, metrics in sorted(self.by_entity.items())
            },
            "baseline_overlap_recall": self.baseline_overlap_recall,
            "passed_baseline": self.passed_baseline,
        }


def load_gold_jsonl(path: Path) -> list[GoldExample]:
    examples: list[GoldExample] = []
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            try:
                text = raw["text"]
                raw_entities = raw["entities"]
            except KeyError as exc:
                raise ValueError(
                    f"{path}:{line_number}: missing required field {exc.args[0]!r}"
                ) from exc
            if not isinstance(text, str):
                raise ValueError(f"{path}:{line_number}: text must be a string")
            example_id = str(raw.get("id") or f"line-{line_number}")
            spans = tuple(
                _parse_gold_span(span, text=text, path=path, line_number=line_number)
                for span in raw_entities
            )
            examples.append(GoldExample(id=example_id, text=text, entities=spans))
    return examples


def score_examples(
    examples: Iterable[GoldExample],
    predict: Callable[[str], Sequence[PIISpan]] = detect_pii_spans,
    baseline_overlap_recall: float = LEGACY_OVERLAP_BASELINE,
) -> EvaluationReport:
    examples = list(examples)
    predictions = {example.id: tuple(predict(example.text)) for example in examples}
    return score_predictions(
        examples=examples,
        predictions=predictions,
        baseline_overlap_recall=baseline_overlap_recall,
    )


def score_predictions(
    examples: Iterable[GoldExample],
    predictions: Mapping[str, Sequence[PIISpan]],
    baseline_overlap_recall: float = LEGACY_OVERLAP_BASELINE,
) -> EvaluationReport:
    gold_counts: Counter[str] = Counter()
    prediction_counts: Counter[str] = Counter()
    overlap_hits: Counter[str] = Counter()
    exact_hits: Counter[str] = Counter()

    for example in examples:
        predicted = tuple(_normalize_span(span) for span in predictions.get(example.id, ()))
        predicted_by_entity: dict[str, list[PIISpan]] = defaultdict(list)
        for span in predicted:
            prediction_counts[span.entity] += 1
            predicted_by_entity[span.entity].append(span)

        for gold in example.entities:
            gold = _normalize_span(gold)
            gold_counts[gold.entity] += 1
            candidates = predicted_by_entity.get(gold.entity, ())
            if any(_exact_match(gold, candidate) for candidate in candidates):
                exact_hits[gold.entity] += 1
            if any(_overlaps(gold, candidate) for candidate in candidates):
                overlap_hits[gold.entity] += 1

    entities = sorted(set(gold_counts) | set(prediction_counts))
    by_entity = {
        entity: EntityMetrics(
            gold=gold_counts[entity],
            predicted=prediction_counts[entity],
            overlap_hits=overlap_hits[entity],
            exact_hits=exact_hits[entity],
        )
        for entity in entities
    }
    overall = EntityMetrics(
        gold=sum(gold_counts.values()),
        predicted=sum(prediction_counts.values()),
        overlap_hits=sum(overlap_hits.values()),
        exact_hits=sum(exact_hits.values()),
    )
    return EvaluationReport(
        by_entity=by_entity,
        overall=overall,
        baseline_overlap_recall=baseline_overlap_recall,
    )


def format_report(report: EvaluationReport) -> str:
    lines = [
        "entity gold predicted overlap_hits exact_hits overlap_recall exact_recall"
    ]
    for entity, metrics in sorted(report.by_entity.items()):
        lines.append(_format_metrics(entity, metrics))
    lines.append(_format_metrics("OVERALL", report.overall))
    lines.append(
        "baseline_overlap_recall="
        f"{report.baseline_overlap_recall:.4f} "
        f"passed={str(report.passed_baseline).lower()}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Presidio PII detection against a gold JSONL file."
    )
    parser.add_argument("--gold", type=Path, required=True, help="Gold JSONL file.")
    parser.add_argument(
        "--baseline-overlap",
        type=float,
        default=LEGACY_OVERLAP_BASELINE,
        help="Minimum acceptable overall overlap recall.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text table.",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Always exit 0 after printing the report.",
    )
    args = parser.parse_args(argv)

    report = score_examples(
        load_gold_jsonl(args.gold),
        baseline_overlap_recall=args.baseline_overlap,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0 if args.no_gate or report.passed_baseline else 1


def _parse_gold_span(
    raw: object,
    text: str,
    path: Path,
    line_number: int,
) -> PIISpan:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}:{line_number}: each entity must be an object")
    label = raw.get("entity") or raw.get("entity_type") or raw.get("label") or raw.get("type")
    if label is None:
        raise ValueError(f"{path}:{line_number}: entity span missing label")
    try:
        start = int(raw["start"])
        end = int(raw["end"])
    except KeyError as exc:
        raise ValueError(
            f"{path}:{line_number}: entity missing required field {exc.args[0]!r}"
        ) from exc
    if start < 0 or end <= start or end > len(text):
        raise ValueError(
            f"{path}:{line_number}: invalid span offsets {start}:{end} "
            f"for text length {len(text)}"
        )
    return PIISpan(entity=normalize_entity(str(label)), start=start, end=end)


def _normalize_span(span: PIISpan) -> PIISpan:
    return PIISpan(
        entity=normalize_entity(span.entity),
        start=span.start,
        end=span.end,
        score=span.score,
    )


def _exact_match(gold: PIISpan, predicted: PIISpan) -> bool:
    return gold.start == predicted.start and gold.end == predicted.end


def _overlaps(gold: PIISpan, predicted: PIISpan) -> bool:
    return max(gold.start, predicted.start) < min(gold.end, predicted.end)


def _format_metrics(entity: str, metrics: EntityMetrics) -> str:
    return (
        f"{entity} {metrics.gold} {metrics.predicted} "
        f"{metrics.overlap_hits} {metrics.exact_hits} "
        f"{metrics.overlap_recall:.4f} {metrics.exact_recall:.4f}"
    )
