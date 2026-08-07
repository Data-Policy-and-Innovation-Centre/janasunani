"""Empirical routing crosswalk built from what actually happened (#33).

The ORTPSA master tables carry no category-to-department link — `intCategoryGrp`
is NULL on all 62 categories — so `MappingRouter` can only bridge by exact name
equality, which covers a handful. The OLAP history has the link the masters
lack: **83.1% of the 1.37M complaints record both a `category` and the `dept`
they were actually routed to.**

This module turns that into a lookup. Measured argmax accuracy, on history:

    category -> dept                        60.9%
    + subcategory                           67.5%
    + subcategory + district                72.8%

**It learns where cases were sent, not where they were resolved well.** Those
are different questions and the demo narrative must not blur them. A route this
returns is "the office that historically handled this kind of complaint here",
which is a reasonable default and not a recommendation. The outcome-optimising
version — ranking on disposal time and citizen benefit rather than incidence —
is deliberately deferred: it carries an omitted-variable problem (disposal time
is confounded by case difficulty and by non-random office selection) that needs
causal care before it drives anything real.

**Confidence is computed, not asserted.** A route backed by four rows and one
backed by forty thousand must not present identically, so confidence combines
how concentrated the destination is (share of the winning dept) with how much
evidence there is (support count). Both are carried on the result so a caller
can show them.

The crosswalk is an artifact: built once from the lake by
:func:`build_crosswalk`, loaded at route time. If the file is absent the router
returns ``None`` and the caller falls through to the mapping tables and then
the generic fallback, so a missing artifact degrades rather than breaks.

Aggregates only — category, subcategory, district, dept and counts. No citizen
text and no identifiers ever enter this file, which is why it is safe to commit
the built artifact where the corpus itself is not.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from janasunani.routing.mappings import _norm

# Support at which the evidence term saturates. Chosen so a few hundred
# historical routings count as "well attested" and a handful does not; the
# curve is logarithmic, so the difference between 4 and 400 is large and
# between 4,000 and 40,000 is small, which matches how much those differences
# actually tell you.
SUPPORT_SATURATION = 200

# Never claim certainty from a frequency count. Even a destination with perfect
# historical concentration is a description of the past, not a judgement that
# it was correct.
MAX_CONFIDENCE = 0.95

# Below this the route is not worth returning: too few rows to distinguish a
# pattern from an accident, and a wrong confident route is worse than falling
# through to the mapping tables.
MIN_SUPPORT = 3

# Ships inside the package rather than under data/. It is aggregates with a
# support floor of MIN_SUPPORT, carries no identifiers and no text, and the
# demo must not depend on a `dvc pull` step someone can forget -- the ED asked
# for routing as a working surface, not a working surface with a prerequisite.
DEFAULT_ARTIFACT = Path(__file__).parent / "reference" / "routing_crosswalk.json"


@dataclass(frozen=True)
class CrosswalkRoute:
    """One resolved destination, with the evidence behind it."""

    dept: str
    support: int
    share: float
    width: str

    @property
    def confidence(self) -> float:
        """Concentration tempered by evidence.

        `share` alone would let a single historical routing look certain;
        `support` alone would let a heavily-used but evenly-split category look
        certain. The product is low unless the destination is both dominant and
        well attested.
        """
        evidence = math.log1p(self.support) / math.log1p(SUPPORT_SATURATION)
        return round(min(MAX_CONFIDENCE, self.share * min(1.0, evidence)), 4)


def _key(category: str, subcategory: Optional[str], district: Optional[str]) -> str:
    return "|".join(
        _norm(part) for part in (category, subcategory or "", district or "")
    )


@dataclass(frozen=True)
class Crosswalk:
    """Three lookup widths and the ladder that walks them."""

    by_full: dict[str, dict]
    by_subcategory: dict[str, dict]
    by_category: dict[str, dict]

    def lookup(
        self,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> Optional[CrosswalkRoute]:
        """The best-supported rung, not simply the most specific one.

        The full key is the most accurate width overall (72.8% against 60.9%
        for category alone), so specificity is the right default. But a
        *particular* narrow cell can be thin where the broader one is solid --
        `Accident / Fire Accident / Cuttack` has a support of 3 and scores
        0.26, while `Accident / Fire Accident` has 66 and scores 0.56. Taking
        the narrow one there would hand a caller the less trustworthy answer
        and label it more confident than it is.

        Since confidence is computed rather than asserted, using it to choose
        between rungs is the consequence: evaluate the ladder and return the
        highest-scoring match. Ties keep the more specific rung, which is the
        original ordering.
        """
        ladder = (
            ("category+subcategory+district", self.by_full, (category, subcategory, district)),
            ("category+subcategory", self.by_subcategory, (category, subcategory, None)),
            ("category", self.by_category, (category, None, None)),
        )
        candidates: list[CrosswalkRoute] = []
        for width, table, parts in ladder:
            if parts[1] is None and width != "category":
                continue
            if width == "category+subcategory+district" and parts[2] is None:
                continue
            entry = table.get(_key(*parts))
            if entry and entry["support"] >= MIN_SUPPORT:
                candidates.append(
                    CrosswalkRoute(
                        dept=entry["dept"],
                        support=int(entry["support"]),
                        share=float(entry["share"]),
                        width=width,
                    )
                )
        if not candidates:
            return None
        # max() keeps the first of equal scores, and `ladder` runs narrowest
        # first, so a tie resolves to the more specific rung.
        return max(candidates, key=lambda route: route.confidence)


def _argmax_table(rows) -> dict[str, dict]:
    """Winning dept per key, with its share of that key's total."""
    totals: dict[str, int] = {}
    best: dict[str, tuple[str, int]] = {}
    for key, dept, count in rows:
        totals[key] = totals.get(key, 0) + count
        current = best.get(key)
        if current is None or count > current[1]:
            best[key] = (dept, count)
    # Cells below the support floor are dropped at build time, not just
    # skipped at lookup. Two reasons and the second is the one that matters:
    # the router would refuse them anyway, and a (category, subcategory,
    # district) cell with a support of one says that exactly one complaint of
    # that kind exists in that district. That is thin, but it is a disclosure
    # the artifact does not need to carry -- and it is what makes this file
    # aggregates rather than something derived closely enough from individuals
    # to need the treatment the corpus gets.
    return {
        key: {"dept": dept, "support": count, "share": round(count / totals[key], 4)}
        for key, (dept, count) in best.items()
        if count >= MIN_SUPPORT
    }


