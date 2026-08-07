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

**The live classifier does not predict a subcategory.** `PipelineGrievanceProcessor.process` (`inference/service.py`) calls
``route(category=..., district=...)`` -- no ``subcategory`` argument exists on
that path. The two subcategory-keyed rungs above are therefore unreachable
from the deployed router; only ``category -> dept`` and the
``category + district -> dept`` rung below are ever consulted live, which is
why the latter exists as a first-class table rather than a derived fallback.

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

# Below this the crosswalk declines rather than override a better-grounded
# layer. It must clear the generic fallback's 0.25 -- a route less certain
# than "we don't know" is strictly worse -- and the margin is deliberate, not
# a hair's width: fragmented categories such as "general" (0.216),
# "miscellaneous" (0.208) and "pension/retirement benefits" (0.194) sit just
# under 0.25 already and must fall through to the mapping tables and the
# generic fallback, not outrank them.
MIN_CONFIDENCE = 0.3

# A department's routings for a key must be reasonably concentrated in one
# office before the artifact asserts that office by name -- otherwise
# `argmax(dept, office)` would report whichever office happened to edge out a
# near-tie, which is not a destination history actually supports. Below this
# (or below MIN_SUPPORT rows for that office specifically), the entry names
# the department only.
OFFICE_MIN_SHARE = 0.5

# Ships inside the package rather than under data/. It is aggregates with a
# support floor of MIN_SUPPORT, carries no identifiers and no text, and the
# demo must not depend on a `dvc pull` step someone can forget -- the ED asked
# for routing as a working surface, not a working surface with a prerequisite.
# The currently shipped legacy artifact predates the category+district rung,
# so that table is explicitly empty. It cannot be reconstructed correctly from
# the old full-key argmax cells because their losing-department counts were
# discarded; only a fresh `build_crosswalk` run over source rows may populate
# it. Lookup therefore falls through to the category rung for that artifact.
DEFAULT_ARTIFACT = Path(__file__).parent / "reference" / "routing_crosswalk.json"


