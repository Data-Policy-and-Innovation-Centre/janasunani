import hashlib
from uuid import uuid4

import pytest
from mlflow.tracking import MlflowClient

from janasunani.tracking.mlflow_utils import (
    DVC_HASH_TAG,
    DVC_PATH_TAG,
    ensure_experiment,
    log_benchmark_run,
    log_evaluation_run,
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
# Governed evaluation runs
# ---------------------------------------------------------------------------


def _governed_evaluation_args(tmp_path):
    report = tmp_path / "evaluation.json"
    report.write_text('{"accuracy": 0.75}\n', encoding="utf-8")
    return {
        "task": "actionability",
        "dataset_fingerprint": f"sha256:{'a' * 64}",
        "split_fingerprint": f"sha256:{'b' * 64}",
        "code_sha": "c" * 40,
        "dependency_lock_sha": f"sha256:{'d' * 64}",
        "report_schema": "classification-report",
        "report_version": "1.0",
        "parameters": {
            "model_family": "tfidf-logreg",
            "c": 2.0,
            "class_weight_balanced": True,
            "max_features": None,
        },
        "metrics": {"macro_f1": 0.72, "accuracy": 0.75},
        "report_path": report,
    }


def test_log_evaluation_run_records_required_provenance_and_report(tmp_path):
    tracking_uri = (tmp_path / "mlruns").as_uri()
    artifact_uri = (tmp_path / "artifacts").as_uri()
    arguments = _governed_evaluation_args(tmp_path)

    run_id = log_evaluation_run(
        **arguments,
        experiment_name="governed-test",
        tracking_uri=tracking_uri,
        artifact_uri=artifact_uri,
    )

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    params = run.data.params
    assert params["evaluation.task"] == "actionability"
    assert params["dataset.fingerprint"] == f"sha256:{'a' * 64}"
    assert params["split.fingerprint"] == f"sha256:{'b' * 64}"
    assert params["code.sha"] == "c" * 40
    assert params["dependency_lock.sha256"] == "d" * 64
    assert params["report.schema"] == "classification-report"
    assert params["report.version"] == "1.0"
    assert params["report.sha256"] == hashlib.sha256(
        arguments["report_path"].read_bytes()
    ).hexdigest()
    assert params["parameter.model_family"] == "tfidf-logreg"
    assert params["parameter.c"] == "2.0"
    assert params["parameter.class_weight_balanced"] == "true"
    assert params["parameter.max_features"] == "null"
    assert run.data.metrics["macro_f1"] == pytest.approx(0.72)
    assert run.data.metrics["accuracy"] == pytest.approx(0.75)
    assert {item.path for item in client.list_artifacts(run_id, "report")} == {
        "report/evaluation.json"
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task", "", "task"),
        ("dataset_fingerprint", "", "dataset_fingerprint"),
        ("split_fingerprint", "not-a-digest", "split_fingerprint"),
        ("code_sha", "abc1234", "code_sha"),
        ("dependency_lock_sha", "f" * 40, "dependency_lock_sha"),
        ("report_schema", "", "report_schema"),
        ("report_version", "version with spaces", "report_version"),
    ],
)
def test_log_evaluation_run_rejects_missing_or_invalid_provenance(
    tmp_path, field, value, message
):
    arguments = _governed_evaluation_args(tmp_path)
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        log_evaluation_run(**arguments, tracking_uri=(tmp_path / "mlruns").as_uri())

    # Validation is complete before MLflow creates a local experiment/run.
    assert not (tmp_path / "mlruns").exists()


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({}, "parameters"),
        ({"nested": {"c": 1}}, "scalar"),
        ({"bad value": 1}, "parameter name"),
        ({"c": float("nan")}, "scalar"),
    ],
)
def test_log_evaluation_run_rejects_incomplete_or_lossy_parameters(
    tmp_path, parameters, message
):
    arguments = _governed_evaluation_args(tmp_path)
    arguments["parameters"] = parameters

    with pytest.raises(ValueError, match=message):
        log_evaluation_run(**arguments, tracking_uri=(tmp_path / "mlruns").as_uri())


@pytest.mark.parametrize(
    ("metrics", "message"),
    [
        ({}, "metrics"),
        ({"accuracy": float("inf")}, "finite"),
        ({"accuracy": True}, "finite"),
        ({"bad metric": 0.5}, "metric name"),
    ],
)
def test_log_evaluation_run_rejects_missing_or_invalid_metrics(
    tmp_path, metrics, message
):
    arguments = _governed_evaluation_args(tmp_path)
    arguments["metrics"] = metrics

    with pytest.raises(ValueError, match=message):
        log_evaluation_run(**arguments, tracking_uri=(tmp_path / "mlruns").as_uri())


@pytest.mark.parametrize("kind", ["missing", "empty", "directory"])
def test_log_evaluation_run_requires_a_nonempty_report_file(tmp_path, kind):
    arguments = _governed_evaluation_args(tmp_path)
    report = tmp_path / f"{kind}-report.json"
    if kind == "empty":
        report.touch()
    elif kind == "directory":
        report.mkdir()
    arguments["report_path"] = report

    with pytest.raises(ValueError, match="report_path"):
        log_evaluation_run(**arguments, tracking_uri=(tmp_path / "mlruns").as_uri())


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
