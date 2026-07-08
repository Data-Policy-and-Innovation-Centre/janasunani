"""Slim MLflow + DVC metadata helpers.

MLflow owns run/metric/registry metadata. DVC remains the source of truth for
artifact bytes; every registered model version gets the DVC path/hash tags that
let operators resolve the exact mirrored artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    """Return an MLflow experiment id, creating it if needed."""
    configure_tracking(tracking_uri)
    client = MlflowClient()
    experiment = client.get_experiment_by_name(name)
    if experiment is not None:
        return experiment.experiment_id
    return client.create_experiment(
        name,
        artifact_location=artifact_uri or settings.MLFLOW_ARTIFACT_URI,
    )


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
    tags = {
        DVC_PATH_TAG: dvc_path,
        DVC_HASH_TAG: dvc_hash,
        "artifact.local_path": path.as_posix(),
    }
    if extra_tags:
        tags.update(extra_tags)

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