@dataclass(frozen=True)
class CrosswalkRoute:
    """One resolved destination, with the evidence behind it."""

    dept: str
    support: int
    share: float
    width: str
    # None when the department's routings for this key are too fragmented
    # across offices to name one (see OFFICE_MIN_SHARE) -- a caller must not
    # invent a specific office in that case.
    office: Optional[str] = None

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
    """Four lookup widths and the ladder that walks them."""

    by_full: dict[str, dict]
    by_subcategory: dict[str, dict]
    by_category_district: dict[str, dict]
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
        highest-scoring match. Ties keep the earlier (narrower / more
        informative) rung, since `max()` keeps the first of equal scores and
        `ladder` runs in that order.

        `category+district` exists because the live classifier never supplies
        a subcategory (see the module docstring) -- without it, a district-
        specific request that misses the full and subcategory rungs would
        skip straight to the bare category table, throwing away exactly the
        district signal a caller actually provided.

        Below `MIN_CONFIDENCE` the best candidate is refused rather than
        returned: a route less certain than the generic fallback is worse
        than no route at all.
        """
        ladder = (
            (
                "category+subcategory+district",
                self.by_full,
                (category, subcategory, district),
                subcategory is not None and district is not None,
            ),
            (
                "category+subcategory",
                self.by_subcategory,
                (category, subcategory, None),
                subcategory is not None,
            ),
            (
                "category+district",
                self.by_category_district,
                (category, None, district),
                district is not None,
            ),
            ("category", self.by_category, (category, None, None), True),
        )
        candidates: list[CrosswalkRoute] = []
        for width, table, parts, reachable in ladder:
            if not reachable:
                continue
            entry = table.get(_key(*parts))
            if entry and entry["support"] >= MIN_SUPPORT:
                candidates.append(
                    CrosswalkRoute(
                        dept=entry["dept"],
                        support=int(entry["support"]),
                        share=float(entry["share"]),
                        width=width,
                        office=entry.get("office"),
                    )
                )
        if not candidates:
            return None
        best = max(candidates, key=lambda route: route.confidence)
        if best.confidence < MIN_CONFIDENCE:
            return None
        return best


def _argmax_table(rows) -> dict[str, dict]:
    """Winning (dept, office) per key, with the dept's share of the key's total.

    ``rows`` is ``(key, dept, office, count)``, pre-grouped by the caller's
    SQL at that granularity. Two argmaxes happen: which dept won the key --
    decided on the dept's total count across every office, as before -- and,
    within that dept, which office won its rows. The office is only carried
    through when it clears both floors (`MIN_SUPPORT`, `OFFICE_MIN_SHARE`): a
    coin-flip office is not something history actually supports, and a caller
    asking "where does this go" deserves the department rather than a
    fabricated-looking specific answer in that case.
    """
    key_totals: dict[str, int] = {}
    dept_totals: dict[tuple[str, str], int] = {}
    office_counts: dict[tuple[str, str], dict[str, int]] = {}
    for key, dept, office, count in rows:
        key_totals[key] = key_totals.get(key, 0) + count
        dept_key = (key, dept)
        dept_totals[dept_key] = dept_totals.get(dept_key, 0) + count
        by_office = office_counts.setdefault(dept_key, {})
        office_name = office or ""
        by_office[office_name] = by_office.get(office_name, 0) + count

    best: dict[str, tuple[str, int]] = {}
    for (key, dept), count in dept_totals.items():
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
    table: dict[str, dict] = {}
    for key, (dept, count) in best.items():
        if count < MIN_SUPPORT:
            continue
        entry = {"dept": dept, "support": count, "share": round(count / key_totals[key], 4)}
        office_name, office_count = max(
            office_counts.get((key, dept), {}).items(),
            key=lambda item: item[1],
            default=("", 0),
        )
        if (
            office_name
            and office_count >= MIN_SUPPORT
            and office_count / count > OFFICE_MIN_SHARE
        ):
            entry["office"] = office_name
        table[key] = entry
    return table


def build_crosswalk(lake_dir: Optional[Path] = None) -> Crosswalk:
    """Aggregate the history into the four lookup widths.

    Reads the lake rather than OLTP: this is analytical work over the whole
    corpus, which is what the Parquet snapshot exists for.

    Keys are built in Python via :func:`_key`, the same function
    :meth:`Crosswalk.lookup` normalizes with at read time -- not replicated as
    SQL string surgery, which previously fell out of sync (SQL's
    ``lower(trim(...))`` left repeated internal whitespace where `_key`'s
    `_norm` collapses it, so a handful of built keys were unreachable from any
    real lookup). Grouping on the raw columns rather than a pre-normalized SQL
    key means rows that only differ by case or internal spacing arrive as
    separate SQL rows and are merged by `_argmax_table` instead -- which is
    also strictly more correct than the old SQL-side normalization.
    """
    from janasunani.olap.lake import query

    base_where = (
        "category IS NOT NULL AND trim(category) <> '' "
        "AND dept IS NOT NULL AND trim(dept) <> ''"
    )

    def rows(select_cols: str, where_extra: str, key_fn):
        frame = query(
            f"""
            SELECT {select_cols}, dept, office, COUNT(*) AS n
            FROM complaints
            WHERE {base_where} {where_extra}
            GROUP BY {select_cols}, dept, office
            """,
            lake_dir,
        )
        return [
            (key_fn(r), r["dept"], r["office"], int(r["n"]))
            for r in frame.iter_rows(named=True)
        ]

    full = rows(
        "category, subcategory, district",
        "AND subcategory IS NOT NULL AND trim(subcategory) <> '' "
        "AND district IS NOT NULL AND trim(district) <> ''",
        lambda r: _key(r["category"], r["subcategory"], r["district"]),
    )
    sub = rows(
        "category, subcategory",
        "AND subcategory IS NOT NULL AND trim(subcategory) <> ''",
        lambda r: _key(r["category"], r["subcategory"], None),
    )
    cat_district = rows(
        "category, district",
        "AND district IS NOT NULL AND trim(district) <> ''",
        lambda r: _key(r["category"], None, r["district"]),
    )
    cat = rows(
        "category",
        "",
        lambda r: _key(r["category"], None, None),
    )

    return Crosswalk(
        by_full=_argmax_table(full),
        by_subcategory=_argmax_table(sub),
        by_category_district=_argmax_table(cat_district),
        by_category=_argmax_table(cat),
    )


_TABLE_KEYS = ("by_full", "by_subcategory", "by_category_district", "by_category")


def save_crosswalk(crosswalk: Crosswalk, path: Path = DEFAULT_ARTIFACT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "by_full": crosswalk.by_full,
                "by_subcategory": crosswalk.by_subcategory,
                "by_category_district": crosswalk.by_category_district,
                "by_category": crosswalk.by_category,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _valid_entry(entry: object) -> bool:
    """One key's aggregate has the shape `_argmax_table` produces: a
    non-empty `dept`, integer `support`, bounded numeric `share`, and an `office` that is
    either absent or a string. Anything else -- a list where a table should
    be, a dict missing a field, the wrong type -- is a corrupt artifact, not
    a differently-shaped one, and must not be trusted at lookup time.
    """
    if not isinstance(entry, dict):
        return False
    dept = entry.get("dept")
    if not isinstance(dept, str) or not dept.strip():
        return False
    support = entry.get("support")
    if isinstance(support, bool) or not isinstance(support, int) or support < MIN_SUPPORT:
        return False
    share = entry.get("share")
    if (
        isinstance(share, bool)
        or not isinstance(share, (int, float))
        or not math.isfinite(share)
        or not 0.0 < share <= 1.0
    ):
        return False
    office = entry.get("office")
    if office is not None and (not isinstance(office, str) or not office.strip()):
        return False
    return True


def _valid_table(table: object, width: str) -> bool:
    """Reject malformed or misplaced entries before the live router sees them."""
    if not isinstance(table, dict):
        return False
    required = {
        "by_full": (True, True),
        "by_subcategory": (True, False),
        "by_category_district": (False, True),
        "by_category": (False, False),
    }[width]
    for key, entry in table.items():
        if not isinstance(key, str) or not _valid_entry(entry):
            return False
        parts = key.split("|")
        if len(parts) != 3 or key != _key(*parts):
            return False
        has_subcategory, has_district = bool(parts[1]), bool(parts[2])
        if (has_subcategory, has_district) != required:
            return False
    return True


def load_crosswalk(path: Path = DEFAULT_ARTIFACT) -> Optional[Crosswalk]:
    """The built artifact, or ``None`` when it is absent or unusable.

    ``None`` rather than an exception on purpose: a missing *or corrupt*
    crosswalk must degrade to the mapping tables and the generic fallback,
    not take the live path down -- #33's stated degradation. A syntactically
    valid JSON file that is structurally wrong (a table encoded as a list, an
    entry missing `support`/`share`) is exactly as unusable as a missing
    file, so every table and every entry is shape-checked here rather than
    trusted until the first lookup raises.
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    tables: dict[str, dict] = {}
    for name in _TABLE_KEYS:
        table = payload.get(name)
        if not _valid_table(table, name):
            return None
        tables[name] = table
    return Crosswalk(
        by_full=tables["by_full"],
        by_subcategory=tables["by_subcategory"],
        by_category_district=tables["by_category_district"],
        by_category=tables["by_category"],
    )


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
        "wrote {}: {} full / {} subcategory / {} category+district / {} category keys",
        path,
        len(crosswalk.by_full),
        len(crosswalk.by_subcategory),
        len(crosswalk.by_category_district),
        len(crosswalk.by_category),
    )
