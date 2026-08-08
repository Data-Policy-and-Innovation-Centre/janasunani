"""Slim MLflow + DVC metadata helpers.

MLflow owns run/metric/registry metadata. DVC remains the source of truth for
artifact bytes; every registered model version gets the DVC path/hash tags that
let operators resolve the exact mirrored artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Mapping, Optional

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from janasunani.config import settings


DVC_PATH_TAG = "dvc.path"
DVC_HASH_TAG = "dvc.hash"


@dataclass(frozen=True)
class LoggedModelArtifact:
    run_id: str
    experiment_id: str
    artifact_uri: str
    registered_model_name: Optional[str] = None
    model_version: Optional[str] = None


def configure_tracking(tracking_uri: Optional[str] = None) -> str:
    """Configure MLflow's tracking backend and return the URI in use."""
    uri = tracking_uri or settings.MLFLOW_TRACKING_URI
    if uri.startswith("file:") or "://" not in uri:
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(uri)
    return uri


def ensure_experiment(
    name: str,
    *,
    tracking_uri: Optional[str] = None,
    artifact_uri: Optional[str] = None,
) -> str:
    """Return an MLflow experiment id, creating it if needed.

    MLflow pins an experiment's artifact location at creation time; it cannot be
    changed later. If ``artifact_uri`` (or ``settings.MLFLOW_ARTIFACT_URI``) names
    a desired artifact root and an experiment with this name already exists
    pointing somewhere else, raise instead of silently reusing it — otherwise
    artifacts would keep landing in the stale location (e.g. the local default)
    even after the caller configured a durable root (e.g. S3), while
    registration still reports success.
    """
    configure_tracking(tracking_uri)
    desired_artifact_uri = artifact_uri or settings.MLFLOW_ARTIFACT_URI
    client = MlflowClient()
    experiment = client.get_experiment_by_name(name)
    if experiment is not None:
        if desired_artifact_uri is not None and _normalize_artifact_uri(
            experiment.artifact_location
        ) != _normalize_artifact_uri(desired_artifact_uri):
            raise ValueError(
                f"Experiment {name!r} already exists with artifact_location "
                f"{experiment.artifact_location!r}, which differs from the "
                f"requested artifact root {desired_artifact_uri!r}. MLflow "
                "cannot relocate an experiment's artifact store after "
                "creation; use a differently named experiment or reconcile "
                "the artifact root before proceeding."
            )
        return experiment.experiment_id
    return client.create_experiment(
        name,
        artifact_location=desired_artifact_uri,
    )


def _normalize_artifact_uri(uri: Optional[str]) -> Optional[str]:
    """Normalize an artifact URI for equality comparison across MLflow's
    own formatting (e.g. trailing slashes) without changing the compared
    values' meaning."""
    if uri is None:
        return None
    return uri.rstrip("/")


def log_model_artifact(
    *,
    experiment_name: str,
    local_path: Path | str,
    dvc_path: str,
    dvc_hash: str,
    registered_model_name: Optional[str] = None,
    tracking_uri: Optional[str] = None,
    artifact_uri: Optional[str] = None,
    artifact_subdir: str = "model",
    extra_tags: Optional[Mapping[str, str]] = None,
) -> LoggedModelArtifact:
    """Log an artifact and optionally create a registered model version.

    ``local_path`` may be a file or directory. The registered model version
    points at the logged MLflow artifact URI; DVC metadata is copied to both the
    run and model-version tags.
    """
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(path)

    experiment_id = ensure_experiment(
        experiment_name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )
    tags: dict[str, str] = {"artifact.local_path": path.as_posix()}
    if extra_tags:
        tags.update(extra_tags)
    # The DVC provenance tags are the guarantee this module exists to provide:
    # they must always reflect the artifact actually logged in this call, so
    # apply them last and let them win over any caller-supplied extra_tags
    # (accidental or otherwise) that reuse the same reserved keys.
    tags[DVC_PATH_TAG] = dvc_path
    tags[DVC_HASH_TAG] = dvc_hash

    with mlflow.start_run(experiment_id=experiment_id) as run:
        mlflow.set_tags(tags)
        if path.is_dir():
            mlflow.log_artifacts(path.as_posix(), artifact_path=artifact_subdir)
        else:
            mlflow.log_artifact(path.as_posix(), artifact_path=artifact_subdir)
        source_uri = mlflow.get_artifact_uri(artifact_subdir)
        run_id = run.info.run_id

    version = None
    if registered_model_name:
        version = _create_model_version(
            registered_model_name,
            source_uri=source_uri,
            run_id=run_id,
            tags=tags,
        )

    return LoggedModelArtifact(
        run_id=run_id,
        experiment_id=experiment_id,
        artifact_uri=source_uri,
        registered_model_name=registered_model_name,
        model_version=version,
    )


def _create_model_version(
    name: str,
    *,
    source_uri: str,
    run_id: str,
    tags: Mapping[str, str],
) -> str:
    client = MlflowClient()
    try:
        client.create_registered_model(name)
    except MlflowException as exc:
        if "already exists" not in str(exc).lower():
            raise

    model_version = client.create_model_version(
        name=name,
        source=source_uri,
        run_id=run_id,
    )
    for key, value in tags.items():
        client.set_model_version_tag(name, model_version.version, key, value)
    return model_version.version


# ---------------------------------------------------------------------------
# Benchmark run logger — Unit F (demo-integration-rehearsal Part 5)
# ---------------------------------------------------------------------------

