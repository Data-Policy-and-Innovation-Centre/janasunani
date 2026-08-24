"""Build the outcome mart: three-state S/C, duration, censoring, splits.

The disposal-ladder CASE was copy-pasted four times across the first run (once
here, three times in `e0_flow_census.py`), so the definition of `correct` could
drift between the census and the mart without anything failing. It lives here
once, as `LADDER_SQL`, and the census imports it.

TWO OUTCOME DEFINITIONS LIVE HERE, AND ONLY ONE IS CORRECT
-----------------------------------------------------------
`correct` is the original binary label: action rung, or the benefit flag set on
any rung. It is retained because the fitted models and the superseded runs used
it, and dropping it would silently change what old artifacts mean. It is
**wrong** in the way §2.3.2 of the design document describes: it scores the
correct closure of a duplicate as a failure, and does so disproportionately
among fast cases.

`s_bucket`, `S` and `C` are the closure-derived replacement from `outcome.py`.
The legacy column `S` is the post-resolution proxy `S_tilde`, not latent
intake-time actionability `S*`; it therefore does not license conditioning for
the causal target. `C` is an outcome and may only be constrained. Both are NULL
where the closing remark does not determine them; the NULLs are the honest
answer and must not be filled with zeros.

CENSORING IS NO LONGER DROPPED AT THIS STAGE
---------------------------------------------
The previous version gave unresolved cases `days_capped = 365` and wrote only
the resolved subsets, so censoring could not be modelled downstream. That is
tolerable for 2021--23 (2--4% censored) and val 2024 (9.2%); it is not tolerable
for test 2025 (34.4%), where the resolved rows are selected on having been fast
enough to close before the snapshot.

This build now emits `observed_days`, `event` and `censor_days` against an
explicit `snapshot` date, which is what `censoring.py` needs to form the
restricted mean with inverse-probability-of-censoring weights. The per-split
censoring rate is still written to `censoring.json` so summaries have to carry
it.
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd

from . import outcome, paths

#: Exact-match disposal ladder. Mirrors `analytics/sql/closure.sql`.
LADDER_SQL = """
    CASE regexp_replace(
             trim(regexp_replace(lower(coalesce(c.action_taken_remark, '')), '\\s+', ' ', 'g')),
             '\\.+$', '', 'g')
        WHEN 'the grievance has been disposed' THEN 'bare'
        WHEN 'the grievance has been resolved' THEN 'bare'
        WHEN 'the grievance has been disposed with appropriate action' THEN 'with_action'
        WHEN 'the grievance has been resolved with appropriate action' THEN 'with_action'
        WHEN 'the grievance has been disposed & beneficiary benefited' THEN 'benefit'
        WHEN 'the grievance has been resolved & beneficiary benefited' THEN 'benefit'
        ELSE 'off_ladder'
    END AS rung
"""

#: Last action-history remark at or before resolution, per ticket.
CLOSING_SQL = """
    ranked AS (
        SELECT a.ticket_no, a.action_taken_remark,
               ROW_NUMBER() OVER (
                   PARTITION BY a.ticket_no
                   ORDER BY a.action_taken_date DESC, a.id DESC
               ) AS rn
        FROM read_parquet('{action_history}') a
        JOIN (
            SELECT ticket_no, resolved_on FROM base WHERE resolved_on IS NOT NULL
        ) r ON r.ticket_no = a.ticket_no
        WHERE a.action_taken_date IS NOT NULL
          AND CAST(a.action_taken_date AS DATE) <= CAST(r.resolved_on AS DATE)
    ),
    closing AS (SELECT ticket_no, action_taken_remark FROM ranked WHERE rn = 1)
