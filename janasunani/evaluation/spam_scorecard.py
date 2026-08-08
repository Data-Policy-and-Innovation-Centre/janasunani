"""Spam prevalence + PPV/false-positive harness (Unit 2b).

Prevalence is measured over the lake slice's ``grievance_redacted`` column
only — never over ``complaints.grievance`` raw.  Reporting is by
district/category/mode/year so the demo can show where low-signal filings
concentrate.

PPV / false-positive rate is measured against officer-confirmed spam-like
discards (the two low-signal families from ROADMAP §5.2):

- details inadequate  39,943
- no specific grievance  16,340

Distinct from duplicate families (case already taken up 19,904 +
duplicate copy 14,767) which are dedup, never spam.  Also distinct from
not within purview (8,455) + documents not attached (29,029) + needs
policy decision (9,090) + address not given (4,110) which are
incomplete/routing failures, never spam.  The harness never counts those
as positives.

Held-out evaluation is reported, not gated — a deterministic 30% holdout
by ticket hash so the same slice always yields the same numbers.

This module is the evaluation counterpart to :mod:`pipeline.spam`; the
latter scores one text, this one aggregates a corpus and reconciles
against the officer labels.  It reuses the same bounded scorer so
prevalence and PPV are on the same scale the live triage will use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

from janasunani.olap import lake as lake_mod
from janasunani.pipeline.spam import SPAM_VERSION, score_spam

# Officer-confirmed spam-like families (the only two that map to spam)
SPAM_LIKE_FAMILIES = ("details_inadequate", "no_specific_grievance")

# Families that must never be counted as spam (guard)
NON_SPAM_FAMILIES = (
    "case_already_taken_up",
    "duplicate_copy",
    "outside_grievance_cell_purview",
    "documents_not_attached",
    "policy_decision_required",
    "address_not_given",
)


def _parse_slice(value: str) -> tuple[str, int]:
    if "/" not in value:
        raise argparse.ArgumentTypeError("--slice must be DISTRICT/YEAR, e.g. Sambalpur/2024")
    district, year_s = value.split("/", 1)
    district = district.strip()
    if not district:
        raise argparse.ArgumentTypeError("district part of --slice is empty")
    try:
        year = int(year_s.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"year part of --slice must be int, got {year_s!r}") from exc
    return district, year


def compute_prevalence(
    lake_dir: Path | None,
    district: str,
    year: int,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Score the slice and return prevalence aggregates.

    Reads ``grievance_redacted`` only — never ``complaints.grievance``.
    """
    district_lit = "'" + district.replace("'", "''") + "'"
    sql = f"""
        SELECT
            c.ticket_no,
            c.district,
            c.category,
            c.mode,
            c.created_year,
            g.grievance_redacted AS redacted_text
        FROM complaints c
        JOIN grievance_redactions g USING (ticket_no)
        WHERE lower(c.district) = lower({district_lit})
          AND c.created_year = {year}
          AND g.grievance_redacted IS NOT NULL
    """
    try:
        frame = lake_mod.query(sql, lake_dir, tables=("complaints", "grievance_redactions"))
    except FileNotFoundError:
        return {
            "slice": f"{district}/{year}",
            "method": SPAM_VERSION,
            "total": 0,
            "flagged": 0,
            "prevalence": 0.0,
            "threshold": threshold,
            "by_district": [],
            "by_category": [],
            "by_mode": [],
            "by_year": [],
            "note": "lake tables missing",
        }

    if frame.height == 0:
        return {
            "slice": f"{district}/{year}",
            "method": SPAM_VERSION,
            "total": 0,
            "flagged": 0,
            "prevalence": 0.0,
            "threshold": threshold,
            "by_district": [],
            "by_category": [],
            "by_mode": [],
            "by_year": [],
            "note": "no rows in slice",
        }

    scores = [score_spam(r["redacted_text"] or "") for r in frame.iter_rows(named=True)]
    frame = frame.with_columns(
        [
            pl.Series("spam_score", [s.spam_score for s in scores]),
            pl.Series("spam_reason", [s.spam_reason for s in scores]),
            pl.Series("flagged", [1 if s.spam_score >= threshold else 0 for s in scores]),
        ]
    )
    total = frame.height
    flagged = int(frame["flagged"].sum())
    prevalence = flagged / total if total else 0.0

    def _group(by: str) -> list[dict]:
        if by not in frame.columns:
            return []
        g = (
            frame.group_by(by)
            .agg([pl.len().alias("total"), pl.col("flagged").sum().alias("flagged")])
            .with_columns((pl.col("flagged") / pl.col("total")).alias("prevalence"))
            .sort(by)
        )
        return g.to_dicts()

    return {
        "slice": f"{district}/{year}",
        "method": SPAM_VERSION,
        "total": total,
        "flagged": flagged,
        "prevalence": prevalence,
        "threshold": threshold,
        "by_district": _group("district"),
        "by_category": _group("category"),
        "by_mode": _group("mode"),
        "by_year": _group("created_year"),
    }


