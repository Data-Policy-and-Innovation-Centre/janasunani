import json

import pytest

from janasunani.evaluation import sarvam_cached


def _evidence(tmp_path):
    path = tmp_path / "sarvam.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "janasunani.sarvam-cached-evidence/v1",
                "as_of": "2026-08-10",
                "provider": "sarvam-hosted",
                "model": "Sarvam Vision 1.5",
                "slice": "Sambalpur/2024",
                "extract_schema_version": "v1",
                "normalizer_version": "1.0",
                "credits_available_for_new_calls": False,
                "reproducibility": {
                    "tracked_aggregate_only": True,
                    "source_artifacts_tracked": False,
                    "source_artifact_hashes_available": False,
                    "derivation_command_recorded": False,
                    "latency_distribution_available": False,
                    "claim_limit": "Aggregate counts cannot reconstruct the source run.",
                },
                "reporting_rule": "Report aggregate coverage and divergence only.",
                "runs": [
                    {
                        "run_id": "interrupted",
                        "status": "interrupted_credit_exhaustion",
                        "arm": "both",
                        "pages_attempted": 65,
                        "pages_paired_scored": 56,
                        "accepted_jobs": 127,
                        "provider_job_failures": 7,
                        "normalized_exact_text_divergence": 1.0,
                        "sarvam_to_pytesseract_character_ratio": 1.3345,
                        "estimated_list_price_accepted_jobs_rupees": 95.0,
                        "actual_billing_available": False,
                    }
                ],
            }
        )
    )
    return path


def test_cached_import_logs_aggregate_without_provider_call(tmp_path, monkeypatch):
    calls = []

    def fake_log(**kwargs):
        calls.append(kwargs)
        return "mlflow-run-1"

    monkeypatch.setattr(sarvam_cached, "log_benchmark_run", fake_log)
    result = sarvam_cached.import_evidence(_evidence(tmp_path))

    assert result == {"interrupted": "mlflow-run-1"}
    logged = calls[0]
    assert logged["pipeline_variant"] == "sarvam_both"
    assert logged["sample_n"] == 56
    assert "cost_per_doc_rupees" not in logged
    assert logged["extra_metrics"]["cost_per_attempted_page_rupees"] == pytest.approx(
        95 / 65
    )
    assert logged["extra_metrics"]["paired_page_coverage"] == pytest.approx(56 / 65)
    assert logged["extra_metrics"]["provider_job_failure_rate"] == pytest.approx(7 / 127)
    assert logged["extra_metrics"]["cost_total_rupees"] == 95.0
    assert logged["extra_params"]["cost_evidence"] == (
        "estimated_list_price_accepted_jobs"
    )
    assert logged["extra_params"]["cost_denominator"] == "attempted_page"
    assert logged["extra_params"]["quality_claim_permitted"] == "false"
    assert len(logged["extra_params"]["evidence_sha256"]) == 64
    assert logged["extra_params"]["git_sha_role"] == "cached_evidence_import_code"
    assert logged["extra_params"]["source_run_git_sha"] == "unavailable"
    assert logged["extra_params"]["derivation_command_recorded"] == "false"
    assert logged["artifacts"]


def test_cached_import_omits_failure_rate_without_accepted_job_denominator(
    tmp_path, monkeypatch
):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["runs"][0].pop("accepted_jobs")
    path.write_text(json.dumps(payload))
    calls = []
    monkeypatch.setattr(
        sarvam_cached,
        "log_benchmark_run",
        lambda **kwargs: calls.append(kwargs) or "run",
    )

    sarvam_cached.import_evidence(path)

    assert calls[0]["extra_metrics"]["provider_job_failures"] == 7.0
    assert "provider_job_failure_rate" not in calls[0]["extra_metrics"]


def test_estimated_cost_never_implies_actual_billing(tmp_path, monkeypatch):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["runs"][0].pop("actual_billing_available")
    path.write_text(json.dumps(payload))
    calls = []
    monkeypatch.setattr(
        sarvam_cached,
        "log_benchmark_run",
        lambda **kwargs: calls.append(kwargs) or "run",
    )

    sarvam_cached.import_evidence(path)

    assert calls[0]["extra_params"]["actual_billing_available"] == "false"


def test_recorded_list_price_never_implies_actual_billing(tmp_path, monkeypatch):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    run = payload["runs"][0]
    run.pop("estimated_list_price_accepted_jobs_rupees")
    run["recorded_cost_rupees"] = 95.0
    run["cost_basis"] = "list-price calculation; actual billing unavailable"
    path.write_text(json.dumps(payload))
    calls = []
    monkeypatch.setattr(
        sarvam_cached,
        "log_benchmark_run",
        lambda **kwargs: calls.append(kwargs) or "run",
    )

    sarvam_cached.import_evidence(path)

    assert calls[0]["extra_params"]["cost_evidence"] == (
        "recorded_non_billing_amount"
    )
    assert calls[0]["extra_params"]["actual_billing_available"] == "false"


def test_cached_import_rejects_unknown_schema(tmp_path):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["schema_version"] = "future"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="schema"):
        sarvam_cached.load_evidence(path)


def test_cached_import_requires_explicit_reproducibility_boundary(tmp_path):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload.pop("reproducibility")
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="reproducibility"):
        sarvam_cached.load_evidence(path)


def test_cached_import_rejects_unknown_fields_that_could_carry_narrative(tmp_path):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["runs"][0]["raw_text"] = "must never be imported"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="unknown fields"):
        sarvam_cached.load_evidence(path)


def test_cached_import_accepts_declared_reproducibility_and_cost_basis(tmp_path):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["reproducibility"] = {
        "tracked_aggregate_only": True,
        "source_artifacts_tracked": False,
        "source_artifact_hashes_available": False,
        "derivation_command_recorded": False,
        "latency_distribution_available": False,
        "claim_limit": "Aggregate counts cannot reconstruct the source run.",
    }
    payload["runs"][0]["cost_basis"] = "list-price estimate"
    path.write_text(json.dumps(payload))

    loaded = sarvam_cached.load_evidence(path)

    assert loaded["runs"][0]["cost_basis"] == "list-price estimate"


def test_cached_import_rejects_duplicate_run_ids_and_inconsistent_counts(tmp_path):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["runs"].append(dict(payload["runs"][0]))
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicates run_id"):
        sarvam_cached.load_evidence(path)

    payload["runs"] = [payload["runs"][0]]
    payload["runs"][0]["pages_excluded"] = 8
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="pages_excluded"):
        sarvam_cached.load_evidence(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pages_paired_scored", 66, "more pages"),
        ("pages_attempted", -1, "non-negative"),
        ("normalized_exact_text_divergence", 1.1, "divergence"),
        ("sarvam_to_pytesseract_character_ratio", float("nan"), "finite"),
        ("estimated_list_price_accepted_jobs_rupees", True, "finite"),
        ("status", ["completed"], "invalid status"),
    ],
)
def test_cached_import_rejects_impossible_aggregate_metrics(tmp_path, field, value, message):
    path = _evidence(tmp_path)
    payload = json.loads(path.read_text())
    payload["runs"][0][field] = value
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        sarvam_cached.load_evidence(path)
