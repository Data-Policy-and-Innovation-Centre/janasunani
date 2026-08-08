"""Thin MLflow benchmark logger — batch benchmark comparison only.

This module is the narrow MLflow surface for Unit F. It does NOT switch
live serving at runtime (out of scope for 14 Aug per the plan) and does
NOT run inside ``make up``. It logs benchmark runs to the local file
store (``file://.../mlruns``, already in ``janasunani.config.settings``)
so operators can compare variants side-by-side:

* ``standard``        — local pytesseract + Presidio + MuRIL + BART
* ``sarvam_digitise`` — Sarvam Vision digitise in OCR stage
* ``sarvam_extract``  — Sarvam Vision extract (schema) at document level
* ``sarvam_both``     — digitise + extract (₹1.50/page)

Usage via ``janasunani.tracking.mlflow_utils.log_benchmark_run`` directly,
or via the helpers here that build params/metrics from
``scripts/benchmark_pipeline`` outputs and optional scorecard artifacts.

Post-demo, a provider registry in config (``JANASUNANI_OCR_PROVIDER``)
and a live Sarvam path behind the egress kill-switch are the intended
follow-ons — documented, not built here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

EXPERIMENT_NAME = "janasunani-demo-benchmark"

# Valid variants — mirrors scripts/benchmark_pipeline.VALID_VARIANTS so the
# two stay in sync without a circular import at module load time.
VALID_VARIANTS: set[str] = {
    "standard",
    "sarvam_digitise",
    "sarvam_extract",
    "sarvam_both",
}

# Sarvam arms for the scorecard side (digitise vs extract vs both)
VALID_SARVAM_ARMS: set[str] = {"digitise", "extract", "both"}


def _get_git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            return sha if sha else None
    except Exception:
        pass
    return None


def build_benchmark_params(
    *,
    pipeline_variant: str,
    sarvam_arm: str | None = None,
    schema_version: str | None = None,
    slice_id: str | None = None,
    ocr_engine: str | None = None,
    sample_n: int | None = None,
    git_sha: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build the param dict for ``log_benchmark_run``.

    All values are stringified because MLflow params are string-typed.
    ``None`` values are omitted. ``extra`` is merged last (explicit args win
    over extra on key collision).
    """
    if pipeline_variant not in VALID_VARIANTS:
        raise ValueError(
            f"unknown pipeline_variant {pipeline_variant!r}; "
            f"valid: {sorted(VALID_VARIANTS)}"
        )
    if sarvam_arm is not None and sarvam_arm not in VALID_SARVAM_ARMS:
        raise ValueError(
            f"unknown sarvam_arm {sarvam_arm!r}; valid: {sorted(VALID_SARVAM_ARMS)}"
        )
    sha = git_sha or _get_git_sha()
    raw: dict[str, Any] = {
        "pipeline_variant": pipeline_variant,
        "sarvam_arm": sarvam_arm,
        "schema_version": schema_version,
        "slice_id": slice_id,
        "ocr_engine": ocr_engine,
        "sample_n": sample_n,
        "git_sha": sha,
    }
    params: dict[str, str] = {}
    for k, v in raw.items():
        if v is not None:
            params[k] = str(v)
    if extra:
        for k, v in extra.items():
            if k not in params and v is not None:
                params[k] = str(v)
    return params


