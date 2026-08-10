"""Build a deterministic, PII-redacted actionability adjudication sample.

This script is intended to run on DPIC-controlled infrastructure. It reads the
named redacted complaint column and exact administrative templates only. The
output contains no ticket number, raw grievance, officer remark, person field,
or office field. Sampling strata are opaque so adjudicators remain blind to the
administrative weak label used only to improve class coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Sequence


SHAPED_PII = re.compile(
    r"(?:\b\d{10}\b|\b\d{4}[ -]?\d{4}[ -]?\d{4}\b|"
    r"\b[A-Z]{5}\d{4}[A-Z]\b|\b[^\s@]+@[^\s@]+\.[^\s@]+\b)",
    re.IGNORECASE,
)

FAMILY_TO_STRATUM = {
    "details_inadequate": "s1",
    "documents_not_attached": "s1",
    "address_not_given": "s1",
    "no_specific_grievance": "s2",
    "outside_grievance_cell_purview": "s3",
    "policy_decision_required": "s4",
}

SPLIT_FOR_YEAR = {
    2021: "train",
    2022: "train",
    2023: "train",
    2024: "validation",
    2025: "test",
}

_NORMALIZED_REMARK_SQL = (
    "regexp_replace(regexp_replace(lower(trim(action_taken_remark)), "
    "'\\s+', ' ', 'g'), '\\.$', '')"
)


def _identifier(ticket_no: str, *, salt: str) -> str:
    return hashlib.sha256(f"{salt}\0{ticket_no}".encode()).hexdigest()


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _private_salt(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
    if len(value) < 32:
        raise ValueError("identifier salt file is invalid")
    return value


def _split_for_item(
    *, ticket_no: str, year: int, seed: str, chronological: bool
) -> str | None:
    if chronological:
        return SPLIT_FOR_YEAR.get(year)
    bucket = int.from_bytes(
        hashlib.sha256(f"{seed}\0split\0{ticket_no}".encode()).digest()[:8],
        "big",
    ) % 10
    if bucket < 6:
        return "train"
    if bucket < 8:
        return "validation"
    return "test"


def _load_parquet_rows(
    complaints_path: Path,
    action_history_path: Path,
    template_rows: Sequence[tuple[str, str]],
) -> list[tuple[str, int, str, str | None]]:
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TEMP TABLE discard_template(family VARCHAR, template VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO discard_template VALUES (?, ?)", template_rows
        )
        connection.from_parquet(str(complaints_path)).create_view("complaints")
        connection.from_parquet(str(action_history_path)).create_view("action_history")
        return connection.execute(
            """
            WITH normalized_action AS (
                SELECT
                    ticket_no,
                    regexp_replace(
                        regexp_replace(lower(trim(action_taken_remark)), '\\s+', ' ', 'g'),
                        '\\.$', ''
                    ) AS normalized_remark
                FROM action_history
                WHERE action_taken_remark IS NOT NULL
            ), matched_family AS (
                SELECT DISTINCT a.ticket_no, d.family AS label_family
                FROM normalized_action a
                INNER JOIN discard_template d ON d.template = a.normalized_remark
            ), ticket_family AS (
                SELECT
                    ticket_no,
                    count(*) AS family_count,
                    min(label_family) AS label_family
                FROM matched_family
                GROUP BY ticket_no
            )
            SELECT
                cast(c.ticket_no AS VARCHAR) ticket_no,
                cast(c.created_year AS INTEGER) created_year,
                cast(c.grievance_redacted AS VARCHAR) redacted_text,
                CASE
                    WHEN tf.family_count = 1 THEN tf.label_family
                    ELSE NULL
                END AS label_family
            FROM complaints c
            LEFT JOIN ticket_family tf USING (ticket_no)
            WHERE c.created_year BETWEEN 2021 AND 2025
              AND c.grievance_redacted IS NOT NULL
              AND trim(c.grievance_redacted) <> ''
            """
        ).fetchall()
    finally:
        connection.close()


async def _load_oltp_rows_async(
    database_url: str,
    template_rows: Sequence[tuple[str, str]],
) -> list[tuple[str, int, str, str | None]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    values = []
    parameters: dict[str, str] = {}
    for index, (family, template) in enumerate(template_rows):
        values.append(f"(:family_{index}, :template_{index})")
        parameters[f"family_{index}"] = family
        parameters[f"template_{index}"] = template
    query = text(
        f"""
        WITH discard_template(family_name, template) AS (
            VALUES {", ".join(values)}
        ), normalized_action AS (
            SELECT ticket_no, {_NORMALIZED_REMARK_SQL} AS normalized_remark
            FROM action_history
            WHERE action_taken_remark IS NOT NULL
        ), matched_family AS (
            SELECT DISTINCT a.ticket_no, d.family_name AS label_family
            FROM normalized_action a
            INNER JOIN discard_template d ON d.template = a.normalized_remark
        ), ticket_family AS (
            SELECT
                ticket_no,
                count(*) AS family_count,
                min(label_family) AS label_family
            FROM matched_family
            GROUP BY ticket_no
        )
        SELECT
            c.ticket_no::text,
            extract(year from c.created_on)::integer AS created_year,
            g.grievance_redacted,
            CASE
                WHEN tf.family_count = 1 THEN tf.label_family
                ELSE NULL
            END AS label_family
        FROM complaints c
        INNER JOIN grievance_redactions g USING (ticket_no)
        LEFT JOIN ticket_family tf USING (ticket_no)
        WHERE g.grievance_redacted IS NOT NULL
          AND trim(g.grievance_redacted) <> ''
        """
    )
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(query, parameters)
            return [tuple(row) for row in result.fetchall()]
    finally:
        await engine.dispose()


def _load_oltp_rows(
    env_file: Path,
    template_rows: Sequence[tuple[str, str]],
) -> list[tuple[str, int, str, str | None]]:
    from dotenv import dotenv_values

    database_url = dotenv_values(env_file).get("OLTP_DB_URL")
    if not database_url:
        raise ValueError("OLTP_DB_URL is missing from the supplied env file")
    return asyncio.run(_load_oltp_rows_async(str(database_url), template_rows))


def build_sample(
    complaints_path: Path | None,
    action_history_path: Path | None,
    oltp_env_file: Path | None,
    output_path: Path,
    manifest_path: Path,
    *,
    per_stratum_split: int,
    unlabeled_per_split: int,
    seed: str,
    id_salt: str,
) -> None:
    parquet_mode = complaints_path is not None or action_history_path is not None
    if parquet_mode == (oltp_env_file is not None):
        raise ValueError("choose either both Parquet inputs or one OLTP env file")
    if parquet_mode:
        if complaints_path is None or not complaints_path.is_file():
            raise FileNotFoundError(complaints_path)
        if action_history_path is None or not action_history_path.is_file():
            raise FileNotFoundError(action_history_path)
    elif oltp_env_file is None or not oltp_env_file.is_file():
        raise FileNotFoundError(oltp_env_file)
    if per_stratum_split < 1 or unlabeled_per_split < 1:
        raise ValueError("sample sizes must be positive")
    if not seed or not id_salt:
        raise ValueError("seed and id salt must be non-empty")

    from janasunani.analytics.findings.discards import TEMPLATES

    template_rows = [
        (family, template)
        for family, templates in TEMPLATES.items()
        if family in FAMILY_TO_STRATUM
        for template in templates
    ]
    rows = (
        _load_parquet_rows(complaints_path, action_history_path, template_rows)
        if parquet_mode and complaints_path is not None and action_history_path is not None
        else _load_oltp_rows(oltp_env_file, template_rows)  # type: ignore[arg-type]
    )
    observed_years = {int(row[1]) for row in rows}
    chronological = len(observed_years) >= 3 and {2024, 2025}.issubset(observed_years)

    candidates: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    excluded_shaped_pii = 0
    for ticket_no, year, raw_text, family in rows:
        split = _split_for_item(
            ticket_no=str(ticket_no),
            year=int(year),
            seed=seed,
            chronological=chronological,
        )
        if split is None:
            continue
        text = _normalized_text(str(raw_text))
        if SHAPED_PII.search(text):
            excluded_shaped_pii += 1
            continue
        stratum = FAMILY_TO_STRATUM.get(str(family), "s5")
        candidates.setdefault((split, stratum), []).append(
            (str(ticket_no), int(year), text)
        )

    selected: list[dict[str, object]] = []
    for split in ("train", "validation", "test"):
        for stratum in ("s1", "s2", "s3", "s4", "s5"):
            target = unlabeled_per_split if stratum == "s5" else per_stratum_split
            pool = candidates.get((split, stratum), [])
            pool.sort(
                key=lambda row: hashlib.sha256(
                    f"{seed}\0{split}\0{stratum}\0{row[0]}".encode()
                ).digest()
            )
            if len(pool) < target:
                raise ValueError(
                    f"insufficient {split}/{stratum} candidates: {len(pool)} < {target}"
                )
            for ticket_no, year, text in pool[:target]:
                item_id = _identifier(ticket_no, salt=id_salt)
                selected.append(
                    {
                        "item_id": item_id,
                        "group_id": item_id,
                        "redacted_text": text,
                        "created_year": year,
                        "split": split,
                        "language": "unknown_pending_adjudication",
                        "sampling_stratum": stratum,
                    }
                )

    selected.sort(key=lambda row: (str(row["split"]), str(row["item_id"])))
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in selected
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    output_path.chmod(0o600)
    fingerprint = "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()
    counts: dict[str, int] = {}
    for row in selected:
        key = f"{row['split']}/{row['sampling_stratum']}"
        counts[key] = counts.get(key, 0) + 1
    manifest = {
        "schema_version": "actionability-adjudication-sample-v1",
        "dataset_fingerprint": fingerprint,
        "records": len(selected),
        "counts": dict(sorted(counts.items())),
        "parameters": {
            "per_weak_stratum_split": per_stratum_split,
            "unlabeled_per_split": unlabeled_per_split,
            "seed": seed,
            "split_policy": (
                "chronological_2021_2023_train_2024_validation_2025_test"
                if chronological
                else "single_snapshot_hash_60_20_20_development_only"
            ),
            "ticket_identifier": "salted_sha256_not_reversible",
            "shaped_pii_excluded": excluded_shaped_pii,
            "adjudicator_blinding": "sampling strata are opaque s1-s5",
        },
        "sample_design": {
            "sampling_scheme": "fixed quotas across opaque sampling strata",
            "production_prevalence_representative": False,
            "metric_interpretation": (
                "accuracy, precision, PPV, and review workload measured on this "
                "sample are composition-specific and are not production prevalence"
            ),
            "intended_use": "development model comparison and error analysis",
        },
        "selected_fields": [
            "salted item/group id",
            "grievance_redacted",
            "created_year",
            "split",
            "opaque sampling stratum",
        ],
        "forbidden_fields": [
            "ticket_no",
            "raw grievance",
            "officer remark",
            "petitioner identifiers",
            "office",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complaints", type=Path)
    parser.add_argument("--action-history", type=Path)
    parser.add_argument("--oltp-env-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--per-stratum-split", type=int, default=15)
    parser.add_argument("--unlabeled-per-split", type=int, default=40)
    parser.add_argument("--seed", default="actionability-gold-v1")
    parser.add_argument("--id-salt-file", type=Path, required=True)
    args = parser.parse_args()
    build_sample(
        args.complaints,
        args.action_history,
        args.oltp_env_file,
        args.output,
        args.manifest,
        per_stratum_split=args.per_stratum_split,
        unlabeled_per_split=args.unlabeled_per_split,
        seed=args.seed,
        id_salt=_private_salt(args.id_salt_file),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
