import hashlib
import json
from datetime import date

import pytest

from janasunani.evaluation.routing import (
    INCIDENCE_ARTIFACT_FILENAME,
    INCIDENCE_ARTIFACT_SCHEMA_VERSION,
    INCIDENCE_OBJECTIVE,
    IncidenceRouter,
    RouteRecord,
    benchmark_incidence_router,
    load_incidence_router,
    save_incidence_router,
    validate_route_records,
)


def route(
    item_id,
    when,
    category,
    department,
    split,
    *,
    district="Sambalpur",
    subcategory=None,
    language="English",
    group=None,
    weight=1,
):
    return RouteRecord(
        item_id=item_id,
        group_id=group or item_id,
        observed_on=when,
        category=category,
        subcategory=subcategory,
        district=district,
        department=department,
        split=split,
        language=language,
        weight=weight,
    )


def dataset():
    rows = []
    departments = {
        "Water": "Water Department",
        "Pension": "Social Security",
        "Road": "Works Department",
    }
    for category, department in departments.items():
        for i in range(20):
            rows.append(
                route(
                    f"train-{category}-{i}",
                    date(2023, 1, 1),
                    category,
                    department if i < 16 else "General Administration",
                    "train",
                    district="Sambalpur" if i % 2 else "Cuttack",
                    subcategory=f"{category} issue",
                )
            )
        for i in range(4):
            rows.append(
                route(
                    f"validation-{category}-{i}",
                    date(2024, 1, 1),
                    category,
                    department,
                    "validation",
                    district="Sambalpur",
                    subcategory=f"{category} issue",
                )
            )
        for i in range(4):
            rows.append(
                route(
                    f"test-{category}-{i}",
                    date(2025, 1, 1),
                    category,
                    department,
                    "test",
                    district="Sambalpur",
                    subcategory=f"{category} issue",
                    language="Odia" if i % 2 else "English",
                )
            )
    return rows


def test_temporal_and_group_leakage_are_hard_failures():
    rows = dataset()
    rows[0] = route(
        rows[0].item_id,
        date(2024, 6, 1),
        rows[0].category,
        rows[0].department,
        "train",
    )
    with pytest.raises(ValueError, match="training must end"):
        validate_route_records(rows)

    rows = dataset()
    rows[-1] = route(
        rows[-1].item_id,
        rows[-1].observed_on,
        rows[-1].category,
        rows[-1].department,
        "test",
        group=rows[0].group_id,
    )
    with pytest.raises(ValueError, match="leaks"):
        validate_route_records(rows)


def test_smoothed_router_returns_complete_probabilities_and_backoff():
    train = [record for record in dataset() if record.split == "train"]
    router = IncidenceRouter(alpha=10.0).fit(train)

    known = router.predict_proba(category="Water", district="Sambalpur")
    unseen = router.predict_proba(category="Astronomy", district="Unknown")

    assert set(known) == set(router.departments)
    assert sum(known.values()) == pytest.approx(1.0)
    assert max(known, key=known.get) == "Water Department"
    assert sum(unseen.values()) == pytest.approx(1.0)
    assert unseen == {
        department: router._global[department] / sum(router._global.values())
        for department in router.departments
    }


def test_refit_replaces_prior_counts_instead_of_accumulating():
    training = [record for record in dataset() if record.split == "train"]
    router = IncidenceRouter(alpha=10.0).fit(training)
    first_total = sum(router._global.values())

    router.fit(training[:5])

    assert first_total == 60
    assert sum(router._global.values()) == 5


def test_serving_prediction_requires_support_concentration_and_margin():
    ambiguous = [
        route(
            f"ambiguous-{index}",
            date(2023, 1, 1),
            "Water",
            department,
            "train",
        )
        for index, department in enumerate(("A", "B", "C", "D"))
    ]
    router = IncidenceRouter(alpha=10.0, serving_min_support=3).fit(ambiguous)
    assert router.predict_with_evidence(category="Water") is None

    concentrated = [
        route(
            f"concentrated-{index}",
            date(2023, 1, 1),
            "Water",
            "A" if index < 8 else "B",
            "train",
        )
        for index in range(10)
    ]
    gated = IncidenceRouter(
        alpha=10.0,
        serving_min_support=10,
        serving_min_concentration=0.75,
        serving_min_margin=0.20,
    ).fit(concentrated)
    prediction = gated.predict_with_evidence(category="Water")
    assert prediction is not None
    assert prediction.support == 10
    assert prediction.concentration == 0.8

    sparse_district = [
        route(
            f"broad-{index}",
            date(2023, 1, 1),
            "Water",
            "A",
            "train",
            district="Cuttack",
        )
        for index in range(20)
    ] + [
        route(
            f"sparse-{index}",
            date(2023, 1, 1),
            "Water",
            "B",
            "train",
            district="Sambalpur",
        )
        for index in range(3)
    ]
    sparse = IncidenceRouter(alpha=1.0, serving_min_support=10).fit(
        sparse_district
    )
    assert (
        sparse.predict_with_evidence(category="Water", district="Sambalpur")
        is None
    )


