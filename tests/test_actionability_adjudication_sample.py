import json
import stat

import polars as pl

from scripts.sample_actionability_adjudication import build_sample


def _write_source(path):
    remarks = {
        "s1": "complaint details inadequate",
        "s2": "no specific grievance",
        "s3": "this is not within the purview of this grievance cell",
        "s4": "can be considered only after a policy decision is made by the government",
    }
    complaints = []
    actions = []
    for year in (2021, 2024, 2025):
        for stratum in ("s1", "s2", "s3", "s4", "s5"):
            ticket = f"ticket-{year}-{stratum}"
            complaints.append(
                {
                    "ticket_no": ticket,
                    "created_year": year,
                    "grievance_redacted": f"safe redacted complaint {year} {stratum}",
                }
            )
            if stratum in remarks:
                actions.append(
                    {"ticket_no": ticket, "action_taken_remark": remarks[stratum]}
                )
    complaints.append(
        {
            "ticket_no": "excluded-phone",
            "created_year": 2024,
            "grievance_redacted": "please call 9876543210",
        }
    )
    pl.DataFrame(complaints).write_parquet(path / "complaints.parquet")
    pl.DataFrame(actions).write_parquet(path / "action_history.parquet")


def test_build_sample_is_blinded_deterministic_and_redacted(tmp_path):
    _write_source(tmp_path)
    first = tmp_path / "sample.jsonl"
    first_manifest = tmp_path / "manifest.json"
    second = tmp_path / "sample-again.jsonl"
    second_manifest = tmp_path / "manifest-again.json"
    kwargs = {
        "complaints_path": tmp_path / "complaints.parquet",
        "action_history_path": tmp_path / "action_history.parquet",
        "oltp_env_file": None,
        "per_stratum_split": 1,
        "unlabeled_per_split": 1,
        "seed": "unit-test-seed",
        "id_salt": "unit-test-private-salt-value-at-least-32",
    }
    build_sample(output_path=first, manifest_path=first_manifest, **kwargs)
    build_sample(
        output_path=second,
        manifest_path=second_manifest,
        **kwargs,
    )

    assert first.read_bytes() == second.read_bytes()
    records = [json.loads(line) for line in first.read_text().splitlines()]
    assert len(records) == 15
    assert len({row["item_id"] for row in records}) == 15
    assert {row["split"] for row in records} == {"train", "validation", "test"}
    assert all(row["item_id"] == row["group_id"] for row in records)
    assert all("ticket_no" not in row for row in records)
    assert all("action_taken_remark" not in row for row in records)
    assert all("9876543210" not in row["redacted_text"] for row in records)
    assert stat.S_IMODE(first.stat().st_mode) == 0o600

    manifest = json.loads(first_manifest.read_text())
    assert manifest["records"] == 15
    assert manifest["parameters"]["shaped_pii_excluded"] == 1
    assert manifest["parameters"]["split_policy"].startswith("chronological_")
    assert set(manifest["counts"].values()) == {1}
    assert manifest["sample_design"] == {
        "sampling_scheme": "fixed quotas across opaque sampling strata",
        "production_prevalence_representative": False,
        "metric_interpretation": (
            "accuracy, precision, PPV, and review workload measured on this "
            "sample are composition-specific and are not production prevalence"
        ),
        "intended_use": "development model comparison and error analysis",
    }


def test_build_sample_requires_one_source_mode(tmp_path):
    _write_source(tmp_path)
    output = tmp_path / "sample.jsonl"
    manifest = tmp_path / "manifest.json"
    try:
        build_sample(
            tmp_path / "complaints.parquet",
            tmp_path / "action_history.parquet",
            tmp_path / ".env",
            output,
            manifest,
            per_stratum_split=1,
            unlabeled_per_split=1,
            seed="seed",
            id_salt="salt-value-long-enough-for-test-only",
        )
    except ValueError as exc:
        assert "choose either" in str(exc)
    else:
        raise AssertionError("mixed source modes must fail closed")