"""

#: Normalisation shared by the ladder, `outcome.ASSIGNMENTS` and
#: `analytics/findings/discards.py`: lowercase, collapse whitespace, strip
#: trailing full stops. Every exact-match lookup keys on this and nothing else.
NORMALIZED_REMARK_SQL = (
    r"regexp_replace(trim(regexp_replace(lower(coalesce(c.action_taken_remark, '')),"
    r" '\s+', ' ', 'g')), '\.+$', '', 'g')"
)

#: Extract date. Administrative censoring is a deterministic function of arrival
#: and this date (§2.5), which is what makes the IPCW assumption credible rather
#: than hopeful. Derived rather than hard-coded so a refreshed lake moves it.
#:
#: The last *resolution* is not a safe proxy on its own. A quiet period with no
#: closures, or an extract carrying complaints created after the final closure,
#: puts arrivals past the supposed cutoff and yields negative `censor_days` --
#: which then feed the censoring events and the IPCW weights. Taking the later
#: of the two maxima is still an inference rather than provenance, so
#: `build_mart` asserts the result is not violated instead of trusting it. On
#: the 30 July 2025 extract both maxima are 2025-07-30 and no open complaint
#: post-dates the last resolution, so the two definitions agree today.
SNAPSHOT_SQL = """
    snapshot AS (
        SELECT greatest(
                   max(CAST(resolved_on AS DATE)),
                   max(CAST(created_on AS DATE))
               ) AS as_of
        FROM read_parquet('{complaints}')
    )
"""

MART_SQL = f"""
WITH base AS (
    SELECT ticket_no, created_on, resolved_on, benefitted, category, subcategory,
           district, block, state, mode, mode_id, office, office_id, dept, dept_id,
           pending_with_id, pending_with, all_esc_user, transfer_status,
           self_assign, created_year, assigned_on, escalation_date
    FROM read_parquet('{{complaints}}')
),
{CLOSING_SQL},
{SNAPSHOT_SQL},
classified AS (
    SELECT b.*,
           DATE_DIFF('day', b.created_on, b.resolved_on) AS days,
           length(b.all_esc_user) - length(replace(b.all_esc_user, ',', '')) + 1 AS n_esc,
           {NORMALIZED_REMARK_SQL} AS normalized_remark,
           {LADDER_SQL},
           s.as_of AS snapshot_date
    FROM base b
    LEFT JOIN closing c ON c.ticket_no = b.ticket_no
    CROSS JOIN snapshot s
),
bucketed AS (
    SELECT *,
{{s_bucket_case}}
    FROM classified
)
SELECT *,
       -- Retained, and wrong. See the module docstring.
       CASE WHEN rung IN ('with_action', 'benefit')
                 OR lower(coalesce(benefitted, '')) LIKE '%yes%'
            THEN 1 ELSE 0 END AS correct,
       -- Three-state. NULL means undetermined and never zero.
       CASE WHEN s_bucket = 'unknown' THEN NULL
            WHEN s_bucket = 's0' THEN 0
            ELSE 1 END AS S,
       CASE WHEN s_bucket = 's1_c1' THEN 1
            WHEN s_bucket = 's1_c0' THEN 0
            ELSE NULL END AS C,
       -- Duration and administrative censoring.
       CASE WHEN resolved_on IS NULL THEN 1 ELSE 0 END AS censored,
       CASE WHEN resolved_on IS NULL THEN 0 ELSE 1 END AS event,
       DATE_DIFF('day', created_on, snapshot_date) AS censor_days,
       coalesce(
           DATE_DIFF('day', created_on, resolved_on),
           DATE_DIFF('day', created_on, snapshot_date)
       ) AS observed_days,
       LEAST(coalesce(DATE_DIFF('day', created_on, resolved_on), 365), 365) AS days_capped