def test_subcategory_is_an_explicit_non_live_candidate():
    train = [record for record in dataset() if record.split == "train"]
    live = IncidenceRouter(alpha=3.0, use_subcategory=False).fit(train)
    future = IncidenceRouter(alpha=3.0, use_subcategory=True).fit(train)

    live_probability = live.predict_proba(
        category="Water", district="Sambalpur", subcategory="Water issue"
    )
    future_probability = future.predict_proba(
        category="Water", district="Sambalpur", subcategory="Water issue"
    )

    assert live_probability != future_probability


def test_benchmark_selects_on_validation_and_reports_honest_objective():
    benchmark = benchmark_incidence_router(
        dataset(), alpha_values=(1.0, 10.0, 100.0)
    )
    report = benchmark.report

    assert report["objective"] == INCIDENCE_OBJECTIVE
    assert report["outcome_optimized"] is False
    assert report["label_provenance"] == {
        "source_table": "complaints",
        "source_field": "dept",
        "semantics": "unconfirmed_recorded_department_snapshot",
        "source_owner_confirmation": "unavailable",
        "not_equivalent_to": [
            "joint_department_chain_assignment_intent",
            "action_history_route_traversal",
            "correct_authority",
        ],
    }
    assert report["live_feature_shape"] == "category+district"
    assert report["split_counts"] == {"train": 60, "validation": 12, "test": 12}
    assert report["selected_alpha"] in {1.0, 10.0, 100.0}
    assert report["final_fit"] == "train_plus_validation_before_untouched_test"
    assert report["evaluation_router"] == (
        "serialized_reloaded_privacy_suppressed_artifact"
    )
    assert report["serving_gates"] == {
        "minimum_support": 10,
        "minimum_concentration": 0.5,
        "minimum_top1_margin": 0.1,
        "eligible_test_cases": 12,
        "eligible_test_coverage": 1.0,
        "fallback_test_cases": 0,
    }
    assert report["test"]["n"] == 12
    assert report["test"]["top_k_accuracy"]["3"] == 1.0
    assert report["test"]["accuracy_interval"] is None
    assert report["test"]["interval_requirement"] == (
        "district_time_or_campaign_block_uncertainty_required"
    )
    assert set(report["test_by_language"]) == {"English", "Odia"}
    assert all(
        metrics["accuracy_interval"] is None
        for metrics in report["test_by_language"].values()
    )
    assert len(report["candidate_validation"]) == 3
    assert "recorded department snapshot" in report["limitations"][0]


def test_unknown_future_department_counts_as_failure_instead_of_disappearing():
    rows = dataset()
    rows.append(
        route(
            "test-new-dept",
            date(2025, 1, 1),
            "Water",
            "New Department",
            "test",
        )
    )
    benchmark = benchmark_incidence_router(rows, alpha_values=(10.0,))

    assert benchmark.report["unseen_departments"] == ["New Department"]
    assert benchmark.report["test"]["accuracy"] < 1.0


def test_weighted_route_cells_count_underlying_cases():
    rows = dataset()
    rows[0] = route(
        "train-0",
        date(2023, 1, 1),
        "Water",
        "Water Department",
        "train",
        weight=10,
    )
    result = benchmark_incidence_router(rows, alpha_values=(3.0,))

    assert result.report["split_counts"]["train"] == 69


def test_history_window_is_selected_on_validation_then_rolls_forward():
    result = benchmark_incidence_router(
        dataset(),
        alpha_values=(3.0,),
        history_year_values=(None, 1),
    )

    assert result.report["selected_history_years"] in {None, 1}
    assert len(result.report["candidate_validation"]) == 2
    assert result.report["final_fit_cases"] == 72


