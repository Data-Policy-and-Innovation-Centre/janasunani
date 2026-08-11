"""Prepare a private, local-only BART summary review sample.

The input must be the redacted categorization benchmark. Narrative source and
candidate text is written only to a caller-selected private review path. The
aggregate provenance sidecar contains no narrative or item identifiers and is
safe to track; structured judgments are scored separately by
``janasunani.evaluation.summary``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
from typing import Callable, Iterable, Mapping, Sequence

from janasunani.pipeline.spam import is_content_free_abuse
from janasunani.pipeline.stages.categorizer.stage import _is_english
from janasunani.pipeline.stages.summarizer import (
    MAX_INPUT_LENGTH,
    MAX_SUMMARY_LENGTH,
    MIN_SUMMARY_LENGTH,
    Summarizer,
)


SCHEMA_VERSION = "summary-development-review/v1"
PROVENANCE_VERSION = "summary-development-provenance/v1"
ADJUDICATION_RUBRIC = """Judge only the supplied redacted source and local candidate.
Set should_skip only when no coherent grievance or request merits an officer-facing
abstract. For generated candidates, count applicable atomic facts about the problem,
affected subject, place, time/status, and requested remedy; then count those faithfully
retained. Count unsupported propositions and contradictions. Mark pii_leak when the
candidate exposes an identifying detail that should not survive redaction or invents
one. Usefulness: 0 misleading/useless; 1 major rewrite; 2 useful with minor edits;
3 ready. usable_without_edit means ready for an officer as-is. Measure actual
adjudicator correction time; never interpret it as officer time saved."""
_INPUT_FIELDS = {
    "item_id",
    "group_id",
    "redacted_text",
    "category",
    "split",
    "language",
    "source_kind",
}


@dataclass(frozen=True)
class Candidate:
    item_id: str
    group_id: str
    redacted_text: str
    category: str
    split: str
    language: str
    source_kind: str

    @property
    def word_count(self) -> int:
        return len(self.redacted_text.split())


def _read_candidates(path: Path) -> list[Candidate]:
    rows: list[Candidate] = []
    seen_items: set[str] = set()
    seen_groups: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not valid JSON") from exc
        if not isinstance(payload, Mapping) or set(payload) != _INPUT_FIELDS:
            raise ValueError(f"line {line_number} does not match the redacted input schema")
        row = Candidate(**payload)
        if not row.item_id or not row.group_id or not row.redacted_text.strip():
            raise ValueError(f"line {line_number} contains an empty required value")
        if row.item_id in seen_items or row.group_id in seen_groups:
            raise ValueError("input item_id and group_id values must be unique")
        seen_items.add(row.item_id)
        seen_groups.add(row.group_id)
        rows.append(row)
    if not rows:
        raise ValueError("at least one candidate is required")
    return rows


def _rank(row: Candidate, cohort: str) -> str:
    return hashlib.sha256(f"{cohort}\0{row.item_id}".encode()).hexdigest()


def _take(
    pool: Iterable[Candidate],
    *,
    count: int,
    cohort: str,
    selected: dict[str, tuple[Candidate, str]],
) -> None:
    if count <= 0:
        return
    available = (row for row in pool if row.group_id not in selected)
    for row in sorted(available, key=lambda item: _rank(item, cohort))[:count]:
        selected[row.group_id] = (row, cohort)


def select_candidates(
    rows: Sequence[Candidate],
    *,
    split: str = "test",
    sample_size: int = 30,
    is_english: Callable[[str], bool] = _is_english,
) -> list[tuple[Candidate, str]]:
    """Select a deterministic, deliberately enriched development sample."""

    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    eligible = [row for row in rows if row.split == split]
    if len(eligible) < sample_size:
        raise ValueError(f"split {split!r} has fewer than {sample_size} rows")
    english = [row for row in eligible if is_english(row.redacted_text)]
    non_english = [row for row in eligible if not is_english(row.redacted_text)]
    selected: dict[str, tuple[Candidate, str]] = {}

    # One normal-length, serving-compatible example per historical category.
    for category in sorted({row.category for row in eligible}):
        if len(selected) >= sample_size:
            break
        _take(
            (row for row in english if row.category == category and row.word_count >= 20),
            count=min(1, sample_size - len(selected)),
            cohort=f"category:{category}",
            selected=selected,
        )

    # Exercise the wrapper's verbatim short-input guard, long-input truncation,
    # and the live language abstention path. The final fill preserves exactly n.
    _take(
        (row for row in english if row.word_count < MIN_SUMMARY_LENGTH),
        count=min(6, sample_size - len(selected)),
        cohort="short-input",
        selected=selected,
    )
    _take(
        (row for row in english if row.word_count > 120),
        count=min(3, sample_size - len(selected)),
        cohort="long-input",
        selected=selected,
    )
    _take(
        non_english,
        count=min(3, sample_size - len(selected)),
        cohort="language-abstention",
        selected=selected,
    )
    _take(eligible, count=sample_size - len(selected), cohort="deterministic-fill", selected=selected)
    if len(selected) != sample_size:
        raise ValueError("could not construct the requested unique-group sample")
    return sorted(selected.values(), key=lambda pair: pair[0].item_id)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_review(
    *,
    dataset: Path,
    private_review: Path,
    provenance: Path,
    model_path: Path,
    split: str,
    sample_size: int,
    summarizer_factory: Callable[[Path], object] = Summarizer,
    is_english: Callable[[str], bool] = _is_english,
) -> dict[str, object]:
    rows = _read_candidates(dataset)
    selected = select_candidates(
        rows, split=split, sample_size=sample_size, is_english=is_english
    )
    review_rows: list[dict[str, object]] = []
    summarizer: object | None = None
    for row, cohort in selected:
        english_compatible = is_english(row.redacted_text)
        content_free = is_content_free_abuse(row.redacted_text)
        skipped = content_free or not english_compatible
        if content_free:
            skip_reason = "bounded_content_free_regression"
        elif not english_compatible:
            skip_reason = "unsupported_language"
        else:
            skip_reason = None
        candidate_summary: str | None = None
        if not skipped:
            if summarizer is None:
                summarizer = summarizer_factory(model_path)
            candidate_summary = str(summarizer.summarize(row.redacted_text))
        review_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "item_id": row.item_id,
                "group_id": row.group_id,
                "category": row.category,
                "source_type": row.source_kind,
                "selection_cohort": cohort,
                "language_recorded": row.language,
                "english_compatible": english_compatible,
                "source_word_count": row.word_count,
                "short_input_guard": not skipped and row.word_count < MIN_SUMMARY_LENGTH,
                "skipped": skipped,
                "skip_reason": skip_reason,
                "source_text": row.redacted_text,
                "candidate_summary": candidate_summary,
            }
        )

    rendered = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in review_rows
    )
    private_review.parent.mkdir(parents=True, exist_ok=True)
    private_review.write_text(rendered, encoding="utf-8")
    private_review.chmod(0o600)

    weights = model_path / "model.safetensors"
    if not weights.is_file():
        raise FileNotFoundError(weights)
    import torch
    import transformers

    payload: dict[str, object] = {
        "schema_version": PROVENANCE_VERSION,
        "evidence_status": "single-frontier-judge-development-only",
        "publication_ready": False,
        "source": {
            "path": str(dataset),
            "sha256": _sha256_file(dataset),
            "redacted_only": True,
            "split": split,
        },
        "selection": {
            "sample_size": len(review_rows),
            "policy": "deterministic-enriched-category-short-long-language-v1",
            "not_prevalence_representative": True,
            "cohort_counts": dict(sorted(Counter(row["selection_cohort"] for row in review_rows).items())),
            "generated": sum(not bool(row["skipped"]) for row in review_rows),
            "skipped": sum(bool(row["skipped"]) for row in review_rows),
            "private_review_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        },
        "model": {
            "family": "facebook/bart-large-cnn",
            "revision": model_path.name,
            "weights_sha256": _sha256_file(weights),
            "max_input_tokens": MAX_INPUT_LENGTH,
            "min_output_tokens": MIN_SUMMARY_LENGTH,
            "max_output_tokens": MAX_SUMMARY_LENGTH,
            "num_beams": 4,
            "local_files_only": True,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
        },
        "adjudication": {
            "judge_type": "single-frontier-agent-context",
            "rubric": "summary-scorecard-v1",
            "rubric_sha256": hashlib.sha256(ADJUDICATION_RUBRIC.encode()).hexdigest(),
            "provider": "OpenAI Codex",
            "exact_served_model_revision": "unavailable",
            "prompt_and_sampling_metadata": "unavailable-beyond-committed-rubric",
            "edit_seconds_source": "frontier-judge estimate, not observed officer time",
            "narrative_review_storage": "private-temporary-only",
            "structured_judgments_only_in_governed_artifacts": True,
            "officer_validated": False,
            "independent_judges": False,
            "one_time_redacted_egress_authorized": True,
        },
        "limitations": [
            "typed redacted inputs only",
            "language labels not adjudicated",
            "single frontier-agent judge",
            "development test viewed",
            "edit time is adjudicator time, not officer time saved",
        ],
    }
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--private-review", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--sample-size", type=int, default=30)
    args = parser.parse_args(argv)
    prepare_review(
        dataset=args.dataset,
        private_review=args.private_review,
        provenance=args.provenance,
        model_path=args.model_path,
        split=args.split,
        sample_size=args.sample_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