def build_benchmark_metrics(
    *,
    latency_e2e_mean: float | None = None,
    latency_e2e_se: float | None = None,
    cost_per_doc_rupees: float | None = None,
    cost_per_1k_tokens: float | None = None,
    category_accuracy_pipeline: float | None = None,
    category_accuracy_sarvam_extract: float | None = None,
    category_diff_ci_low: float | None = None,
    category_diff_ci_high: float | None = None,
    ocr_divergence_rate: float | None = None,
    summary_divergence_rate: float | None = None,
    extra: dict[str, float] | None = None,
    latency_stats: dict[str, Any] | None = None,
    scorecard: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Build the metric dict for ``log_benchmark_run``.

    Callers may pass individual metric kwargs, or supply
    ``latency_stats`` (a stage stats dict as produced by
    ``scripts.benchmark_pipeline.compute_stage_stats`` / the
    ``stages.e2e`` entry in latency.json) and/or a ``scorecard`` dict
    (as produced by ``sarvam_scorecard.build_scorecard().to_dict()``) to
    auto-populate the standard metric names.

    Explicit kwargs win over auto-populated values. ``None`` values are
    omitted. ``extra`` is merged last for caller-defined metrics that do
    not yet have a named kwarg.
    """
    metrics: dict[str, float] = {}

    # Auto from latency_stats (expects keys mean_seconds, se_seconds etc.,
    # or already-named latency_e2e_*).
    if latency_stats is not None:
        # latency.json stores stages.e2e with mean_seconds/se_seconds
        if "mean_seconds" in latency_stats and latency_e2e_mean is None:
            latency_e2e_mean = float(latency_stats["mean_seconds"])
        if "se_seconds" in latency_stats and latency_e2e_se is None:
            latency_e2e_se = float(latency_stats["se_seconds"])
        # Also accept already-prefixed keys if caller passed a flat metrics
        # dict as latency_stats by mistake — be tolerant.

    if scorecard is not None:
        # Category arm
        cat = scorecard.get("category")
        if isinstance(cat, dict):
            if (
                category_accuracy_pipeline is None
                and "pipeline_accuracy" in cat
            ):
                category_accuracy_pipeline = float(cat["pipeline_accuracy"])
            if (
                category_accuracy_sarvam_extract is None
                and "sarvam_accuracy" in cat
            ):
                category_accuracy_sarvam_extract = float(
                    cat["sarvam_accuracy"]
                )
            if category_diff_ci_low is None and "ci_low" in cat:
                category_diff_ci_low = float(cat["ci_low"])
            if category_diff_ci_high is None and "ci_high" in cat:
                category_diff_ci_high = float(cat["ci_high"])
        # Divergence arm
        div = scorecard.get("transcription_divergence")
        if isinstance(div, dict) and ocr_divergence_rate is None:
            # Scorecard calls this transcription_divergence; the benchmark
            # metric is ocr_divergence_rate (same number, clearer name).
            if "rate" in div:
                ocr_divergence_rate = float(div["rate"])

    raw: dict[str, float | None] = {
        "latency_e2e_mean": latency_e2e_mean,
        "latency_e2e_se": latency_e2e_se,
        "cost_per_doc_rupees": cost_per_doc_rupees,
        "cost_per_1k_tokens": cost_per_1k_tokens,
        "category_accuracy_pipeline": category_accuracy_pipeline,
        "category_accuracy_sarvam_extract": category_accuracy_sarvam_extract,
        "category_diff_ci_low": category_diff_ci_low,
        "category_diff_ci_high": category_diff_ci_high,
        "ocr_divergence_rate": ocr_divergence_rate,
        "summary_divergence_rate": summary_divergence_rate,
    }
    for k, v in raw.items():
        if v is not None:
            metrics[k] = float(v)
    if extra:
        for k, v in extra.items():
            if k not in metrics and v is not None:
                metrics[k] = float(v)
    return metrics


def load_latency_e2e(path: Path | str) -> dict[str, Any] | None:
    """Load the ``stages.e2e`` dict from a latency.json file.

    Supports both single-variant files (top-level ``stages``) and
    multi-variant files (top-level ``variants``). When multi-variant,
    returns the ``standard`` entry if present, else the first variant.
    Returns None if the file does not exist or has no e2e entry.
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    # Single-variant
    if "stages" in data and isinstance(data["stages"], dict):
        e2e = data["stages"].get("e2e")
        if isinstance(e2e, dict):
            return e2e
    # Multi-variant
    variants = data.get("variants")
    if isinstance(variants, dict):
        # Prefer standard, else first
        for key in ("standard",):
            if key in variants:
                stages = variants[key].get("stages", {})
                e2e = stages.get("e2e") if isinstance(stages, dict) else None
                if isinstance(e2e, dict):
                    return e2e
        for variant_data in variants.values():
            if isinstance(variant_data, dict):
                stages = variant_data.get("stages", {})
                e2e = stages.get("e2e") if isinstance(stages, dict) else None
                if isinstance(e2e, dict):
                    return e2e
    return None


def log_benchmark(
    *,
    pipeline_variant: str,
    sarvam_arm: str | None = None,
    schema_version: str | None = None,
    slice_id: str | None = None,
    ocr_engine: str | None = None,
    sample_n: int | None = None,
    git_sha: str | None = None,
    latency_path: Path | str | None = None,
    scorecard_path: Path | str | None = None,
    extra_params: dict[str, Any] | None = None,
    extra_metrics: dict[str, float] | None = None,
    artifacts: list[Path | str] | None = None,
    experiment_name: str = EXPERIMENT_NAME,
    tracking_uri: str | None = None,
    artifact_uri: str | None = None,
    # Explicit metric overrides (win over file-loaded values)
    latency_e2e_mean: float | None = None,
    latency_e2e_se: float | None = None,
    cost_per_doc_rupees: float | None = None,
    cost_per_1k_tokens: float | None = None,
    category_accuracy_pipeline: float | None = None,
    category_accuracy_sarvam_extract: float | None = None,
    category_diff_ci_low: float | None = None,
    category_diff_ci_high: float | None = None,
    ocr_divergence_rate: float | None = None,
    summary_divergence_rate: float | None = None,
) -> str:
    """Convenience wrapper that builds params/metrics and calls
    ``janasunani.tracking.mlflow_utils.log_benchmark_run``.

    Loads latency e2e stats from ``latency_path`` and scorecard metrics
    from ``scorecard_path`` when provided, then delegates.

    Returns the MLflow run_id.
    """
    # Lazy import to avoid circular import at load time
    from janasunani.tracking.mlflow_utils import log_benchmark_run

    latency_stats: dict[str, Any] | None = None
    if latency_path is not None:
        latency_stats = load_latency_e2e(latency_path)
        # Also try to infer sample_n from the file if not already set
        if sample_n is None and latency_stats is not None:
            try:
                p = Path(latency_path)
                if p.is_file():
                    data = json.loads(p.read_text())
                    # Single-variant
                    if "n_docs" in data:
                        sample_n = int(data["n_docs"])
                    elif "variants" in data:
                        # Use first variant's n_docs
                        first = next(iter(data["variants"].values()))
                        if isinstance(first, dict) and "n_docs" in first:
                            sample_n = int(first["n_docs"])
            except Exception:
                pass

    scorecard: dict[str, Any] | None = None
    if scorecard_path is not None:
        sp = Path(scorecard_path)
        if sp.is_file():
            try:
                scorecard = json.loads(sp.read_text())
            except Exception:
                scorecard = None

    # Infer ocr_engine from variant if not set
    if ocr_engine is None:
        if pipeline_variant == "standard":
            ocr_engine = "pytesseract"
        elif pipeline_variant in ("sarvam_digitise", "sarvam_both"):
            ocr_engine = "sarvam"
        elif pipeline_variant == "sarvam_extract":
            ocr_engine = "sarvam"

    metrics = build_benchmark_metrics(
        latency_e2e_mean=latency_e2e_mean,
        latency_e2e_se=latency_e2e_se,
        cost_per_doc_rupees=cost_per_doc_rupees,
        cost_per_1k_tokens=cost_per_1k_tokens,
        category_accuracy_pipeline=category_accuracy_pipeline,
        category_accuracy_sarvam_extract=category_accuracy_sarvam_extract,
        category_diff_ci_low=category_diff_ci_low,
        category_diff_ci_high=category_diff_ci_high,
        ocr_divergence_rate=ocr_divergence_rate,
        summary_divergence_rate=summary_divergence_rate,
        extra=extra_metrics,
        latency_stats=latency_stats,
        scorecard=scorecard,
    )

    params = build_benchmark_params(
        pipeline_variant=pipeline_variant,
        sarvam_arm=sarvam_arm,
        schema_version=schema_version,
        slice_id=slice_id,
        ocr_engine=ocr_engine,
        sample_n=sample_n,
        git_sha=git_sha,
        extra=extra_params,
    )

    # Collect artifact paths to log: explicit artifacts plus the two
    # standard files if they exist and were not already listed.
    artifact_paths: list[Path | str] = list(artifacts or [])
    for candidate in (latency_path, scorecard_path):
        if candidate is not None and candidate not in artifact_paths:
            # Only include if the file actually exists
            if Path(candidate).is_file():
                artifact_paths.append(candidate)

    return log_benchmark_run(
        pipeline_variant=pipeline_variant,
        sarvam_arm=sarvam_arm,
        schema_version=schema_version,
        slice_id=slice_id,
        ocr_engine=ocr_engine,
        sample_n=sample_n,
        git_sha=params.get("git_sha"),
        latency_e2e_mean=metrics.get("latency_e2e_mean"),
        latency_e2e_se=metrics.get("latency_e2e_se"),
        cost_per_doc_rupees=metrics.get("cost_per_doc_rupees"),
        cost_per_1k_tokens=metrics.get("cost_per_1k_tokens"),
        category_accuracy_pipeline=metrics.get(
            "category_accuracy_pipeline"
        ),
        category_accuracy_sarvam_extract=metrics.get(
            "category_accuracy_sarvam_extract"
        ),
        category_diff_ci_low=metrics.get("category_diff_ci_low"),
        category_diff_ci_high=metrics.get("category_diff_ci_high"),
        ocr_divergence_rate=metrics.get("ocr_divergence_rate"),
        summary_divergence_rate=metrics.get("summary_divergence_rate"),
        extra_metrics={
            k: v
            for k, v in metrics.items()
            if k
            not in {
                "latency_e2e_mean",
                "latency_e2e_se",
                "cost_per_doc_rupees",
                "cost_per_1k_tokens",
                "category_accuracy_pipeline",
                "category_accuracy_sarvam_extract",
                "category_diff_ci_low",
                "category_diff_ci_high",
                "ocr_divergence_rate",
                "summary_divergence_rate",
            }
        }
        or None,
        extra_params={
            k: v
            for k, v in params.items()
            if k
            not in {
                "pipeline_variant",
                "sarvam_arm",
                "schema_version",
                "slice_id",
                "ocr_engine",
                "sample_n",
                "git_sha",
            }
        }
        or None,
        artifacts=artifact_paths or None,
        experiment_name=experiment_name,
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
    )