_VALID_BENCHMARK_VARIANTS: set[str] = {
    "standard",
    "sarvam_digitise",
    "sarvam_extract",
    "sarvam_both",
}

_BENCHMARK_EXPERIMENT_DEFAULT = "janasunani-demo-benchmark"


def _get_git_sha() -> str | None:
    """Return short HEAD sha or None if not in a git repo."""
    try:
        import subprocess

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


def log_benchmark_run(
    *,
    pipeline_variant: str,
    sarvam_arm: str | None = None,
    schema_version: str | None = None,
    slice_id: str | None = None,
    ocr_engine: str | None = None,
    sample_n: int | None = None,
    git_sha: str | None = None,
    # Metrics
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
    # Generic extensions
    extra_params: Mapping[str, str] | None = None,
    extra_metrics: Mapping[str, float] | None = None,
    metrics: Mapping[str, float] | None = None,
    params: Mapping[str, str] | None = None,
    artifacts: list[Path | str] | None = None,
    experiment_name: str = _BENCHMARK_EXPERIMENT_DEFAULT,
    tracking_uri: str | None = None,
    artifact_uri: str | None = None,
) -> str:
    """Log a benchmark comparison run to MLflow.

    This is the narrow MLflow surface for the 14 Aug demo. It logs a
    single benchmark variant comparison as one MLflow run so operators can
    compare ``standard`` vs ``sarvam_*`` side-by-side in the UI. It does
    NOT switch live serving.

    Params (logged as MLflow params, all string-typed):
      pipeline_variant (required): one of standard / sarvam_digitise /
        sarvam_extract / sarvam_both
      sarvam_arm: digitise | extract | both (when Sarvam is involved)
      schema_version: pinned grievance extract schema version (e.g. v1)
      slice_id: slice label like Sambalpur/2024
      ocr_engine: pytesseract | sarvam
      sample_n: number of documents/pages in the benchmark sample
      git_sha: git HEAD sha for reproducibility

    Metrics (logged as MLflow metrics, all float-typed):
      latency_e2e_mean, latency_e2e_se,
      cost_per_doc_rupees, cost_per_1k_tokens,
      category_accuracy_pipeline, category_accuracy_sarvam_extract,
      category_diff_ci_low, category_diff_ci_high,
      ocr_divergence_rate, summary_divergence_rate

    Artifacts (optional): list of file or directory paths to log under the
    run. Typical: table2.md, table2.json, latency.json,
    sarvam_scorecard.json. Missing paths are skipped with a warning
    (the run still succeeds).

    Generic ``params`` / ``metrics`` / ``extra_params`` / ``extra_metrics``
    allow callers to pass additional keys without changing this signature.

    Returns the MLflow run_id.
    """
    if pipeline_variant not in _VALID_BENCHMARK_VARIANTS:
        raise ValueError(
            f"unknown pipeline_variant {pipeline_variant!r}; "
            f"valid: {sorted(_VALID_BENCHMARK_VARIANTS)}"
        )

    # Build param dict — explicit args win over generic maps
    param_dict: dict[str, str] = {}
    if params:
        param_dict.update({k: str(v) for k, v in params.items() if v is not None})
    if extra_params:
        for k, v in extra_params.items():
            if k not in param_dict and v is not None:
                param_dict[k] = str(v)

    # Explicit typed params override generic
    explicit_params: dict[str, str | None] = {
        "pipeline_variant": pipeline_variant,
        "sarvam_arm": sarvam_arm,
        "schema_version": schema_version,
        "slice_id": slice_id,
        "ocr_engine": ocr_engine,
        "sample_n": str(sample_n) if sample_n is not None else None,
        "git_sha": git_sha or _get_git_sha(),
    }
    for k, v in explicit_params.items():
        if v is not None:
            param_dict[k] = str(v)

    # Build metric dict
    metric_dict: dict[str, float] = {}
    if metrics:
        for k, v in metrics.items():
            if v is not None:
                metric_dict[k] = float(v)
    if extra_metrics:
        for k, v in extra_metrics.items():
            if k not in metric_dict and v is not None:
                metric_dict[k] = float(v)

    explicit_metrics: dict[str, float | None] = {
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
    for k, v in explicit_metrics.items():
        if v is not None:
            metric_dict[k] = float(v)

    experiment_id = ensure_experiment(
        experiment_name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )

    # Start run and log
    with mlflow.start_run(experiment_id=experiment_id) as run:
        if param_dict:
            mlflow.log_params(param_dict)
        if metric_dict:
            # Filter out NaN/inf which MLflow rejects
            clean_metrics = {
                k: float(v)
                for k, v in metric_dict.items()
                if v is not None and math.isfinite(float(v))
            }
            if clean_metrics:
                mlflow.log_metrics(clean_metrics)
        # Artifacts
        if artifacts:
            for art in artifacts:
                p = Path(art)
                if not p.exists():
                    # Log warning but do not fail the run — a missing
                    # optional artifact (e.g. table2.md not yet generated)
                    # should not block benchmark comparison.
                    import warnings

                    warnings.warn(
                        f"benchmark artifact not found, skipping: {p}",
                        stacklevel=2,
                    )
                    continue
                if p.is_dir():
                    mlflow.log_artifacts(p.as_posix())
                else:
                    mlflow.log_artifact(p.as_posix())
        run_id = run.info.run_id

    return run_id