def build_crosswalk(lake_dir: Optional[Path] = None) -> Crosswalk:
    """Aggregate the history into the three lookup widths.

    Reads the lake rather than OLTP: this is analytical work over the whole
    corpus, which is what the Parquet snapshot exists for.
    """
    from janasunani.olap.lake import query

    def rows_for(group_cols: str, key_expr: str):
        frame = query(
            f"""
            SELECT {key_expr} AS k, dept, COUNT(*) AS n
            FROM complaints
            WHERE category IS NOT NULL AND category <> ''
              AND dept IS NOT NULL AND dept <> ''
              {group_cols}
            GROUP BY k, dept
            """,
            lake_dir,
        )
        return [(r[0], r[1], int(r[2])) for r in frame.iter_rows()]

    full = rows_for(
        "AND subcategory IS NOT NULL AND subcategory <> '' "
        "AND district IS NOT NULL AND district <> ''",
        "lower(trim(category)) || '|' || lower(trim(subcategory)) || '|' || lower(trim(district))",
    )
    sub = rows_for(
        "AND subcategory IS NOT NULL AND subcategory <> ''",
        "lower(trim(category)) || '|' || lower(trim(subcategory)) || '|'",
    )
    cat = rows_for("", "lower(trim(category)) || '|' || '|'")

    return Crosswalk(
        by_full=_argmax_table(full),
        by_subcategory=_argmax_table(sub),
        by_category=_argmax_table(cat),
    )


def save_crosswalk(crosswalk: Crosswalk, path: Path = DEFAULT_ARTIFACT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "by_full": crosswalk.by_full,
                "by_subcategory": crosswalk.by_subcategory,
                "by_category": crosswalk.by_category,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def load_crosswalk(path: Path = DEFAULT_ARTIFACT) -> Optional[Crosswalk]:
    """The built artifact, or ``None`` when it has not been built.

    ``None`` rather than an exception on purpose: a missing crosswalk must
    degrade to the mapping tables and the generic fallback, not take the live
    path down. #33's stated degradation.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Crosswalk(
            by_full=payload["by_full"],
            by_subcategory=payload["by_subcategory"],
            by_category=payload["by_category"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def main() -> None:
    import argparse

    from loguru import logger

    parser = argparse.ArgumentParser(
        description="Build the empirical routing crosswalk from the Parquet lake."
    )
    parser.add_argument("--lake-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()

    crosswalk = build_crosswalk(args.lake_dir)
    path = save_crosswalk(crosswalk, args.out)
    logger.info(
        "wrote {}: {} full / {} subcategory / {} category keys",
        path,
        len(crosswalk.by_full),
        len(crosswalk.by_subcategory),
        len(crosswalk.by_category),
    )
