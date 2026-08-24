"""Build a private, redacted chronological categorization benchmark.

The command reads the OLTP redaction side table, never ``complaints.grievance``.
It keeps one representative per exact normalized-text group, excludes groups
with conflicting administrative labels, and assigns a group to the period in
which it first appeared. The JSONL output contains redacted narrative and must
remain in private DVC storage; the provenance sidecar is aggregate-only.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Sequence

from janasunani.evaluation.categorization import (
    EXPECTED_GROUP_POLICY,
    EXPECTED_SPLIT_POLICY,
    PROVENANCE_SCHEMA_VERSION,
)


_SHAPED_PII = re.compile(
    r"(?<!\d)(?:[6-9]\d{9}|\d{12})(?!\d)"
    r"|\b[A-Z]{5}\d{4}[A-Z]\b"
    r"|\b[A-Z0-9._%+-]+@(?!gov\.in\b)[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    return " ".join(text.split()).strip()


def _opaque(value: str, *, salt: str, kind: str) -> str:
    return hashlib.sha256(f"{salt}\0{kind}\0{value}".encode()).hexdigest()


def _split(created_on: datetime) -> str:
    if created_on.month <= 6:
        return "train"
    if created_on.month <= 9:
        return "validation"
    return "test"


def prepare_records(
    rows: Iterable[tuple[str, datetime, str, str]],
    *,
    salt: str,
    min_support_per_split: int,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    """Return group-disjoint records and aggregate preparation evidence."""

    if not salt:
        raise ValueError("salt must be non-empty")
    if min_support_per_split < 1:
        raise ValueError("min_support_per_split must be positive")
    groups: dict[str, list[tuple[str, datetime, str, str]]] = defaultdict(list)
    excluded_shaped_pii = 0
    input_rows = 0
    for ticket_no, created_on, redacted_text, category in rows:
        input_rows += 1
        text = _normalized(str(redacted_text))
        label = _normalized(str(category))
        if not text or not label:
            continue
        if _SHAPED_PII.search(text):
            excluded_shaped_pii += 1
            continue
        group_key = _opaque(text.lower(), salt=salt, kind="normalized-text")
        groups[group_key].append((str(ticket_no), created_on, text, label))

    conflict_groups = 0
    candidates: list[dict[str, str]] = []
    for group_id, members in groups.items():
        labels = {member[3] for member in members}
        if len(labels) != 1:
            conflict_groups += 1
            continue
        first = min(
            members,
            key=lambda member: (
                member[1],
                _opaque(member[0], salt=salt, kind="ticket-order"),
            ),
        )
        candidates.append(
            {
                "item_id": _opaque(first[0], salt=salt, kind="item"),
                "group_id": group_id,
                "redacted_text": first[2],
                "category": first[3],
                "split": _split(first[1]),
                "language": "unknown_not_adjudicated",
                "source_kind": "historical_typed_redacted",
            }
        )

    support: dict[str, Counter[str]] = defaultdict(Counter)
    for row in candidates:
        support[row["category"]][row["split"]] += 1
    eligible_categories = sorted(
        category
        for category, counts in support.items()
        if all(
            counts[split] >= min_support_per_split
            for split in ("train", "validation", "test")
        )
    )
    eligible = set(eligible_categories)
    selected = [row for row in candidates if row["category"] in eligible]
    selected.sort(key=lambda row: (row["split"], row["item_id"]))
    evidence: dict[str, object] = {
        "input_rows": input_rows,
        "exact_text_groups": len(groups),
        "conflicting_label_groups_excluded": conflict_groups,
        "shaped_pii_rows_excluded": excluded_shaped_pii,
        "eligible_categories": eligible_categories,
        "excluded_categories": sorted(set(support) - eligible),
        "split_counts": dict(sorted(Counter(row["split"] for row in selected).items())),
        "category_counts": dict(
            sorted(Counter(row["category"] for row in selected).items())
        ),
    }
    return selected, evidence


async def _load_rows(
    env_file: Path, *, year: int
) -> list[tuple[str, datetime, str, str]]:
    from dotenv import dotenv_values
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = dotenv_values(env_file).get("OLTP_DB_URL")
    if not database_url:
        raise ValueError("OLTP_DB_URL is missing from the supplied env file")
    engine = create_async_engine(str(database_url))
    query = text(
        """
        SELECT c.ticket_no::text, c.created_on, g.grievance_redacted, c.category
        FROM complaints c
        INNER JOIN grievance_redactions g USING (ticket_no)
        WHERE date_part('year', c.created_on) = :year
          AND g.grievance_redacted IS NOT NULL
          AND trim(g.grievance_redacted) <> ''
          AND c.category IS NOT NULL
          AND trim(c.category) <> ''
        """
    )
    try:
        async with engine.connect() as connection:
            result = await connection.execute(query, {"year": year})
            return [tuple(row) for row in result.fetchall()]  # type: ignore[list-item]
    finally:
        await engine.dispose()


def _private_salt(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    salt = path.read_text(encoding="utf-8").strip()
    if len(salt) < 32:
        raise ValueError("salt must contain at least 32 characters")
    return salt


def build_sample(
    *,
    env_file: Path,
    output: Path,
    provenance: Path,
    salt_file: Path | None,
    year: int,
    min_support_per_split: int,
) -> None:
    if salt_file is None:
        from dotenv import dotenv_values

        salt = str(dotenv_values(env_file).get("DEDUP_SALT") or "").strip()
        if len(salt) < 32:
            raise ValueError(
                "DEDUP_SALT must contain at least 32 characters when no salt file is supplied"
            )
    else:
        salt = _private_salt(salt_file)
    records, evidence = prepare_records(
        asyncio.run(_load_rows(env_file, year=year)),
        salt=salt,
        min_support_per_split=min_support_per_split,
    )
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    output.chmod(0o600)
    payload = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "dataset_fingerprint": f"sha256:{hashlib.sha256(rendered.encode()).hexdigest()}",
        "records": len(records),
        "year": year,
        "split_policy": EXPECTED_SPLIT_POLICY,
        "group_policy": EXPECTED_GROUP_POLICY,
        "min_support_per_split": min_support_per_split,
        "label_interpretation": "historical administrative agreement, not policy correctness",
        "privacy": {
            "source_column": "grievance_redactions.grievance_redacted",
            "raw_grievance_read": False,
            "ticket_identifiers_salted": True,
            "narrative_output_private_dvc_only": True,
        },
        **evidence,
    }
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oltp-env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument(
        "--id-salt-file",
        type=Path,
        help="optional private salt file; otherwise use DEDUP_SALT from the env file",
    )
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--min-support-per-split", type=int, default=5)
    args = parser.parse_args(argv)
    build_sample(
        env_file=args.oltp_env_file,
        output=args.output,
        provenance=args.provenance,
        salt_file=args.id_salt_file,
        year=args.year,
        min_support_per_split=args.min_support_per_split,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
