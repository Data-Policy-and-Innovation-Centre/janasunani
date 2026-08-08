from uuid import uuid4

import pytest
from mlflow.tracking import MlflowClient

from janasunani.tracking.mlflow_utils import (
    DVC_HASH_TAG,
    DVC_PATH_TAG,
    ensure_experiment,
    log_benchmark_run,
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


# ---------------------------------------------------------------------------
# log_benchmark_run — Unit F (demo-integration-rehearsal Part 5)
# ---------------------------------------------------------------------------


def test_log_benchmark_run_logs_params_and_metrics(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    run_id = log_benchmark_run(
        pipeline_variant="standard",
        sarvam_arm=None,
        schema_version="v1",
        slice_id="Sambalpur/2024",
        ocr_engine="pytesseract",
        sample_n=30,
        git_sha="abc1234",
        latency_e2e_mean=1.23,
        latency_e2e_se=0.04,
        cost_per_doc_rupees=0.0,
        cost_per_1k_tokens=0.0,
        category_accuracy_pipeline=0.71,
        category_accuracy_sarvam_extract=0.68,
        category_diff_ci_low=-0.05,
        ocr_divergence_rate=0.42,
        summary_divergence_rate=0.31,
        experiment_name="janasunani-demo-benchmark",
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    params = run.data.params
    metrics = run.data.metrics

    assert params["pipeline_variant"] == "standard"
    assert params["schema_version"] == "v1"
    assert params["slice_id"] == "Sambalpur/2024"
    assert params["ocr_engine"] == "pytesseract"
    assert params["sample_n"] == "30"
    assert params["git_sha"] == "abc1234"

    assert metrics["latency_e2e_mean"] == pytest.approx(1.23)
    assert metrics["latency_e2e_se"] == pytest.approx(0.04)
    assert metrics["cost_per_doc_rupees"] == pytest.approx(0.0)
    assert metrics["category_accuracy_pipeline"] == pytest.approx(0.71)
    assert metrics["category_accuracy_sarvam_extract"] == pytest.approx(0.68)
    assert metrics["ocr_divergence_rate"] == pytest.approx(0.42)
    assert metrics["summary_divergence_rate"] == pytest.approx(0.31)


def test_log_benchmark_run_logs_artifacts(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    # Create dummy artifacts (table2 + latency)
    latency_path = tmp_path / "latency.json"
    latency_path.write_text('{"variant": "standard"}')
    table2_path = tmp_path / "table2.md"
    table2_path.write_text("# Table 2")

    run_id = log_benchmark_run(
        pipeline_variant="sarvam_digitise",
        sarvam_arm="digitise",
        ocr_engine="sarvam",
        sample_n=10,
        experiment_name="janasunani-demo-benchmark",
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
        artifacts=[latency_path, table2_path],
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    # Artifacts are logged under the run's artifact_uri; check via list
    artifacts = client.list_artifacts(run_id)
    artifact_names = {a.path for a in artifacts}
    # mlflow.log_artifact preserves filename at top level
    assert "latency.json" in artifact_names
    assert "table2.md" in artifact_names

    run = client.get_run(run_id)
    assert run.data.params["pipeline_variant"] == "sarvam_digitise"
    assert run.data.params["sarvam_arm"] == "digitise"


def test_log_benchmark_run_rejects_unknown_variant(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    with pytest.raises(ValueError, match="pipeline_variant"):
        log_benchmark_run(
            pipeline_variant="invalid_variant",
            tracking_uri=tracking_uri,
            artifact_uri=artifact_uri,
        )


def test_log_benchmark_run_all_variants_accepted(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    for variant in ["standard", "sarvam_digitise", "sarvam_extract", "sarvam_both"]:
        run_id = log_benchmark_run(
            pipeline_variant=variant,
            tracking_uri=tracking_uri,
            artifact_uri=artifact_uri,
        )
        client = MlflowClient(tracking_uri=tracking_uri)
        run = client.get_run(run_id)
        assert run.data.params["pipeline_variant"] == variant


def test_log_benchmark_run_extra_params_and_metrics(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    run_id = log_benchmark_run(
        pipeline_variant="standard",
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
        extra_params={"custom_param": "hello"},
        extra_metrics={"custom_metric": 42.0},
        metrics={"latency_e2e_mean": 0.9},
        params={"slice_id": "Sambalpur/2024"},
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    assert run.data.params["custom_param"] == "hello"
    assert run.data.params["slice_id"] == "Sambalpur/2024"
    assert run.data.metrics["custom_metric"] == pytest.approx(42.0)
    assert run.data.metrics["latency_e2e_mean"] == pytest.approx(0.9)


def test_log_benchmark_run_missing_artifact_is_skipped(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    missing = tmp_path / "does_not_exist.json"

    # Should not raise, just warn and succeed
    run_id = log_benchmark_run(
        pipeline_variant="standard",
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
        artifacts=[missing],
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    assert run.data.params["pipeline_variant"] == "standard"


def test_log_benchmark_run_category_accuracy_metrics(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()

    run_id = log_benchmark_run(
        pipeline_variant="sarvam_extract",
        category_accuracy_pipeline=0.7104,
        category_accuracy_sarvam_extract=0.73,
        category_diff_ci_low=0.01,
        category_diff_ci_high=0.05,
        ocr_divergence_rate=0.15,
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    assert run.data.metrics["category_accuracy_pipeline"] == pytest.approx(0.7104)
    assert run.data.metrics["category_accuracy_sarvam_extract"] == pytest.approx(0.73)
    assert run.data.metrics["category_diff_ci_low"] == pytest.approx(0.01)
    assert run.data.metrics["category_diff_ci_high"] == pytest.approx(0.05)
    assert run.data.metrics["ocr_divergence_rate"] == pytest.approx(0.15)