def test_incidence_artifact_round_trip_is_aggregate_only_and_checksummed(tmp_path):
    training = [record for record in dataset() if record.split == "train"]
    router = IncidenceRouter(alpha=10.0).fit(training)

    artifact = save_incidence_router(router, tmp_path / "release")
    payload = json.loads(artifact.read_text())
    loaded = load_incidence_router(artifact)

    assert artifact.name == INCIDENCE_ARTIFACT_FILENAME
    assert payload["schema_version"] == INCIDENCE_ARTIFACT_SCHEMA_VERSION
    assert payload["objective"] == INCIDENCE_OBJECTIVE
    assert payload["outcome_optimized"] is False
    assert payload["label_provenance"] == {
        "source_table": "complaints",
        "source_field": "dept",
        "semantics": "unconfirmed_recorded_department_snapshot",
        "source_owner_confirmation": "unavailable",
        "not_equivalent_to": [
            "joint_department_chain_assignment_intent",
            "action_history_route_traversal",
            "correct_authority",
        ],
    }
    assert payload["privacy"] == {
        "contains_citizen_text_or_identifiers": False,
        "minimum_key_support": 3,
    }
    serialized = artifact.read_text()
    for forbidden in (
        "item_id",
        "group_id",
        "observed_on",
        "language",
        "train-Water-0",
    ):
        assert forbidden not in serialized
    assert loaded is not None
    assert loaded.serving_min_support == 10
    assert loaded.serving_min_concentration == 0.5
    assert loaded.serving_min_margin == 0.1
    assert loaded.predict_proba(
        category="Water", district="Sambalpur"
    ) == pytest.approx(
        router.predict_proba(category="Water", district="Sambalpur")
    )


def test_incidence_artifact_rejects_checksum_and_schema_tampering(tmp_path):
    training = [record for record in dataset() if record.split == "train"]
    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0).fit(training), tmp_path / "second"
    )
    payload = json.loads(artifact.read_text())
    payload["parameters"]["alpha"] = 999.0
    artifact.write_text(json.dumps(payload))
    assert load_incidence_router(artifact) is None

    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0).fit(training), tmp_path / "provenance"
    )
    payload = json.loads(artifact.read_text())
    payload["label_provenance"]["semantics"] = "confirmed_initial_assignment"
    unsigned = dict(payload)
    unsigned.pop("checksum")
    canonical = json.dumps(
        unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["checksum"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    artifact.write_text(json.dumps(payload))
    assert load_incidence_router(artifact) is None

    artifact = save_incidence_router(
        IncidenceRouter(alpha=10.0).fit(training), tmp_path
    )
    payload = json.loads(artifact.read_text())
    payload["unexpected"] = "field"
    # Re-signing must not make an expanded schema acceptable. The strict
    # top-level field allowlist is a separate control from integrity.
    unsigned = dict(payload)
    unsigned.pop("checksum")
    canonical = json.dumps(
        unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    payload["checksum"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    artifact.write_text(json.dumps(payload))
    assert load_incidence_router(artifact) is None


def test_benchmark_can_publish_an_explicit_serving_artifact(tmp_path):
    benchmark = benchmark_incidence_router(
        dataset(), alpha_values=(10.0,), artifact_dir=tmp_path / "release"
    )

    manifest = benchmark.report["serving_artifact"]
    assert manifest["outcome_optimized"] is False
    assert manifest["contains_citizen_text_or_identifiers"] is False
    assert manifest["label_provenance"]["source_owner_confirmation"] == "unavailable"
    loaded = load_incidence_router(tmp_path / "release")
    assert loaded is not None
    assert loaded.predict_proba(
        category="Water", district="Sambalpur"
    ) == pytest.approx(
        benchmark.router.predict_proba(category="Water", district="Sambalpur")
    )


def test_benchmark_scores_the_privacy_suppressed_reloaded_router():
    rows = [
        route(
            f"train-common-{index}",
            date(2023, 1, 1),
            "Common",
            "Department A",
            "train",
            district=None,
        )
        for index in range(4)
    ]
    rows.extend(
        [
            route(
                "train-sparse",
                date(2023, 1, 1),
                "Sparse",
                "Department B",
                "train",
                district=None,
            ),
            route(
                "validation-sparse",
                date(2024, 1, 1),
                "Sparse",
                "Department B",
                "validation",
                district=None,
            ),
            route(
                "test-sparse",
                date(2025, 1, 1),
                "Sparse",
                "Department B",
                "test",
                district=None,
            ),
        ]
    )

    benchmark = benchmark_incidence_router(rows, alpha_values=(0.1,))

    # The two-case Sparse cell is below the artifact's privacy floor. A richer
    # in-memory fit would predict Department B, but the serialized/reloaded
    # router must back off to its Department A global majority and be scored as
    # wrong on the untouched test case.
    assert max(
        benchmark.router.predict_proba(category="Sparse"),
        key=benchmark.router.predict_proba(category="Sparse").get,
    ) == "Department A"
    assert benchmark.report["test"]["accuracy"] == 0.0


def test_incidence_artifact_is_immutable(tmp_path):
    training = [record for record in dataset() if record.split == "train"]
    router = IncidenceRouter(alpha=10.0).fit(training)
    destination = tmp_path / "release"
    save_incidence_router(router, destination)

    with pytest.raises(FileExistsError):
        save_incidence_router(router, destination)
