"""Real-code-path tests for the routing-outcome experiments.

These cover the defects that invalidated the 11 Aug run. Nothing here reads
`data/`; the mapping tables are synthesised in `tmp_path`.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json

import duckdb
import numpy as np
import pandas as pd
import pytest

from janasunani.analytics.findings import discards
from janasunani.experiments.routing_outcome import (
    censoring,
    crossfit,
    outcome,
    provenance,
    robustness,
    smear,
    tau,
)
from janasunani.experiments.routing_outcome import e0_flow_census as census_mod
from janasunani.experiments.routing_outcome import flow as flow_mod
from janasunani.experiments.routing_outcome.features import (
    ACTION_DEFINITION,
    ACTION_COLUMNS,
    FeatureEncoder,
    cell_key,
    decode_action,
    decode_flow_columns,
    encode_action,
)
from janasunani.experiments.routing_outcome.ope import (
    ArmValue,
    cluster_bootstrap_se,
    dr_scores,
    historical_value,
    summarise,
)
from janasunani.experiments.routing_outcome.models import (
    boosted_correctness_model,
    boosted_duration_model,
    ridge_duration_model,
)
from janasunani.experiments.routing_outcome.policy import EligibleSets, score_policy
from janasunani.experiments.routing_outcome.propensity import (
    EmpiricalSharePropensity,
    effective_sample_size,
    overlap_report,
)


def _frame(districts, categories, flows, *, n_esc=None, departments=None) -> pd.DataFrame:
    n = len(districts)
    department_ids = departments if departments is not None else [10] * n
    return pd.DataFrame(
        {
            "district": districts,
            "category": categories,
            "block": ["B"] * n,
            "mode": ["online"] * n,
            "office": ["Collector"] * n,
            "dept": ["Water Resources"] * n,
            "dept_id": department_ids,
            "pending_with_id": ["14"] * n,
            "transfer_status": ["No"] * n,
            "self_assign": ["No"] * n,
            "n_esc": n_esc if n_esc is not None else [2] * n,
            "all_esc_user": flows,
            "created_on": pd.to_datetime(["2023-03-04"] * n),
            "days_capped": [30.0] * n,
            "correct": [1] * n,
        }
    )


# --------------------------------------------------------------------------
# FeatureEncoder: the per-dataframe categorical codes that broke the first run
# --------------------------------------------------------------------------


def test_encoder_codes_are_stable_across_splits_with_different_level_order():
    """The bug: `pd.Categorical(df[col]).codes` per split permuted the levels."""
    train = _frame(["Angul", "Bolangir", "Cuttack"], ["Water", "Land", "Water"], [None] * 3)
    # Same three districts, different sort order and different frequencies.
    val = _frame(["Cuttack", "Cuttack", "Angul"], ["Water", "Water", "Land"], [None] * 3)
    for df in (train, val):
        decode_flow_columns(df, {})

    encoder = FeatureEncoder.fit(train)
    train_codes = encoder.transform(train)["district_code"]
    val_codes = encoder.transform(val)["district_code"]

    code_of = dict(zip(train["district"], train_codes))
    assert list(val_codes) == [code_of[d] for d in val["district"]]

    # A naive per-frame encoding disagrees, which is what made the fix necessary.
    naive = pd.Categorical(val["district"]).codes
    assert list(naive) != list(val_codes)


def test_encoder_maps_unseen_levels_to_sentinel():
    train = _frame(["Angul"], ["Water"], [None])
    val = _frame(["Khordha"], ["Water"], [None])
    for df in (train, val):
        decode_flow_columns(df, {})

    encoder = FeatureEncoder.fit(train)
    assert encoder.transform(val)["district_code"].iloc[0] == -1


def test_encoder_codes_numeric_department_ids_rather_than_voiding_them():
    """Integer department IDs must match the train-fitted string levels.

    Comparing raw ints against string levels would make the joint-action column
    a constant -1 and silently erase the department half of the treatment.
    """
    train = _frame(
        ["Angul"] * 3,
        ["Water"] * 3,
        ["100"] * 3,
        departments=[31, 81, 81],
    )
    decode_flow_columns(train, {})

    encoder = FeatureEncoder.fit(train)
    codes = encoder.transform(train)["department_code"]
    assert (codes >= 0).all()
    assert codes.iloc[1] == codes.iloc[2]  # same id, same code
    assert codes.iloc[0] != codes.iloc[1]
    assert codes.nunique() == 2


def test_encoder_omits_leaking_and_split_collinear_columns():
    train = _frame(["Angul"], ["Water"], [None])
    decode_flow_columns(train, {})
    columns = FeatureEncoder.fit(train).feature_names()
    # `benefitted` partly defines `correct`; `year` is collinear with the split.
    assert "govt_ticket" not in columns
    assert "year" not in columns
    # This is a transient not-yet-assigned state, not a baseline history flag.
    assert "transfer_status" not in columns
    assert "month" in columns


def test_encoder_is_invariant_to_transient_transfer_state():
    """Changing a current workflow-state flag cannot alter assignment-time X."""
    train = _frame(["Angul", "Angul"], ["Water", "Water"], ["100", "100"])
    decode_flow_columns(train, {"100": "1"})
    encoder = FeatureEncoder.fit(train)

    changed = train.copy()
    changed["transfer_status"] = ["Yes", "No"]

    pd.testing.assert_frame_equal(
        encoder.transform(train),
        encoder.transform(changed),
    )


def test_robustness_cli_uses_derived_output_without_mutating_ladder(
    tmp_path, monkeypatch
):
    """A run must not overwrite a dated record or consume its global config."""
    before = deepcopy(robustness.LADDER)

    def fake_ablation(_splits, *, name, note, **_kwargs):
        return robustness.AblationResult(
            name=name,
            rmse_flow=1.0,
            rmse_noflow=1.1,
            delta=0.1,
            delta_eval_se=0.01,
            n_train=10,
            n_val=5,
            note=note,
        )

    monkeypatch.setattr(robustness, "_load_splits", lambda: {})
    monkeypatch.setattr(robustness, "run_ablation", fake_ablation)
    monkeypatch.setattr(
        robustness,
        "seed_is_inert",
        lambda: {"model": "gbm", "identical_across_seeds": True},
    )
    monkeypatch.setattr(robustness.paths, "OUT_DIR", tmp_path)

    assert robustness.main(["--exercise", "ladder", "--fit-draws", "0"]) == 0
    assert robustness.LADDER == before

    payload = json.loads((tmp_path / "robustness.json").read_text())
    assert payload["schema_version"] == robustness.SCHEMA_VERSION
    assert payload["action_definition"] == ACTION_DEFINITION
    assert date.fromisoformat(payload["generated_at"]) <= date.today()
    assert [row["name"] for row in payload["results"]] == list(before)


def test_robustness_seed_check_exercises_the_fitted_estimator():
    result = robustness.seed_is_inert()
    assert result == {
        "model": "gbm",
        "identical_across_seeds": True,
        "subsample": 1.0,
        "max_features": None,
    }


# --------------------------------------------------------------------------
# provenance.py: assignment timing versus current transfer state
# --------------------------------------------------------------------------


def test_assignment_provenance_separates_transfer_timing_without_claiming_versions(
    tmp_path,
):
    complaints = pd.DataFrame(
        {
            "ticket_no": ["none", "before", "after", "waiting", "bad-flag"],
            "created_on": pd.to_datetime(["2026-01-01"] * 5),
            "resolved_on": pd.to_datetime([None, None, "2026-01-04", None, None]),
            "last_updated_on": pd.to_datetime(
                ["2026-01-01", "2026-01-03", "2026-01-04", "2026-01-02", "2026-01-01"]
            ),
            "assigned_on": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-02", None, "2026-01-01"]
            ),
            "transfer_status": ["No", "No", "No", "Yes", "Yes"],
            "status": ["Open", "Open", "Disposed", "Not Assigned", "Open"],
            "all_esc_user": ["1,2", "1,2", "1,2", None, "1,2"],
            "pending_with_id": [1, 1, 1, None, 1],
        }
    )
    actions = pd.DataFrame(
        {
            "ticket_no": ["none", "before", "after", "waiting"],
            "action_taken_date": pd.to_datetime(
                ["2026-01-01", "2026-01-01", "2026-01-03", "2026-01-01"]
            ),
            "action_status": [
                "Open",
                provenance.TRANSFER_ACTION,
                provenance.TRANSFER_ACTION,
                provenance.TRANSFER_ACTION,
            ],
        }
    )
    complaints_path = tmp_path / "complaints.parquet"
    actions_path = tmp_path / "action_history.parquet"
    complaints.to_parquet(complaints_path)
    actions.to_parquet(actions_path)

    report = provenance.assignment_provenance_audit(complaints_path, actions_path)

    assert report["explicit_transfer_history"] == {
        "tickets": 3,
        "events": 3,
        "pre_assignment_only": 1,
        "pre_assignment_on_creation_day": 1,
        "at_or_after_assignment": 1,
        "unassigned": 1,
    }
    assert report["current_transfer_state"] == {
        "flag_yes": 2,
        "history_but_flag_no": 2,
        "flag_yes_without_history": 1,
        "flag_yes_other_status": 1,
        "flag_yes_with_assignment": 1,
        "flag_yes_with_chain": 1,
        "flag_yes_with_pending_holder": 1,
        "flag_yes_resolved": 0,
    }
    field_provenance = report["assignment_field_provenance"]
    assert field_provenance["status"] == "not_identified_from_current_snapshot"
    assert field_provenance["action_history_route_snapshot_columns"] == []
    assert "cannot reveal an earlier value" in field_provenance["reason"]


@pytest.mark.parametrize("factory", [ridge_duration_model, boosted_duration_model])
def test_duration_models_are_invariant_to_permuting_nominal_codes(factory):
    """Integer category labels must not create a distance or ordering."""
    rows = 60
    x = pd.DataFrame(
        {
            "department_code": np.tile([0, 1, 2], rows // 3),
            "district_code": np.tile([0, 0, 1, 1, 2], rows // 5),
            "n_esc": np.tile([1, 2], rows // 2),
        }
    )
    y = pd.Series(
        2.0
        + 0.7 * (x["department_code"] == 1)
        - 0.3 * (x["district_code"] == 2)
        + 0.1 * x["n_esc"]
    )
    permuted = x.copy()
    permuted["department_code"] = permuted["department_code"].map({0: 20, 1: 3, 2: 11})
    permuted["district_code"] = permuted["district_code"].map({0: 9, 1: 2, 2: 14})
    features = list(x.columns)

    original = factory(features).fit(x, y).predict(x)
    relabelled = factory(features).fit(permuted, y).predict(permuted)

    assert np.allclose(original, relabelled)


def test_nominal_model_pipelines_handle_unseen_levels_with_finite_predictions():
    x = pd.DataFrame(
        {
            "department_code": np.tile([0, 1], 20),
            "district_code": np.tile([0, 1, 2, 3], 10),
            "n_esc": np.tile([1, 2], 20),
        }
    )
    y_duration = pd.Series(2.0 + 0.2 * x["department_code"] + 0.1 * x["n_esc"])
    y_correct = pd.Series((np.arange(len(x)) % 3) == 0, dtype=int)
    unseen = pd.DataFrame(
        {"department_code": [99], "district_code": [77], "n_esc": [2]}
    )
    features = list(x.columns)

    duration_predictions = [
        ridge_duration_model(features).fit(x, y_duration).predict(unseen),
        boosted_duration_model(features).fit(x, y_duration).predict(unseen),
    ]
    probability = (
        boosted_correctness_model(features)
        .fit(x, y_correct)
        .predict_proba(unseen)[:, 1]
    )

    assert all(np.isfinite(prediction).all() for prediction in duration_predictions)
    assert np.isfinite(probability).all()


def test_encoder_action_override_rewrites_department_and_chain_together():
    """Counterfactual scoring changes both halves of the joint action."""
    train = _frame(
        ["Angul", "Angul"],
        ["Water", "Water"],
        ["100,200", "100"],
        departments=[10, 81],
    )
    decode_flow_columns(train, {"100": "1", "200": "5"})
    encoder = FeatureEncoder.fit(train)

    observed = encoder.transform(train)
    counterfactual = encoder.transform(train, action="81::1,5")

    assert (counterfactual["n_esc"] == 2).all()
    assert (counterfactual["department_code"] == counterfactual["department_code"].iloc[0]).all()
    assert (counterfactual["department_code"] >= 0).all()
    assert not observed["department_code"].equals(counterfactual["department_code"])
    assert (counterfactual["entry_role_code"] == counterfactual["entry_role_code"].iloc[0]).all()
    # Covariates are untouched.
    for column in ("district_code", "category_code", "month"):
        pd.testing.assert_series_equal(observed[column], counterfactual[column])


def test_encoder_ablation_drops_joint_action_columns():
    train = _frame(["Angul"], ["Water"], ["100,200"])
    decode_flow_columns(train, {"100": "1", "200": "5"})
    encoder = FeatureEncoder.fit(train)
    ablated = encoder.transform(train, include_action=False)
    assert not set(ACTION_COLUMNS) & set(ablated.columns)


def test_decode_flow_columns_builds_joint_action_and_nulls_unmappable_chains():
    df = _frame(["Angul", "Angul"], ["Water", "Water"], ["100,200", "100,999"])
    decode_flow_columns(df, {"100": "1", "200": "5"})
    assert df["chain_template"].tolist() == ["1,5", None]
    assert df["action_template"].tolist() == ["10::1,5", None]


def test_joint_action_identifier_round_trips_department_and_complete_chain():
    action = encode_action(31, "1,5,8")
    assert action == "31::1,5,8"
    assert decode_action(action) == ("31", "1,5,8")
    assert decode_action("chain-only") is None


def test_census_detects_when_one_chain_spans_departments():
    frame = _frame(
        ["Angul"] * 3,
        ["Water"] * 3,
        ["100,200", "100,200", "100"],
        departments=[10, 20, 10],
    )
    tables = type(
        "Tables",
        (),
        {
            "user_role": {"100": "1", "200": "5"},
            "role_name": {"1": "BDO", "5": "Collector"},
        },
    )()

    census = census_mod.flow_census(frame, tables)

    assert census["n_templates"] == 2
    assert census["n_joint_actions"] == 3
    assert census["n_multidepartment_templates"] == 1
    assert census["rows_on_multidepartment_templates"] == 2


def test_cell_key_keeps_nulls_addressable():
    df = _frame([None, "Angul"], ["Water", None], [None, None])
    assert cell_key(df).tolist() == ["Water|MISSING", "MISSING|Angul"]


# --------------------------------------------------------------------------
# Propensity and overlap
# --------------------------------------------------------------------------


def _propensity_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cell": ["A"] * 8 + ["B"] * 2,
            "action_template": ["10::1,5"] * 6 + ["20::6"] * 2 + ["10::1,5"] * 2,
        }
    )


def test_propensity_returns_training_shares():
    model = EmpiricalSharePropensity.fit(_propensity_frame())
    scored = model.score(pd.Series(["A", "A"]), pd.Series(["10::1,5", "20::6"]))
    assert scored.tolist() == pytest.approx([0.75, 0.25])


def test_propensity_backs_off_to_marginal_not_the_clip_floor():
    """The bug: an unseen (cell, flow) returned 0.01, inflating 1/e by 100x."""
    model = EmpiricalSharePropensity.fit(_propensity_frame())
    unseen_in_cell = model.score(pd.Series(["B"]), pd.Series(["20::6"])).iloc[0]
    assert unseen_in_cell == pytest.approx(0.2)  # marginal share of "6"
    assert unseen_in_cell > model.clip_low

    unseen_anywhere = model.score(pd.Series(["B"]), pd.Series(["9,9,9"])).iloc[0]
    assert unseen_anywhere == pytest.approx(model.clip_low)


def test_effective_sample_size_penalises_concentrated_weight():
    assert effective_sample_size(np.ones(10)) == pytest.approx(10.0)
    assert effective_sample_size(np.array([100.0, 1.0, 1.0])) < 1.2
    assert effective_sample_size(np.zeros(5)) == 0.0


def test_overlap_report_counts_only_matched_rows():
    propensity = pd.Series([0.5, 0.5, 0.02, 0.5])
    matched = pd.Series([True, True, False, False])
    report = overlap_report(propensity, matched)
    assert report["n_matched"] == 2
    assert report["match_rate"] == pytest.approx(0.5)
    assert report["ess"] == pytest.approx(2.0)
    assert report["ess_over_n"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# The augmented score (doubly robust in m/e only when G is correct)
# --------------------------------------------------------------------------


def test_dr_collapses_to_direct_method_when_the_model_is_exact():
    outcome = np.array([10.0, 20.0, 30.0])
    scores = dr_scores(
        outcome=outcome,
        mu_observed=outcome,  # zero residual
        mu_policy=np.array([5.0, 6.0, 7.0]),
        matched=np.array([True, True, True]),
        propensity=np.array([0.5, 0.5, 0.5]),
    )
    assert scores.mean() == pytest.approx(6.0)


def test_exact_outcome_model_does_not_protect_against_wrong_censoring_weights():
    """D and T form an independent 2x2 population, but R depends on Y through time.

    Even with the exact conditional mean, replacing R/G(Y) by R biases the
    augmented score. Correct G is required in both m/e robustness branches.
    """
    outcome = np.array([0.0, 0.0, 10.0, 10.0])
    exact_mu = np.full(4, 5.0)
    matched = np.ones(4, dtype=bool)
    propensity = np.ones(4)
    observed = np.array([True, True, False, True])

    correct_r_over_g = np.array([1.0, 1.0, 0.0, 2.0])
    wrong_r_over_g = observed.astype(float)
    correct = dr_scores(
        outcome,
        exact_mu,
        exact_mu,
        matched,
        propensity,
        censoring_weight=correct_r_over_g,
    )
    wrong = dr_scores(
        outcome,
        exact_mu,
        exact_mu,
        matched,
        propensity,
        censoring_weight=wrong_r_over_g,
    )

    assert correct.mean() == pytest.approx(5.0)
    assert wrong.mean() != pytest.approx(5.0)


def test_correct_propensity_branch_still_requires_correct_censoring_weights():
    """With misspecified mu and correct e, correct R/G recovers the value."""
    outcome = np.array([0.0, 0.0, 10.0, 10.0])
    wrong_mu = np.zeros(4)
    correct = dr_scores(
        outcome,
        wrong_mu,
        wrong_mu,
        np.ones(4, dtype=bool),
        np.ones(4),
        censoring_weight=np.array([1.0, 1.0, 0.0, 2.0]),
    )
    wrong = dr_scores(
        outcome,
        wrong_mu,
        wrong_mu,
        np.ones(4, dtype=bool),
        np.ones(4),
        censoring_weight=np.array([1.0, 1.0, 0.0, 1.0]),
    )
    assert correct.mean() == pytest.approx(5.0)
    assert wrong.mean() != pytest.approx(5.0)


def test_dr_recovers_the_sample_mean_under_the_logging_policy():
    """History matches everywhere with e=1, so DR must return mean(T)."""
    outcome = np.array([10.0, 20.0, 30.0])
    mu = np.array([12.0, 18.0, 33.0])
    scores = dr_scores(
        outcome=outcome,
        mu_observed=mu,
        mu_policy=mu,
        matched=np.ones(3, dtype=bool),
        propensity=np.ones(3),
    )
    assert scores.mean() == pytest.approx(outcome.mean())


def test_self_normalisation_bounds_a_single_low_propensity_match():
    kwargs = dict(
        outcome=np.array([10.0, 10.0, 10.0, 400.0]),
        mu_observed=np.array([10.0, 10.0, 10.0, 10.0]),
        mu_policy=np.full(4, 10.0),
        matched=np.array([False, False, False, True]),
        propensity=np.array([0.5, 0.5, 0.5, 0.01]),
    )
    raw = dr_scores(**kwargs, self_normalise=False).mean()
    normalised = dr_scores(**kwargs, self_normalise=True).mean()
    assert raw == pytest.approx(10.0 + 390.0 / 0.01 / 4)
    assert normalised == pytest.approx(10.0 + 390.0)
    assert normalised < raw


def test_historical_value_separates_the_two_estimators():
    """The stochastic baseline retains like-with-like outcome/model routes."""
    df = pd.DataFrame({"outcome": [10.0, 30.0], "mu": [15.0, 15.0]})
    arm = historical_value(df, outcome_col="outcome", mu_observed_col="mu")
    assert arm.v_dr == pytest.approx(20.0)  # mean of realised T
    assert arm.v_direct == pytest.approx(15.0)  # mean of fitted values
    assert arm.v_dr != arm.v_direct

    summary = summarise(arm, [ArmValue("candidate", 12.0, 18.0)], censoring_rate=0, n=2)
    assert summary["historical_regime"] == "stochastic_logging_policy"
    assert summary["arms"]["candidate"]["delta_direct"] == pytest.approx(3.0)
    assert summary["arms"]["candidate"]["delta_dr"] == pytest.approx(2.0)
    caveats = " ".join(summary["caveats"])
    assert "responsible assigning office" in caveats
    assert "initial de jure assignment" in caveats
    assert "de facto handling" in caveats
    assert "treatment provenance is unresolved" in caveats
    assert "treatment-version mechanism" in caveats
    assert "Assign Another ATA" in caveats
    assert "not fields shown on the captured assignment form" in caveats


def test_cluster_bootstrap_se_prices_within_cluster_correlation():
    """Clustering exists to widen the interval when errors move together.

    `district-year` shocks are exactly that: a slow year in one district moves
    every case in it. Treating those rows as independent understates the SE.
    """
    rng = np.random.default_rng(0)
    clusters = np.repeat(np.arange(40), 10)
    shock = rng.normal(scale=3.0, size=40)[clusters]
    scores = shock + rng.normal(scale=0.1, size=400)

    clustered = cluster_bootstrap_se(scores, clusters)
    independent = cluster_bootstrap_se(scores, np.arange(400))
    # Ratio is sqrt(cluster size) = sqrt(10) in expectation, noisy at 40 clusters.
    assert clustered > 2 * independent


def test_cluster_bootstrap_se_is_deterministic_and_needs_two_clusters():
    rng = np.random.default_rng(0)
    scores = rng.normal(size=400)
    clusters = np.repeat(np.arange(4), 100)
    assert cluster_bootstrap_se(scores, clusters) == cluster_bootstrap_se(scores, clusters)
    assert np.isnan(cluster_bootstrap_se(scores, np.zeros(400)))


# --------------------------------------------------------------------------
# Eligibility and the policy
# --------------------------------------------------------------------------


def test_eligible_sets_apply_support_floor_and_top_k():
    df = pd.DataFrame(
        {
            "cell": ["A"] * 26,
            "action_template": (
                ["10::1,5"] * 12
                + ["20::6"] * 10
                + ["10::5"] * 3
                + ["20::7"] * 1
            ),
        }
    )
    eligible = EligibleSets.fit(df, top_k=3, min_support=10)
    assert eligible.candidates("A") == ("10::1,5", "20::6")
    assert eligible.candidates("missing-cell") == ()
    assert set(eligible.universe) == {"10::1,5", "20::6"}


def test_same_chain_in_two_departments_remains_two_actions():
    df = pd.DataFrame(
        {
            "cell": ["A"] * 20,
            "action_template": ["10::1,5"] * 10 + ["20::1,5"] * 10,
        }
    )
    eligible = EligibleSets.fit(df, top_k=3, min_support=10)
    assert eligible.candidates("A") == ("10::1,5", "20::1,5")


class _MuStub:
    """Predicts log1p(days) as a function of chain length only."""

    def predict(self, X):
        return np.log1p(10.0 * X["n_esc"].to_numpy(dtype=float))


class _TradeoffMuStub:
    """Longer chains are faster, so a correctness floor has a visible cost."""

    def predict(self, X):
        days = 30.0 - 10.0 * X["n_esc"].to_numpy(dtype=float)
        return np.log1p(days)


class _NonFiniteMuStub:
    def predict(self, X):
        values = np.zeros(len(X), dtype=float)
        values[0] = np.nan
        return values


class _PiStub:
    """Correctness falls with chain length, so tau bites the long chains."""

    def predict_proba(self, X):
        p = 0.8 / X["n_esc"].to_numpy(dtype=float)
        return np.column_stack([1 - p, p])


def _policy_fixture():
    train = _frame(["Angul"] * 20, ["Water"] * 20, ["100,200"] * 12 + ["100"] * 8)
    decode_flow_columns(train, {"100": "1", "200": "5"})
    train["cell"] = cell_key(train)
    encoder = FeatureEncoder.fit(train)
    eligible = EligibleSets.fit(train, top_k=3, min_support=5)
    return train, encoder, eligible


def test_score_policy_picks_the_row_wise_argmin_over_eligible_flows():
    train, encoder, eligible = _policy_fixture()
    scored = score_policy(
        train,
        encoder=encoder,
        mu_model=_MuStub(),
        eligible=eligible,
        features=encoder.feature_names(),
    )
    # "1" is the one-hop chain, so it minimises the stub's mu everywhere.
    assert set(scored.action) == {"10::1"}
    assert scored.mu.round(6).eq(10.0).all()
    assert (scored.n_eligible == 2).all()


def test_score_policy_respects_the_correctness_floor():
    train, encoder, eligible = _policy_fixture()
    permissive = score_policy(
        train,
        encoder=encoder,
        mu_model=_MuStub(),
        eligible=eligible,
        features=encoder.feature_names(),
        pi_model=_PiStub(),
        tau=0.4,
    )
    assert set(permissive.action) == {"10::1"}  # pi = 0.8 for the one-hop chain

    blocked = score_policy(
        train,
        encoder=encoder,
        mu_model=_MuStub(),
        eligible=eligible,
        features=encoder.feature_names(),
        pi_model=_PiStub(),
        tau=0.9,  # unreachable, so every candidate is refused
    )
    assert (blocked.n_eligible == 0).all()
    assert blocked.fallback.all()
    assert blocked.action.eq("10::1").all()  # highest predicted correctness
    assert np.allclose(blocked.mu, 10.0)
    assert blocked.pi is not None and blocked.pi.eq(0.8).all()


def test_score_policy_frontier_is_total_and_pointwise_monotone():
    train, encoder, eligible = _policy_fixture()
    scores = [
        score_policy(
            train,
            encoder=encoder,
            mu_model=_TradeoffMuStub(),
            eligible=eligible,
            features=encoder.feature_names(),
            pi_model=_PiStub(),
            tau=floor,
        )
        for floor in (0.0, 0.6, 0.9)
    ]

    for row in train.index:
        assert np.allclose([score.mu[row] for score in scores], [10.0, 20.0, 20.0])
        assert np.allclose([score.pi[row] for score in scores], [0.4, 0.8, 0.8])
    assert not scores[0].fallback.any()
    assert not scores[1].fallback.any()
    assert scores[2].fallback.all()
    assert scores[2].pi is not None and np.isfinite(scores[2].pi).all()


def test_score_policy_requires_correctness_model_for_positive_floor():
    train, encoder, eligible = _policy_fixture()
    with pytest.raises(ValueError, match="positive correctness floor requires pi_model"):
        score_policy(
            train,
            encoder=encoder,
            mu_model=_MuStub(),
            eligible=eligible,
            features=encoder.feature_names(),
            tau=0.5,
        )


def test_score_policy_rejects_floor_outside_probability_range():
    train, encoder, eligible = _policy_fixture()
    with pytest.raises(ValueError, match=r"correctness floor tau must lie in \[0, 1\]"):
        score_policy(
            train,
            encoder=encoder,
            mu_model=_MuStub(),
            eligible=eligible,
            features=encoder.feature_names(),
            pi_model=_PiStub(),
            tau=1.5,
        )


def test_score_policy_fails_closed_on_non_finite_predictions():
    train, encoder, eligible = _policy_fixture()
    with pytest.raises(ValueError, match="non-finite log-duration predictions"):
        score_policy(
            train,
            encoder=encoder,
            mu_model=_NonFiniteMuStub(),
            eligible=eligible,
            features=encoder.feature_names(),
        )


def test_score_policy_rejects_rows_outside_the_supported_target_population():
    train, encoder, eligible = _policy_fixture()
    other = _frame(["Puri"], ["Land"], ["100"])
    decode_flow_columns(other, {"100": "1"})
    other["cell"] = cell_key(other)

    with pytest.raises(ValueError, match="no supported policy candidates"):
        score_policy(
            other,
            encoder=encoder,
            mu_model=_MuStub(),
            eligible=eligible,
            features=encoder.feature_names(),
        )


# --------------------------------------------------------------------------
# flow.py: lazy loading and the sentinel handling that used to raise
# --------------------------------------------------------------------------


@pytest.fixture()
def mapping_dir(tmp_path):
    (tmp_path / "t_user_role_details.csv").write_text(
        "intUserId,intRoleId\n100,1\n200,5\n300,6\n"
    )
    (tmp_path / "m_role.csv").write_text(
        "intRoleId,vchRoleName\n1,BDO\n5,Collector\n6,Secretary\n"
    )
    (tmp_path / "m_office_designation_mapping.csv").write_text(
        "intDesignationId,intOfficeId\n1,7\n1,2\n5,3\n"
    )
    flow_mod.load_tables.cache_clear()
    yield tmp_path
    flow_mod.load_tables.cache_clear()


def test_decode_esc_chain_resolves_roles_and_is_deterministic(mapping_dir):
    decoded = flow_mod.decode_esc_chain("100,200", mapping_dir)
    assert decoded.role_ids == ("1", "5")
    assert decoded.role_names == ("BDO", "Collector")
    assert decoded.template == "1,5"
    assert decoded.chain_len == 2
    # Role 1 maps to offices {7, 2}; the pick must not depend on set ordering.
    assert decoded.entry_office == "2"
    assert flow_mod.decode_esc_chain("100,200", mapping_dir).entry_office == "2"


@pytest.mark.parametrize("chain", ["", None, "   ", "100,999"])
def test_decode_esc_chain_returns_none_for_unusable_chains(mapping_dir, chain):
    assert flow_mod.decode_esc_chain(chain, mapping_dir) is None


@pytest.mark.parametrize("sentinel", ["", "-1", "0", None, "nan", "not-an-id", float("nan")])
def test_decode_pending_handles_sentinels_without_raising(mapping_dir, sentinel):
    """The bug: a bare `int(pending_with_id)` raised mid-frame on these."""
    assert flow_mod.decode_pending(sentinel, mapping_dir) is None


@pytest.mark.parametrize("value", [100, "100", 100.0, " 100 "])
def test_decode_pending_accepts_the_id_spellings_the_lake_uses(mapping_dir, value):
    assert flow_mod.decode_pending(value, mapping_dir) == "1"


def test_importing_flow_does_not_read_the_mapping_tables(monkeypatch):
    """Loading must stay lazy: the module used to open CSVs under `data/` at import."""
    opened: list[str] = []
    real_open = flow_mod.open if hasattr(flow_mod, "open") else open

    def _tracking_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _tracking_open)
    importlib = __import__("importlib")
    importlib.reload(flow_mod)
    assert opened == []
    flow_mod.load_tables.cache_clear()


# --------------------------------------------------------------------------
# outcome.py: the three-state S/C map (§2.3.2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remark,expected",
    [
        # The ladder.
        ("the grievance has been disposed", "s1_c0"),
        ("the grievance has been disposed with appropriate action", "s1_c1"),
        ("the grievance has been disposed & beneficiary benefited", "s1_c1"),
        # The case §2.3.2 leads with: correctly closed, scored a failure by the
        # binary label.
        ("duplicate copy", "s0"),
        ("case already taken up for examination", "s0"),
        # The current cell's inability to act is a routing outcome, not proof
        # that no admissible department-and-chain could act.
        ("this is not within the purview of this grievance cell", "unknown"),
        (
            "you are requested to send your grievance/petition directly to vigilance "
            "organisation for redressal of your grievance",
            "unknown",
        ),
        # Worked, but the remark declines to say whether action followed.
        ("as reported", "s1_c_unknown"),
        # Genuinely undecided; must not be guessed either way.
        ("other", "unknown"),
    ],
)
def test_classify_assigns_the_documented_bucket(remark, expected):
    assert outcome.classify(remark) == expected


def test_unrecognised_remark_is_unknown_not_a_default_bucket():
    """The free-text tail is 14.9% of closures. Defaulting it would be evidence."""
    assert outcome.classify("the officer wrote something entirely novel") == "unknown"
    assert outcome.classify(None) == "unknown"


def test_as_reported_never_resolves_to_an_action_claim():
    """Figure B.14 suggested it truncates a benefit template; the corpus refutes that.

    Whatever else changes, this string must not silently become `C=1`: it is
    90,061 closures, 7.45% of resolved, and reading it as a benefit claim would
    move the correctness rate by more than any policy under study.
    """
    s, c = outcome.bucket_to_s_c(outcome.classify("as reported"))
    assert s == 1
    assert c is None


def test_undetermined_states_are_none_and_never_zero():
    """`S=0` means non-actionable. Unknown means unknown. Conflating them would
    silently move the free-text tail into the screened-out population."""
    assert outcome.bucket_to_s_c("unknown") == (None, None)
    assert outcome.bucket_to_s_c("s0") == (0, None)
    assert outcome.bucket_to_s_c("s1_c_unknown") == (1, None)


def test_no_template_is_assigned_twice_with_different_buckets():
    """`_assignments()` merges four dicts; a key collision is a real disagreement."""
    sources = [
        outcome.LADDER,
        outcome.LOCAL_S0_TEMPLATES,
        outcome.LOCAL_S1_TEMPLATES,
        outcome.DEFERRED_TEMPLATES,
    ]
    seen: dict[str, str] = {}
    for mapping in sources:
        for template, assignment in mapping.items():
            assert template not in seen, f"{template!r} assigned twice"
            seen[template] = assignment.bucket
    for family, templates in discards.TEMPLATES.items():
        for template in templates:
            assert template not in seen, f"{template!r} collides with discards.py:{family}"


def test_intrinsic_discard_families_are_proxy_non_actionable():
    """Only families establishing that no admissible route could act are zero."""
    intrinsic_families = set(discards.TEMPLATES) - set(
        outcome.ROUTING_DEPENDENT_DISCARD_FAMILIES
    )
    for family in intrinsic_families:
        templates = discards.TEMPLATES[family]
        for template in templates:
            assert outcome.classify(template) == "s0"


def test_routing_dependent_discard_families_are_unknown():
    """A current-cell rejection cannot define intake-time actionability."""
    for family in outcome.ROUTING_DEPENDENT_DISCARD_FAMILIES:
        for template in discards.TEMPLATES[family]:
            assert outcome.classify(template) == "unknown"


def test_sql_case_and_classify_agree_on_every_mapped_template():
    """The mart builds `s_bucket` in DuckDB and tests run against Python. If the
    two lookups drift, the fitted models and the tests describe different labels."""
    case = outcome.sql_case("remark")
    con = duckdb.connect()
    frame = pd.DataFrame({"remark": list(outcome.ASSIGNMENTS)})
    con.register("frame", frame)
    result = con.execute(f"SELECT remark,\n{case}\nFROM frame").df()
    for row in result.itertuples(index=False):
        assert row.s_bucket == outcome.classify(row.remark)


def test_sql_case_escapes_quotes_in_template_text():
    """One governed template contains a slash and others could contain an
    apostrophe; an unescaped quote is a broken query, not a wrong label."""
    case = outcome.sql_case("remark")
    con = duckdb.connect()
    con.register("frame", pd.DataFrame({"remark": ["it's not within purview"]}))
    result = con.execute(f"SELECT remark,\n{case}\nFROM frame").df()
    assert result["s_bucket"].iloc[0] == "unknown"


# --------------------------------------------------------------------------
# censoring.py: RMST and IPCW (§2.5, Thm C.2)
# --------------------------------------------------------------------------


def _survival_frame(true_days, censor_at, *, horizon=censoring.HORIZON):
    """Apply administrative censoring to known durations."""
    true_days = np.asarray(true_days, dtype=float)
    censor_at = np.asarray(censor_at, dtype=float)
    event = (true_days <= censor_at).astype(int)
    observed = np.where(event == 1, true_days, censor_at)
    return pd.DataFrame(
        {"observed_days": observed, "event": event, "cluster": ["c"] * len(true_days)}
    ), np.minimum(true_days, horizon).mean()


def test_ipcw_recovers_the_truth_that_dropping_censored_rows_misses():
    """The whole reason the module exists.

    Durations are drawn independently of the censoring time, so the completers
    are a speed-selected subsample and their mean is biased low. IPCW reweights
    the survivors to stand for the cases that had not closed yet.
    """
    rng = np.random.default_rng(0)
    n = 20_000
    true_days = rng.exponential(scale=120.0, size=n)
    # Administrative censoring: uniform arrival against a fixed extract date.
    censor_at = rng.uniform(30.0, 400.0, size=n)

    frame, truth = _survival_frame(true_days, censor_at)
    result = censoring.restricted_outcome(frame)
    summary = result.summary()

    assert summary["naive_completer_mean"] < truth - 5.0
    assert summary["rmst"] == pytest.approx(truth, rel=0.05)
    assert abs(summary["rmst"] - truth) < abs(summary["naive_completer_mean"] - truth)


def test_cases_surviving_past_the_horizon_are_fully_observed():
    """`min(T, L)` is known once a case reaches `L`, closed or not. Treating
    those as censored would throw away the rows that pin down the tail."""
    frame = pd.DataFrame(
        {
            "observed_days": [400.0, 500.0],
            "event": [0, 0],  # still open
            "cluster": ["c", "c"],
        }
    )
    result = censoring.restricted_outcome(frame)
    assert list(result.y) == [censoring.HORIZON, censoring.HORIZON]
    assert list(result.weight) == [1.0, 1.0]


def test_cases_censored_before_the_horizon_carry_no_weight():
    frame = pd.DataFrame(
        {"observed_days": [10.0, 50.0, 200.0], "event": [1, 0, 1], "cluster": ["c"] * 3}
    )
    result = censoring.restricted_outcome(frame)
    assert result.weight.iloc[1] == 0.0
    assert (result.weight.iloc[[0, 2]] > 0).all()


def test_censoring_curve_estimates_censoring_not_survival():
    """The flipped indicator. Estimating the outcome curve here inverts every
    weight, and the result still looks plausible, which is why it needs a test."""
    observed = np.array([10.0, 20.0, 30.0, 40.0])
    event = np.array([1, 0, 1, 0])  # two closures, two still open
    grid, survival = censoring.kaplan_meier_censoring(observed, event)
    # A censoring event at t=20 with 3 at risk drops G by a third.
    assert survival[np.searchsorted(grid, 20.0)] == pytest.approx(2.0 / 3.0)
    # The closure at t=10 is not a censoring event and must not move the curve.
    assert survival[np.searchsorted(grid, 10.0)] == pytest.approx(1.0)


def test_fitting_g_on_a_resolved_only_frame_is_a_silent_no_op():
    """Why `fit_frame` exists.

    Legacy `S` is closure proxy `S_tilde`, so every selected row is resolved.
    Estimating the censoring curve there sees no censoring events, returns a
    constant 1, and produces weights that are all exactly 1 -- the correction
    quietly does nothing and the output looks perfectly ordinary.
    """
    rng = np.random.default_rng(11)
    n = 8000
    true_days = rng.exponential(scale=120.0, size=n)
    censor_at = rng.uniform(30.0, 400.0, size=n)
    event = (true_days <= censor_at).astype(int)
    observed = np.where(event == 1, true_days, censor_at)
    cohort = pd.DataFrame(
        {"observed_days": observed, "event": event, "cluster": ["c"] * n}
    )
    resolved = cohort[cohort["event"] == 1]

    # The trap: fit on the filtered frame.
    naive = censoring.restricted_outcome(resolved)
    assert set(np.round(naive.weight.unique(), 9)) == {1.0}

    # The fix: fit on the arrival cohort, apply to the filtered frame.
    corrected = censoring.restricted_outcome(resolved, fit_frame=cohort)
    assert corrected.weight.max() > 1.5

    truth = float(np.minimum(true_days, censoring.HORIZON).mean())
    assert abs(corrected.summary()["rmst"] - truth) < abs(naive.summary()["rmst"] - truth)


def test_fit_frame_missing_the_stratum_column_raises():
    frame = pd.DataFrame({"observed_days": [1.0], "event": [1], "cluster": ["c"]})
    with pytest.raises(ValueError, match="stratum"):
        censoring.restricted_outcome(frame, fit_frame=frame.drop(columns=["cluster"]))



# --------------------------------------------------------------------------
# smear.py: retransformation (Def. 5.3)
# --------------------------------------------------------------------------


def test_smearing_recovers_the_mean_where_naive_exponentiation_finds_the_median():
    """On a lognormal the two targets are far apart and both are known in closed
    form, so this pins the direction of the bias as well as its size."""
    rng = np.random.default_rng(1)
    n = 100_000
    sigma = 0.8
    mu = 3.0
    log_actual = rng.normal(mu, sigma, size=n)
    log_predicted = np.full(n, mu)

    factor = smear.SmearingFactor.fit(log_actual, log_predicted)
    smeared = factor.apply(log_predicted)
    naive = smear.naive_days(log_predicted)

    true_mean = np.exp(mu + sigma**2 / 2) - 1.0
    true_median = np.exp(mu) - 1.0

    assert smeared[0] == pytest.approx(true_mean, rel=0.02)
    assert naive[0] == pytest.approx(true_median, rel=1e-9)
    assert smeared[0] > naive[0]


def test_smearing_factor_is_per_stratum_under_heteroskedasticity():
    """Pooling one factor across strata with different residual spread
    under-corrects the wide one and over-corrects the narrow one."""
    rng = np.random.default_rng(2)
    n = 20_000
    strata = pd.Series(["narrow"] * n + ["wide"] * n)
    log_predicted = np.full(2 * n, 2.0)
    log_actual = np.concatenate(
        [rng.normal(2.0, 0.2, size=n), rng.normal(2.0, 1.2, size=n)]
    )

    factor = smear.SmearingFactor.fit(log_actual, log_predicted, strata=strata)
    assert factor.by_stratum["wide"] > factor.by_stratum["narrow"]
    assert factor.by_stratum["narrow"] == pytest.approx(np.exp(0.2**2 / 2), rel=0.05)
    assert factor.by_stratum["wide"] == pytest.approx(np.exp(1.2**2 / 2), rel=0.05)


def test_thin_strata_fall_back_to_the_pooled_factor():
    """A factor fitted on a handful of residuals is noisier than the bias it
    removes."""
    strata = pd.Series(["big"] * 200 + ["tiny"] * 3)
    log_predicted = np.zeros(203)
    log_actual = np.concatenate([np.full(200, 0.5), np.full(3, 9.0)])
    factor = smear.SmearingFactor.fit(log_actual, log_predicted, strata=strata)
    assert "tiny" not in factor.by_stratum
    applied = factor.apply(np.zeros(203), strata=strata)
    assert applied[-1] == pytest.approx(factor.pooled - 1.0)


# --------------------------------------------------------------------------
# crossfit.py: folds and the winner's curse (§6.1, Lemma F.3)
# --------------------------------------------------------------------------


def test_folds_never_split_a_cluster():
    """Splitting within a district-year leaks the shared shock the clustering
    exists to price, and makes the out-of-fold score look independent when it
    is not."""
    clusters = pd.Series([f"d{i % 40}|2024" for i in range(4000)])
    folds = crossfit.assign_folds(clusters)
    frame = pd.DataFrame({"cluster": clusters, "fold": folds.fold})
    assert (frame.groupby("cluster")["fold"].nunique() == 1).all()


def test_folds_are_balanced_despite_very_unequal_clusters():
    """District-year clusters differ in size by orders of magnitude, and a
    random assignment of a few hundred of them routinely produces folds
    differing by tens of percent -- fold-to-fold variance that reads as
    instability in the estimate. Largest-first placement is the fix."""
    sizes = [400, 260, 180, 150, 120] + [40] * 20 + [7] * 100
    clusters = pd.Series([f"c{i}" for i, n in enumerate(sizes) for _ in range(n)])
    folds = crossfit.assign_folds(clusters, n_folds=5)

    balance = folds.balance()
    mean_load = sum(balance) / len(balance)
    assert max(balance) - min(balance) < 0.1 * mean_load

    # Compare against the naive alternative the docstring rejects.
    rng = np.random.default_rng(7)
    unique = clusters.unique()
    worst = 0.0
    for _ in range(20):
        draw = dict(zip(unique, rng.integers(0, 5, size=len(unique))))
        loads = pd.Series([draw[c] for c in clusters]).value_counts()
        worst = max(worst, (loads.max() - loads.min()) / mean_load)
    assert worst > 0.1


def test_no_single_fold_can_be_balanced_away_from_a_dominant_cluster():
    """The guarantee is only what greedy can deliver: a cluster larger than a
    fold's share must overflow it. Asserting anything tighter would be asserting
    a property the data cannot have."""
    sizes = [1000] + [10] * 50
    clusters = pd.Series([f"c{i}" for i, n in enumerate(sizes) for _ in range(n)])
    folds = crossfit.assign_folds(clusters, n_folds=5)
    balance = folds.balance()
    assert max(balance) >= 1000
    assert max(balance) <= sum(balance) / len(balance) + 1000


def test_fold_assignment_is_deterministic():
    clusters = pd.Series([f"d{i % 17}" for i in range(500)])
    assert list(crossfit.assign_folds(clusters).fold) == list(
        crossfit.assign_folds(clusters).fold
    )


def test_in_fold_scoring_is_optimistic_on_pure_noise_and_out_of_fold_is_not():
    """The winner's curse, isolated. There is no signal, so an argmin over
    candidates should find nothing; scored on the same rows it was chosen on, it
    reliably 'finds' an improvement anyway."""
    rng = np.random.default_rng(3)
    n_candidates, n_rows = 8, 400
    clusters = pd.Series([f"c{i % 40}" for i in range(n_rows)])
    folds = crossfit.assign_folds(clusters, n_folds=4)

    in_fold_gains, out_of_fold_gains = [], []
    for _ in range(60):
        noise = rng.normal(size=(n_rows, n_candidates))
        for k in range(folds.n_folds):
            learn, evaluate = crossfit.split_for_policy(folds, k)
            # Choose the candidate that looks best on the learning rows.
            chosen = int(noise[learn].mean(axis=0).argmin())
            baseline_learn = noise[learn].mean()
            baseline_eval = noise[evaluate].mean()
            in_fold_gains.append(baseline_learn - noise[learn, chosen].mean())
            out_of_fold_gains.append(baseline_eval - noise[evaluate, chosen].mean())

    # The size of the curse is not a guess. Each candidate's learning-fold mean
    # has standard error 1/sqrt(m), and E[min of 8 standard normals] ~ -1.42, so
    # the apparent gain should land near 1.42/sqrt(m).
    m = len(folds.train_rows(0))
    expected = 1.42 / np.sqrt(m)
    assert np.mean(in_fold_gains) == pytest.approx(expected, rel=0.25)
    # Out of fold the chosen candidate is just one of eight arbitrary columns.
    assert abs(np.mean(out_of_fold_gains)) < 0.2 * expected


# --------------------------------------------------------------------------
# tau.py: the speed-correctness frontier (Cor. 4.6, §6.2)
# --------------------------------------------------------------------------


def _stub_frontier(tau_value):
    """Direct scores are monotone while AIPW corrections may fluctuate."""
    return (
        40.0 + 30.0 * tau_value,
        44.0 - tau_value,
        0.25 + 0.30 * tau_value,
        0.35 - 0.05 * tau_value,
        int(100 * tau_value),
        3.0,
    )


def test_frontier_is_monotone_in_tau():
    points = tau.sweep(_stub_frontier, historical_correct=0.30)
    report = tau.monotonicity_report(points)
    assert report == {
        "duration_dm_nondecreasing": True,
        "duration_aipw_nondecreasing": False,
        "correct_dm_nondecreasing": True,
        "correct_aipw_nondecreasing": False,
    }


def test_smallest_feasible_tau_is_the_smallest_not_the_safest():
    """Any floor above `tau*` buys correctness the constraint did not ask for
    and pays for it in days."""
    points = tau.sweep(_stub_frontier, historical_correct=0.30)
    best = tau.smallest_feasible(points, estimator="dm")
    assert best is not None
    assert best.v_correct_dm >= 0.30
    infeasible = [p for p in points if p.tau < best.tau]
    assert all(not p.feasible_dm for p in infeasible)

    augmented = tau.smallest_feasible(points, estimator="aipw")
    assert augmented is not None
    assert augmented.tau == 0.0
    assert augmented.tau != best.tau


def test_no_feasible_tau_returns_none_rather_than_the_largest():
    """Reporting the top of the grid as optimal would present an infeasible
    policy as the answer."""
    points = tau.sweep(_stub_frontier, historical_correct=0.99)
    assert tau.smallest_feasible(points, estimator="dm") is None
    assert tau.smallest_feasible(points, estimator="aipw") is None


def test_calibration_report_prices_a_miscalibrated_classifier():
    """`tau` is only interpretable if `pi` is calibrated near it."""
    rng = np.random.default_rng(4)
    actual = rng.binomial(1, 0.3, size=10_000)
    honest = np.where(actual == 1, 0.3, 0.3)
    overconfident = np.clip(honest * 2.5, 0, 1)
    assert (
        tau.calibration_report(honest, actual)["expected_calibration_error"]
        < tau.calibration_report(overconfident, actual)["expected_calibration_error"]
    )


def test_calibration_report_counts_probability_one():
    """Codex P2 on PR #264. Isotonic regression routinely emits exactly 1.0, and
    a half-open top bin drops those rows from the counts and the error -- in the
    direction that flatters the model, since a confident prediction is where
    miscalibration costs most."""
    probability = np.array([0.05, 0.5, 1.0, 1.0, 1.0])
    actual = np.array([0.0, 1.0, 0.0, 0.0, 0.0])
    report = tau.calibration_report(probability, actual)
    assert sum(b["n"] for b in report["bins"]) == len(probability)
    # Three of five rows predict 1.0 and are all wrong, so the error cannot be
    # the 0.12 the half-open version reported.
    assert report["expected_calibration_error"] > 0.5


def test_calibration_bins_partition_the_unit_interval():
    rng = np.random.default_rng(5)
    probability = np.clip(rng.beta(0.4, 0.4, size=5000), 0.0, 1.0)
    probability[:200] = 1.0
    probability[200:400] = 0.0
    actual = rng.binomial(1, 0.4, size=5000).astype(float)
    report = tau.calibration_report(probability, actual)
    assert sum(b["n"] for b in report["bins"]) == len(probability)
