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
import sys
import tempfile
from collections import Counter
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
GOLD_MANIFEST_VERSION = "actionability-gold-manifest-v1"
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


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _parse_named_path(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("encoder must have the form NAME=LOCAL_PATH")
    return name.strip(), Path(raw_path).expanduser()


def validate_gold_manifest(
    gold_path: Path, manifest_path: Path, records: Sequence[object]
) -> None:
    """Bind governed gold to its adjudication and sample-design contract."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"cannot read actionability gold manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("actionability gold manifest must be an object")

    expected = {
        "schema_version": GOLD_MANIFEST_VERSION,
        "gold_sha256": _sha256(gold_path),
        "records": len(records),
        "label_distribution": dict(
            sorted(Counter(str(record.label) for record in records).items())
        ),
        "split_distribution": dict(
            sorted(Counter(str(record.split) for record in records).items())
        ),
    }
    for field, expected_value in expected.items():
        if manifest.get(field) != expected_value:
            raise ValueError(f"actionability gold manifest {field} mismatch")

    if {str(record.label_source) for record in records} != {"frontier_adjudicated"}:
        raise ValueError(
            "actionability gold must contain only frontier-adjudicated labels"
        )
    resolution = manifest.get("resolution")
    if (
        not isinstance(resolution, dict)
        or resolution.get("uncertain_resolver_labels_enter_gold") is not False
    ):
        raise ValueError(
            "actionability gold manifest does not exclude uncertain labels"
        )
    sample_design = manifest.get("sample_design")
    if (
        not isinstance(sample_design, dict)
        or sample_design.get("production_prevalence_representative") is not False
    ):
        raise ValueError("actionability gold manifest sample design is not governed")
    sample_manifest_sha256 = sample_design.get("sample_manifest_sha256")
    if not _is_sha256(sample_manifest_sha256):
        raise ValueError(
            "actionability gold manifest has no sample-manifest fingerprint"
        )

    provenance = manifest.get("provenance")
    if provenance != {
        "source": "PII-redacted DPIC-controlled sample",
        "adjudication": (
            "two independent frontier judges; confident third-resolver labels "
            "for disagreement or uncertainty; unresolved rows excluded"
        ),
        "claim_status": "development gold, not officer-confirmed truth",
    }:
        raise ValueError("actionability gold manifest provenance contract mismatch")
    adjudication = manifest.get("adjudication_provenance")
    required_adjudication_fields = {
        "protocol_version",
        "rubric_version",
        "prompt_sha256",
        "judge_a_model",
        "judge_b_model",
        "resolver_model",
        "inference_environment",
        "egress_policy",
        "retention_policy",
    }
    if (
        not isinstance(adjudication, dict)
        or set(adjudication) != required_adjudication_fields
    ):
        raise ValueError("actionability gold adjudication provenance is incomplete")
    if not all(
        isinstance(value, str) and value.strip() for value in adjudication.values()
    ):
        raise ValueError("actionability gold adjudication provenance has empty values")
    prompt_sha256 = adjudication["prompt_sha256"]
    if prompt_sha256 != "unavailable" and not _is_sha256(prompt_sha256):
        raise ValueError("actionability gold adjudication prompt has no fingerprint")


def _assert_aggregate_only(value: object, *, path: str = "report") -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_OUTPUT_KEYS.intersection(value)
        if forbidden:
            raise ValueError(
                f"{path} contains forbidden text keys: {sorted(forbidden)!r}"
            )
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
    manifest_path: Path,
    encoder_specs: Sequence[LocalEncoderSpec],
    c_values: Sequence[float],
    min_review_precision: float,
    max_actionable_review_rate: float,
    artifact_dir: Path | None = None,
) -> dict[str, object]:
    records = load_jsonl(gold_path)
    validate_gold_manifest(gold_path, manifest_path, records)
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
    review_index = tfidf_classes.index("review_required")
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
        "objective": "actionable_vs_officer_review",
        "gold": {
            "path": gold_path.as_posix(),
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
    if artifact_dir is not None:
        if ranked[0] != "tfidf_word_char":
            raise ValueError(
                "refusing to export TF-IDF because validation selected another candidate"
            )
        tfidf.save(artifact_dir, benchmark_report=report)
    return report


def _write_json_atomic(
    path: Path, report: dict[str, object], *, overwrite: bool
) -> None:
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
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="export the validation-selected TF-IDF scorer as a local artifact",
    )
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
    parser.add_argument(
        "--c-values", type=float, nargs="+", default=[0.1, 0.5, 1, 2, 10]
    )
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
        manifest_path=args.manifest,
        encoder_specs=specs,
        c_values=args.c_values,
        min_review_precision=args.min_review_precision,
        max_actionable_review_rate=args.max_actionable_review_rate,
        artifact_dir=args.artifact_dir,
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
                "artifact_dir": str(args.artifact_dir) if args.artifact_dir else None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
