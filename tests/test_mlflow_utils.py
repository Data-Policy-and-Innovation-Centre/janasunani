from uuid import uuid4

import pytest
from mlflow.tracking import MlflowClient

from janasunani.tracking.mlflow_utils import (
    DVC_HASH_TAG,
    DVC_PATH_TAG,
    ensure_experiment,
    log_model_artifact,
)


def test_ensure_experiment_uses_local_artifact_root(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    name = f"janasunani-test-{uuid4().hex}"

    experiment_id = ensure_experiment(
        name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment(experiment_id)
    assert experiment.name == name
    assert experiment.artifact_location == artifact_uri


def test_ensure_experiment_reuses_when_artifact_root_matches(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    name = f"janasunani-test-{uuid4().hex}"

    first_id = ensure_experiment(
        name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )
    second_id = ensure_experiment(
        name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )

    assert first_id == second_id


def test_ensure_experiment_raises_on_artifact_root_mismatch(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    original_artifact_uri = (tmp_path / "artifacts-local").as_uri()
    changed_artifact_uri = (tmp_path / "artifacts-s3-stand-in").as_uri()
    name = f"janasunani-test-{uuid4().hex}"

    ensure_experiment(name, tracking_uri=tracking_uri, artifact_uri=original_artifact_uri)

    with pytest.raises(ValueError, match="artifact"):
        ensure_experiment(
            name, tracking_uri=tracking_uri, artifact_uri=changed_artifact_uri
        )


def test_ensure_experiment_skips_check_when_no_artifact_root_requested(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    name = f"janasunani-test-{uuid4().hex}"

    first_id = ensure_experiment(
        name, tracking_uri=tracking_uri, artifact_uri=artifact_uri
    )
    # No artifact_uri requested this time: reuse should proceed without
    # comparing against the experiment's existing (different-looking) root.
    second_id = ensure_experiment(name, tracking_uri=tracking_uri)

    assert first_id == second_id


def test_log_model_artifact_registers_dvc_tagged_version(tmp_path):
    artifact = tmp_path / "categorizer.txt"
    artifact.write_text("demo model", encoding="utf-8")
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    model_name = f"categorizer-{uuid4().hex}"

    logged = log_model_artifact(
        experiment_name="janasunani-test",
        local_path=artifact,
        dvc_path="models/categorizer",
        dvc_hash="abc123",
        registered_model_name=model_name,
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
        extra_tags={"model.kind": "categorizer"},
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(logged.run_id)
    assert run.data.tags[DVC_PATH_TAG] == "models/categorizer"
    assert run.data.tags[DVC_HASH_TAG] == "abc123"
    assert run.data.tags["model.kind"] == "categorizer"

    version = client.get_model_version(model_name, logged.model_version)
    assert version.run_id == logged.run_id
    assert version.tags[DVC_PATH_TAG] == "models/categorizer"
    assert version.tags[DVC_HASH_TAG] == "abc123"
    assert version.tags["model.kind"] == "categorizer"
    assert version.source == logged.artifact_uri


def test_log_model_artifact_dvc_tags_survive_clobbering_extra_tags(tmp_path):
    """extra_tags reusing the reserved dvc.path/dvc.hash keys must never win —
    the registered version has to keep advertising the DVC object it actually
    logged, not whatever a caller (accidentally or otherwise) passed in."""
    artifact = tmp_path / "categorizer.txt"
    artifact.write_text("demo model", encoding="utf-8")
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    model_name = f"categorizer-{uuid4().hex}"

    logged = log_model_artifact(
        experiment_name="janasunani-test",
        local_path=artifact,
        dvc_path="models/categorizer",
        dvc_hash="abc123",
        registered_model_name=model_name,
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
        extra_tags={DVC_PATH_TAG: "WRONG", DVC_HASH_TAG: "WRONG"},
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(logged.run_id)
    assert run.data.tags[DVC_PATH_TAG] == "models/categorizer"
    assert run.data.tags[DVC_HASH_TAG] == "abc123"

    version = client.get_model_version(model_name, logged.model_version)
    assert version.tags[DVC_PATH_TAG] == "models/categorizer"
    assert version.tags[DVC_HASH_TAG] == "abc123"
