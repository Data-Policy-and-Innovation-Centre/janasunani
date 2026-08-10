"""Compare cheap local actionability-review candidates on governed gold.

The command is deliberately offline: every pretrained encoder must be an
already present local directory or cached Hugging Face snapshot.  Output is a
privacy-safe aggregate JSON scorecard and never contains grievance text or
item-level predictions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from janasunani.evaluation.actionability import (
    benchmark_binary_review,
    load_jsonl,
    sample_design_summary,
)
from janasunani.evaluation.embedding_actionability import (
    LocalEncoderSpec,
    TransformersMeanPoolEncoder,
    binary_discrimination_metrics,
    benchmark_frozen_encoder,
    resolve_cached_huggingface_snapshot,
)

SCHEMA_VERSION = "actionability-candidate-benchmark-v1"
MINILM_REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
_FORBIDDEN_OUTPUT_KEYS = {
    "redacted_text",
    "grievance",
    "complaint_text",
    "raw_text",
    "grievance_text",
    "unredacted_text",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _parse_named_path(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("encoder must have the form NAME=LOCAL_PATH")
    return name.strip(), Path(raw_path).expanduser()


def _assert_aggregate_only(value: object, *, path: str = "report") -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_OUTPUT_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"{path} contains forbidden text keys: {sorted(forbidden)!r}")
        for key, child in value.items():
            _assert_aggregate_only(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_aggregate_only(child, path=f"{path}[{index}]")


def _validation_rank(report: dict[str, object]) -> tuple[float, float, float]:
    validation = report["validation"]
    if not isinstance(validation, dict):
        raise ValueError("candidate report has no aggregate validation metrics")
    return (
        float(validation["review_recall"]),
        float(validation["review_precision"]),
        float(validation["accuracy"]),
    )


def build_report(
    *,
    gold_path: Path,
    encoder_specs: Sequence[LocalEncoderSpec],
    c_values: Sequence[float],
    min_review_precision: float,
    max_actionable_review_rate: float,
) -> dict[str, object]:
    records = load_jsonl(gold_path)
    tfidf = benchmark_binary_review(
        records,
        c_values=c_values,
        min_review_precision=min_review_precision,
        max_actionable_review_rate=max_actionable_review_rate,
    )
    split_records = {
        split: [record for record in records if record.split == split]
        for split in ("validation", "test")
    }
    tfidf_classes = tuple(str(label) for label in tfidf.classifier.classes_)
    review_index = tfidf_classes.index("review")
    for split, rows in split_records.items():
        probabilities = [
            float(row[review_index])
            for row in tfidf.classifier.predict_proba(
                [record.redacted_text for record in rows]
            )
        ]
        tfidf.report[f"{split}_discrimination"] = binary_discrimination_metrics(
            probabilities, rows
        )
    candidates: dict[str, dict[str, object]] = {"tfidf_word_char": tfidf.report}
    for spec in encoder_specs:
        if spec.name in candidates:
            raise ValueError(f"duplicate candidate name {spec.name!r}")
        encoder = TransformersMeanPoolEncoder(spec)
        candidates[spec.name] = benchmark_frozen_encoder(
            records,
            encoder,
            c_values=c_values,
            min_review_precision=min_review_precision,
            max_actionable_review_rate=max_actionable_review_rate,
        ).report

    ranked = sorted(
        candidates,
        key=lambda name: (*_validation_rank(candidates[name]), name),
        reverse=True,
    )
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "objective": "actionable_vs_officer_review",
        "gold": {
            "path": str(gold_path.resolve()),
            "sha256": _sha256(gold_path),
            "n": len(records),
            "split_counts": dict(sorted(Counter(row.split for row in records).items())),
            "label_counts": dict(sorted(Counter(row.label for row in records).items())),
            "label_source_counts": dict(
                sorted(Counter(row.label_source for row in records).items())
            ),
            "sample_design": sample_design_summary(records),
        },
        "protocol": {
            "encoder_training": "frozen",
            "probe_training_split": "train",
            "hyperparameter_selection_split": "validation",
            "threshold_selection_split": "validation",
            "candidate_ranking_split": "validation",
            "test_usage": "aggregate_report_only",
            "provider_calls": False,
            "local_files_only": True,
            "confidence_intervals": "two-sided 95% Wilson score intervals",
        },
        "software": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": importlib.metadata.version("numpy"),
            "scikit_learn": importlib.metadata.version("scikit-learn"),
            "torch": importlib.metadata.version("torch"),
            "transformers": importlib.metadata.version("transformers"),
        },
        "constraints": {
            "min_review_precision": min_review_precision,
            "max_actionable_review_rate": max_actionable_review_rate,
        },
        "validation_ranking": ranked,
        "validation_selected_candidate": ranked[0],
        "candidates": candidates,
        "privacy": {
            "aggregate_only": True,
            "contains_grievance_text": False,
            "contains_item_level_predictions": False,
        },
        "release_eligible": False,
        "limitations": [
            "candidate selection is development-only and not a production promotion",
            sample_design_summary(records)["limitation"],
        ],
    }
    _assert_aggregate_only(report)
    return report


def _write_json_atomic(path: Path, report: dict[str, object], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--multilingual-encoder",
        action="append",
        default=[],
        type=_parse_named_path,
        metavar="NAME=LOCAL_PATH",
    )
    parser.add_argument(
        "--english-diagnostic",
        action="append",
        default=[],
        type=_parse_named_path,
        metavar="NAME=LOCAL_PATH",
    )
    parser.add_argument(
        "--cached-minilm",
        action="store_true",
        help="use the already cached all-MiniLM-L6-v2 snapshot; never download",
    )
    parser.add_argument("--hf-cache-dir", type=Path)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--c-values", type=float, nargs="+", default=[0.1, 0.5, 1, 2, 10])
    parser.add_argument("--min-review-precision", type=float, default=0.9)
    parser.add_argument("--max-actionable-review-rate", type=float, default=0.05)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    specs = [
        LocalEncoderSpec(
            name=name,
            model_path=path,
            role="multilingual_candidate",
            max_length=args.max_length,
            batch_size=args.batch_size,
        )
        for name, path in args.multilingual_encoder
    ]
    specs.extend(
        LocalEncoderSpec(
            name=name,
            model_path=path,
            role="english_diagnostic",
            max_length=args.max_length,
            batch_size=args.batch_size,
        )
        for name, path in args.english_diagnostic
    )
    if args.cached_minilm:
        specs.append(
            LocalEncoderSpec(
                name="minilm_english_diagnostic",
                model_path=resolve_cached_huggingface_snapshot(
                    MINILM_REPO_ID, cache_dir=args.hf_cache_dir
                ),
                role="english_diagnostic",
                max_length=args.max_length,
                batch_size=args.batch_size,
            )
        )
    if not specs:
        raise SystemExit(
            "at least one local encoder is required: use --multilingual-encoder, "
            "--english-diagnostic, or --cached-minilm"
        )
    report = build_report(
        gold_path=args.gold,
        encoder_specs=specs,
        c_values=args.c_values,
        min_review_precision=args.min_review_precision,
        max_actionable_review_rate=args.max_actionable_review_rate,
    )
    _write_json_atomic(args.output, report, overwrite=args.overwrite)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "gold_sha256": report["gold"]["sha256"],
                "validation_selected_candidate": report[
                    "validation_selected_candidate"
                ],
                "candidate_count": len(report["candidates"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
