"""E0/E1: flow census and descriptive audit.

E0 decodes every `vchAllEscUser` chain to a role sequence and tabulates chain
lengths, entry/last roles and templates. E1 cross-tabulates `T | C` against the
chain length and shows how far the citizen-selected `office` diverges from
where the file actually sits.

The mart is built once via `dataset.build_mart` rather than by re-pasting the
ladder CASE for each cross-tab, which is what the first version did three times
over.
"""

from __future__ import annotations

import collections

import duckdb
import pandas as pd

from . import paths
from .dataset import build_mart
from .features import encode_action
from .flow import load_tables


def flow_census(df: pd.DataFrame, tables) -> dict:
    """Decode rate and the chain-shape distributions.

    A case with no chain at all is `absent`, not a decode failure: the decode
    rate is over chains that exist. Collapsing the two understates it -- 26,380
    cases carry an empty-string chain, and counting those as failures turns a
    100% decode into 98.1%.
    """
    chain_lengths: collections.Counter = collections.Counter()
    entry_roles: collections.Counter = collections.Counter()
    last_roles: collections.Counter = collections.Counter()
    templates: collections.Counter = collections.Counter()
    joint_actions: collections.Counter = collections.Counter()
    departments_by_template: dict[str, set[str]] = collections.defaultdict(set)
    absent = decoded = failed = 0

    for chain, department_id in zip(df["all_esc_user"], df["dept_id"]):
        if chain is None or not str(chain).strip():
            absent += 1
            continue
        tokens = [t.strip() for t in str(chain).split(",") if t.strip()]
        roles = [tables.user_role.get(t) for t in tokens]
        if any(r is None for r in roles):
            failed += 1
            continue
        decoded += 1
        chain_lengths[len(roles)] += 1
        entry_roles[tables.role_name.get(roles[0], "?")] += 1
        last_roles[tables.role_name.get(roles[-1], "?")] += 1
        template = ",".join(roles)
        templates[template] += 1
        action = encode_action(department_id, template)
        if action is not None:
            joint_actions[action] += 1
            departments_by_template[template].add(str(department_id))

    present = decoded + failed
    multidepartment_templates = {
        template for template, departments in departments_by_template.items()
        if len(departments) > 1
    }
    return {
        "absent": absent,
        "decoded": decoded,
        "failed": failed,
        "decode_rate": decoded / present if present else float("nan"),
        "chain_lengths": dict(sorted(chain_lengths.items())),
        "top_entry_roles": entry_roles.most_common(10),
        "top_last_roles": last_roles.most_common(10),
        "top_templates": templates.most_common(15),
        "n_departments": int(df["dept_id"].nunique(dropna=True)),
        "n_templates": len(templates),
        "n_joint_actions": len(joint_actions),
        "n_multidepartment_templates": len(multidepartment_templates),
        "rows_on_multidepartment_templates": sum(
            templates[template] for template in multidepartment_templates
        ),
        "top_joint_actions": joint_actions.most_common(15),
    }


def office_divergence(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Citizen-selected `office` vs where the file is actually pending."""
    return con.execute(
        f"""
        SELECT office,
               COUNT(*) AS total,
               SUM(CASE WHEN pending_with LIKE 'Collector%' THEN 1 ELSE 0 END)
                   AS pending_collector
        FROM read_parquet('{paths.COMPLAINTS_PARQUET}')
        GROUP BY office
        ORDER BY total DESC
        """
    ).df()


def main() -> int:
    tables = load_tables()
    df = build_mart()
    resolved = df[df["resolved_on"].notna()]

    census = flow_census(df, tables)
    print(f"E0 decode rate {census['decode_rate']:.4f} "
          f"({census['decoded']} ok / {census['failed']} failed / "
          f"{census['absent']} absent)")
    print("chain lengths", census["chain_lengths"])
    print("top entry roles", census["top_entry_roles"][:10])
    print("top last roles", census["top_last_roles"][:10])
    print("top templates", census["top_templates"][:15])
    print(
        "joint action audit",
        {
            key: census[key]
            for key in (
                "n_departments",
                "n_templates",
                "n_joint_actions",
                "n_multidepartment_templates",
                "rows_on_multidepartment_templates",
            )
        },
    )
    print("top joint actions", census["top_joint_actions"][:15])

    print("\nE0 office vs pending")
    print(office_divergence(duckdb.connect()).head(10).to_string(index=False))

    print("\nE1 disposal ladder")
    print(
        resolved.groupby("rung")["days"]
        .agg(["count", "median", "mean"])
        .sort_values("count", ascending=False)
        .to_string()
    )

    print("\nE1 T by correctness")
    print(resolved.groupby("correct")["days"].agg(["count", "median", "mean"]).to_string())

    print("\nE1 T by chain length, within correct")
    print(
        resolved[resolved["correct"] == 1]
        .groupby("n_esc")["days"]
        .agg(["count", "median", "mean"])
        .to_string()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