FROM bucketed
"""

#: Chronological splits. Group-disjoint on ticket_no by construction.
SPLIT_YEARS = {"train": (2021, 2022, 2023), "val": (2024,), "test": (2025,)}


def build_mart(con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    con = con or duckdb.connect()
    query = MART_SQL.format(
        complaints=paths.COMPLAINTS_PARQUET,
        action_history=paths.ACTION_HISTORY_PARQUET,
        # Generated from `outcome.ASSIGNMENTS` rather than written out, so the
        # mart and `outcome.classify` cannot disagree.
        s_bucket_case=outcome.sql_case("normalized_remark"),
    )
    df = con.execute(query).df()

    # A negative duration means the derived snapshot is not the observation
    # cutoff, and every censoring event and IPCW weight downstream is then
    # computed against a date that never happened. Fail loudly rather than
    # propagate it: the fix is extract provenance, not a clamp.
    negative = int((df["censor_days"] < 0).sum())
    if negative:
        raise ValueError(
            f"{negative:,} rows have negative censor_days against the derived "
            f"snapshot {df['snapshot_date'].iloc[0]}. Supply the extract's real "
            "cutoff date instead of deriving one from the data."
        )

    df["created_on"] = pd.to_datetime(df["created_on"], errors="coerce")
    parsed_year = df["created_on"].dt.year.astype("Int64")
    fallback_year = pd.to_numeric(df["created_year"], errors="coerce").astype("Int64")
    df["year"] = parsed_year.fillna(fallback_year)
    return df


def main() -> int:
    df = build_mart()
    print(f"mart rows {len(df)}")

    censoring: dict[str, float] = {}
    for name, years in SPLIT_YEARS.items():
        split = df[df["year"].isin(years)].copy()
        resolved = split[split["resolved_on"].notna()]
        censoring[name] = float(split["censored"].mean()) if len(split) else float("nan")

        split.to_parquet(paths.out(f"{name}_all.parquet"))
        resolved.to_parquet(paths.out(f"{name}_resolved.parquet"))
        resolved[resolved["correct"] == 1].to_parquet(paths.out(f"{name}_correct.parquet"))
        # The evaluation population for the three-state design.
        #
        # It is written from `split` rather than `resolved`, but that buys
        # nothing on its own and the comment here used to claim otherwise:
        # legacy `S` is S_tilde, read off the closing remark, and `CLOSING_SQL` only joins
        # tickets with a non-null `resolved_on`, so an unresolved grievance has
        # no remark, lands in `unknown`, and is dropped by the `S == 1` filter.
        # **Every row in this file is resolved**, and IPCW cannot recover rows
        # removed before weighting.
        #
        # Two consequences, both real. The estimand is conditioned on
        # resolution, so `censoring.py` corrects for differential speed *among
        # cases that closed* rather than restoring the full arrival cohort. And
        # a case still open past the horizon is discarded even though its
        # restricted duration `min(T, 365)` is already known exactly -- an
        # informative row thrown away for want of a label.
        #
        # Closing this properly needs intake-time S* adjudicated or predicted
        # from pre-treatment inputs. Nothing currently bridges that latent
        # quantity and S_tilde. Recorded as unbuilt, not silently absorbed.
        split[split["S"] == 1].to_parquet(paths.out(f"{name}_actionable.parquet"))

        buckets = split["s_bucket"].value_counts()
        print(
            f"{name}: n={len(split)} resolved={len(resolved)} "
            f"correct={(resolved['correct'] == 1).sum()} censored={censoring[name]:.3f}"
        )
        print(
            f"       S=1 {int((split['S'] == 1).sum()):,}  "
            f"S=0 {int((split['S'] == 0).sum()):,}  "
            f"S undetermined {int(split['S'].isna().sum()):,}  |  "
            f"C=1 {int((split['C'] == 1).sum()):,}  "
            f"C=0 {int((split['C'] == 0).sum()):,}  "
            f"C undetermined among S=1 "
            f"{int(((split['S'] == 1) & split['C'].isna()).sum()):,}"
        )
        print(f"       buckets {buckets.to_dict()}")

    with open(paths.out("censoring.json"), "w") as handle:
        json.dump(censoring, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