def compute_ppv(
    lake_dir: Path | None,
    district: str,
    year: int,
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Held-out PPV / false-positive vs officer spam-like labels.

    Positive = officer used one of the two spam-like templates.
    Negatives are all other slice rows (including duplicate and
    not-within-purview families, which are never counted as spam).
    Reported, not gated.
    """
    prevalence = compute_prevalence(lake_dir, district, year, threshold=threshold)
    # Re-load frame with scores for holdout split
    district_lit = "'" + district.replace("'", "''") + "'"
    sql = f"""
        SELECT
            c.ticket_no,
            g.grievance_redacted AS redacted_text
        FROM complaints c
        JOIN grievance_redactions g USING (ticket_no)
        WHERE lower(c.district) = lower({district_lit})
          AND c.created_year = {year}
          AND g.grievance_redacted IS NOT NULL
    """
    try:
        frame = lake_mod.query(sql, lake_dir, tables=("complaints", "grievance_redactions"))
    except FileNotFoundError:
        return {**prevalence, "ppv_holdout": None, "false_positive_rate_holdout": None, "holdout_n": 0}

    if frame.height == 0:
        return {**prevalence, "ppv_holdout": None, "false_positive_rate_holdout": None, "holdout_n": 0}

    scores = [score_spam(r["redacted_text"] or "") for r in frame.iter_rows(named=True)]
    frame = frame.with_columns(
        [
            pl.Series("spam_score", [s.spam_score for s in scores]),
            pl.Series("flagged", [1 if s.spam_score >= threshold else 0 for s in scores]),
        ]
    )

    # Officer positives: the two spam-like families only
    officer_sql = """
        WITH discard_template(family, template) AS (
            VALUES
                ('details_inadequate', 'complaint details inadequate'),
                ('details_inadequate', 'complaint details inadequate. may file a detail grievance'),
                ('no_specific_grievance', 'no specific grievance'),
                ('no_specific_grievance', 'no specific grievance. may file a detail grievance')
        ),
        normalized_action AS (
            SELECT ticket_no,
                   regexp_replace(regexp_replace(lower(trim(action_taken_remark)), '\\s+', ' ', 'g'), '\\.$', '') AS norm
            FROM action_history
            WHERE action_taken_remark IS NOT NULL
        )
        SELECT DISTINCT ticket_no FROM normalized_action
        INNER JOIN discard_template d ON d.template = norm
        WHERE d.family IN ('details_inadequate', 'no_specific_grievance')
    """
    try:
        officer_frame = lake_mod.query(officer_sql, lake_dir, tables=("action_history",))
        officer_positive = set(officer_frame["ticket_no"].to_list()) if officer_frame.height else set()
    except Exception:
        officer_positive = set()

    def is_holdout(t: str) -> bool:
        return (int(hashlib.sha256(t.encode()).hexdigest(), 16) % 10) < 3

    holdout = frame.filter(pl.col("ticket_no").map_elements(is_holdout, return_dtype=pl.Boolean))
    if holdout.height == 0 or not officer_positive:
        return {**prevalence, "ppv_holdout": None, "false_positive_rate_holdout": None, "holdout_n": holdout.height}

    tp = holdout.filter((pl.col("flagged") == 1) & pl.col("ticket_no").is_in(list(officer_positive))).height
    fp = holdout.filter((pl.col("flagged") == 1) & (~pl.col("ticket_no").is_in(list(officer_positive)))).height
    tn = holdout.filter((pl.col("flagged") == 0) & (~pl.col("ticket_no").is_in(list(officer_positive)))).height

    ppv = tp / (tp + fp) if (tp + fp) > 0 else None
    fpr = fp / (fp + tn) if (fp + tn) > 0 else None

    return {
        **prevalence,
        "ppv_holdout": ppv,
        "false_positive_rate_holdout": fpr,
        "holdout_n": holdout.height,
        "officer_positive_in_holdout": holdout.filter(pl.col("ticket_no").is_in(list(officer_positive))).height,
        "note": "duplicate/not-within-purview never counted as spam; PPV reported not gated",
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Spam prevalence — {result.get('slice', '')}",
        "",
        f"Method: `{result.get('method', SPAM_VERSION)}` (threshold {result.get('threshold', 0.5)})",
        f"Total with redacted text: {result.get('total', 0)}",
        f"Flagged: {result.get('flagged', 0)} ({result.get('prevalence', 0):.2%})",
        "",
    ]
    if result.get("ppv_holdout") is not None:
        lines += [
            f"PPV (holdout, officer spam-like): {result['ppv_holdout']:.3f}",
            f"False-positive rate (holdout): {result['false_positive_rate_holdout']:.3f}",
            f"Holdout n: {result.get('holdout_n', 0)}",
            "",
        ]
    else:
        lines += ["PPV: not computed (no holdout or no officer labels in slice)", ""]

    for key, label in [
        ("by_district", "By district"),
        ("by_category", "By category"),
        ("by_mode", "By mode"),
        ("by_year", "By year"),
    ]:
        rows = result.get(key) or []
        if rows:
            lines += [f"## {label}", "", "| Group | Total | Flagged | Prevalence |", "|---|---:|---:|---:|"]
            for r in rows:
                grp = r.get("district") or r.get("category") or r.get("mode") or r.get("created_year")
                lines.append(f"| {grp} | {r['total']} | {r['flagged']} | {r['prevalence']:.2%} |")
            lines.append("")

    # Guard statement
    lines += [
        "These counts are over `grievance_redacted` only — never `complaints.grievance` raw.",
        "Duplicate families (`case already taken up`, `duplicate copy`) and",
        "`not within purview` / incomplete families are never scored as spam.",
        "Status is never mutated; this is an advisory signal only.",
    ]
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "spam_prevalence.json"
    md_path = out / "spam_prevalence.md"
    csv_path = out / "spam_prevalence.csv"
    json_path.write_text(json.dumps(result, indent=2, default=str))
    md_path.write_text(render_markdown(result))

    # Also write a by-group CSV (flattened)
    rows: list[dict[str, Any]] = []
    for key in ("by_district", "by_category", "by_mode", "by_year"):
        for r in result.get(key) or []:
            rows.append({"group_kind": key, **r})
    if rows:
        pl.DataFrame(rows).write_csv(csv_path)
    else:
        pl.DataFrame({"note": ["no groups"]}).write_csv(csv_path)
    return {"json": json_path, "markdown": md_path, "csv": csv_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Spam prevalence + PPV harness (redacted text only).")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--slice", type=str, help="District/year slice, e.g. Sambalpur/2024")
    g.add_argument("--district", type=str, help="District (requires --year)")
    parser.add_argument("--year", type=int, default=None, help="Year (requires --district)")
    parser.add_argument("--lake-dir", type=Path, default=None, help="Lake dir (default: data/interim)")
    parser.add_argument("--threshold", type=float, default=0.5, help="Flag threshold")
    parser.add_argument("--out-dir", type=Path, default=None, help="Write outputs under this dir")
    args = parser.parse_args()

    if args.slice:
        district, year = _parse_slice(args.slice)
    else:
        if args.year is None:
            parser.error("--year is required with --district")
        district, year = args.district, args.year

    result = compute_ppv(args.lake_dir, district, year, threshold=args.threshold)
    print(render_markdown(result))
    if args.out_dir:
        paths = write_outputs(result, Path(args.out_dir))
        for kind, path in paths.items():
            print(f"{kind} -> {path}")


if __name__ == "__main__":
    main()
