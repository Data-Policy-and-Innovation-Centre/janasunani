"""Governed metric definitions over the OLAP lake (#77, cut down from the full
semantic layer described in ROADMAP.md §5.3 S1).

**Deferred, deliberately**: the general YAML-to-SQL compiler with declared
legal dimension pairings. Its only consumer is Phase 20 natural-language
querying, out of scope for August (DELIVERY.md §"Not in scope"). What ships
here instead is a small Python registry -- named, versioned
:class:`MetricDefinition` objects, each with a real, executable DuckDB SQL
body, a stated denominator and caveats -- enough to back the supervisor
screen (#79) and the one worked spike (#78), with per-metric tests.

**Governed** means one definition, one id, traceable back from a number on a
screen. Two people computing "pending complaints" two different ways is the
failure this module exists to prevent, so the checks below run at
registration time (import time), not as something a caller can skip:

- every definition states a denominator -- a bare count with no stated
  population is not a metric, it is a number (this is the closure finding's
  recurring lesson: 61% and 39% differ only by denominator);
- no definition's SQL selects the raw ``complaints.grievance`` column
  (ROADMAP §3.2 -- the analytics surface reads ``grievance_redacted``);
- a metric this module cannot compute from the lake as it stands says so
  (``unavailable_reason``) instead of inventing a column.

:func:`compute_metric` is the one read path, and it enforces small-cell
suppression itself -- a caller cannot ask for the unsuppressed value.

**The three counts** (ROADMAP §5.3 S2): total filings, unique grievance
clusters, unique citizens/signatories. One number cannot answer "how much
work arrived" -- 500 citizens filing about the same pothole is a real signal,
and collapsing it to one row destroys exactly what the reader needs to see.
Every spike view is expected to carry all three (#78), which is why they are
defined together here even though two of them cannot run yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from janasunani.olap import lake

# Below this a grouped cell is small enough that it risks describing one
# office's handful of cases for one week rather than a pattern -- close to
# identifying who is in it. Applies to dimensional breakdowns only; an
# ungrouped, corpus-wide total is not itself disclosive and is never
# suppressed by this layer. This is *the* S1 small-cell threshold ROADMAP
# §5.3 S4 refers back to for block-level cells rather than inventing its own.
SMALL_CELL_THRESHOLD = 10

# Catches `grievance` as a standalone identifier (bare, qualified as
# `complaints.grievance`, or quoted) without also matching
# `grievance_redacted` or the `grievance_redactions` table name: `\b...\b`
# requires a non-word boundary on both sides, and `_` is a word character, so
# neither suffixed form has a boundary right after "grievance".
_RAW_GRIEVANCE_RE = re.compile(r"\bgrievance\b", re.IGNORECASE)


def _forbid_raw_grievance(metric_id: str, sql_fragment: str) -> None:
    if _RAW_GRIEVANCE_RE.search(sql_fragment):
        raise ValueError(
            f"{metric_id}: SQL references the raw `grievance` column. "
            "No query in the metrics path may select it (ROADMAP §3.2) -- "
            "read `grievance_redacted` off `grievance_redactions` instead."
        )


class MetricNotComputable(Exception):
    """Raised by :func:`compute_metric` when a definition has no SQL body
    (``unavailable_reason`` is set) or a table it depends on is missing from
    the lake directory. Distinct from a DuckDB error: this is "the lake does
    not yet have what this metric needs," not "the SQL is wrong."
    """


@dataclass(frozen=True)
class MetricDefinition:
    """One governed metric: a stable id, a name, a description, a stated
    denominator and either an executable query or a stated reason it has
    none yet.

    ``source``/``measure`` rather than one opaque SQL string so
    :func:`compute_metric` can append a ``GROUP BY`` over declared
    ``dimensions`` without string-munging a caller-opaque query. The full,
    ungrouped statement -- the "tested SQL body" #77 asks for -- is exposed
    as :attr:`sql`.
    """

    id: str
    name: str
    description: str
    denominator: str
    tables: tuple[str, ...]
    source: Optional[str] = None
    measure: Optional[str] = None
    dimensions: tuple[str, ...] = ()
    caveats: tuple[str, ...] = field(default_factory=tuple)
    unavailable_reason: Optional[str] = None

    def __post_init__(self) -> None:
        has_query = self.source is not None and self.measure is not None
        if has_query == bool(self.unavailable_reason):
            raise ValueError(
                f"{self.id}: exactly one of (source and measure) or "
                "unavailable_reason must be set -- a metric is either "
                "computable or it says why it is not, never both or neither"
            )
        if not self.denominator:
            raise ValueError(
                f"{self.id}: every metric must state its denominator -- a "
                "bare number is not a governed metric"
            )
        if has_query:
            _forbid_raw_grievance(self.id, self.source or "")
            _forbid_raw_grievance(self.id, self.measure or "")

    @property
    def sql(self) -> Optional[str]:
        """The full, standalone, ungrouped DuckDB statement -- ``None`` when
        :attr:`unavailable_reason` explains why there isn't one."""
        if self.measure is None:
            return None
        return f"SELECT {self.measure} AS value FROM {self.source}"

    @property
    def computable(self) -> bool:
        return self.measure is not None


@dataclass(frozen=True)
class MetricResult:
    """One row of a computed metric: a value (or ``None`` if suppressed),
    the denominator it was carried with, which dimension values it was
    grouped to (empty for the overall total), and how fresh the lake was
    when it ran."""

    metric_id: str
    value: Optional[int]
    denominator: str
    dimensions: dict[str, object]
    suppressed: bool
    lake_as_of: datetime


def _build_registry(*definitions: MetricDefinition) -> dict[str, MetricDefinition]:
    registry: dict[str, MetricDefinition] = {}
    for definition in definitions:
        if definition.id in registry:
            raise ValueError(f"duplicate metric id {definition.id!r}")
        registry[definition.id] = definition
    return registry


TOTAL_FILINGS = MetricDefinition(
    id="total_filings",
    name="Total filings",
    description=(
        "Every complaint row in the lake, undeduplicated. Answers 'how much "
        "work arrived', not 'how many distinct problems' -- see "
        "unique_grievance_clusters for that. A campaign (many citizens "
        "filing about the same issue) is a real signal and must never be "
        "silently collapsed out of this count (ROADMAP §5.3 S2)."
    ),
    denominator=(
        "All complaints in the lake (optionally grouped by district / "
        "category / created_year). This is the base count the other two "
        "S2 counts are read against; it is not itself a share of anything "
        "narrower."
    ),
    tables=("complaints",),
    source="complaints",
    measure="count(*)",
    dimensions=("district", "category", "created_year"),
    caveats=(
        "Counts near-duplicate resubmissions and campaign filings as "
        "separate rows, by design -- report alongside "
        "unique_grievance_clusters, never alone, so a reader can see the "
        "collapse ratio.",
        "district and category carry missingness (measured: category null "
        "on 16.9% of rows); a null value groups under DuckDB's own NULL "
        "bucket rather than being silently dropped.",
    ),
)

UNIQUE_GRIEVANCE_CLUSTERS = MetricDefinition(
    id="unique_grievance_clusters",
    name="Unique grievance clusters",
    description=(
        "Distinct problems after dedup, i.e. 'how many distinct issues' as "
        "opposed to raw filing volume."
    ),
    denominator=(
        "Same complaint population as total_filings for the same filters. "
        "Report the two together -- this count alone, without "
        "total_filings, cannot show whether a spike is one campaign or "
        "hundreds of distinct problems."
    ),
    tables=("dedup_groups",),
    dimensions=("district", "category", "created_year"),
    unavailable_reason=(
        "needs `dedup_groups.duplicate_group_id` (the Phase 14 dedup index "
        "backfill, #50/#71). `dedup_groups` is deliberately excluded from "
        "`olap.materialize.LAKE_TABLES` -- it is a `dpic-infra` artifact "
        "pending the Phase 18 RBAC/audit scope, see that module's comment "
        "next to the tuple -- so this metric cannot run against the lake "
        "as it stands. #78 (the one worked spike) is blocked on the same "
        "backfill."
    ),
)

UNIQUE_CITIZENS_SIGNATORIES = MetricDefinition(
    id="unique_citizens_signatories",
    name="Unique citizens / signatories",
    description=(
        "Distinct people, not filings, counted via the salted identity "
        "keys rather than raw `petitioner_mobile` -- 'how many people' "
        "behind a spike or a workload figure."
    ),
    denominator=(
        "Same complaint population as total_filings for the same filters."
    ),
    tables=("dedup_signatures",),
    dimensions=("district", "category", "created_year"),
    unavailable_reason=(
        "needs the salted `identity_key_mobile`/`identity_key_email` "
        "columns on `dedup_signatures` (#71). Excluded from "
        "`olap.materialize.LAKE_TABLES` for the same `dpic-infra` reason "
        "as `dedup_groups` -- see that module's comment. Not computable "
        "from the lake as it stands."
    ),
)

REGISTRY: dict[str, MetricDefinition] = _build_registry(
    TOTAL_FILINGS, UNIQUE_GRIEVANCE_CLUSTERS, UNIQUE_CITIZENS_SIGNATORIES
)


def list_metrics() -> list[MetricDefinition]:
    """Every governed definition, introspectable without running any of them
    -- the "stand alone as a query target" requirement (ROADMAP §5.3 S1)."""
    return sorted(REGISTRY.values(), key=lambda d: d.id)


def get_definition(metric_id: str) -> MetricDefinition:
    try:
        return REGISTRY[metric_id]
    except KeyError:
        raise KeyError(
            f"no such metric {metric_id!r}; known: {sorted(REGISTRY)}"
        ) from None


def _lake_as_of(tables: Sequence[str], lake_dir: Optional[Path]) -> datetime:
    """A metric is only as fresh as the stalest table it reads, so this is
    the min mtime across ``tables`` -- not "now"."""
    freshness = lake.lake_freshness(lake_dir)
    return min(freshness[table] for table in tables)


def compute_metric(
    metric_id: str,
    *,
    group_by: Sequence[str] = (),
    lake_dir: Optional[Path] = None,
) -> list[MetricResult]:
    """Run a governed metric, grouped by zero or more of its declared
    ``dimensions``.

    Small-cell suppression is applied here, in the layer, on every grouped
    row below :data:`SMALL_CELL_THRESHOLD` -- there is no argument to ask
    for the unsuppressed value. The ungrouped, whole-lake total is never
    suppressed.
    """
    definition = get_definition(metric_id)
    if not definition.computable:
        raise MetricNotComputable(f"{metric_id}: {definition.unavailable_reason}")

    group_by = tuple(group_by)
    illegal = set(group_by) - set(definition.dimensions)
    if illegal:
        raise ValueError(
            f"{metric_id}: {sorted(illegal)} not declared in this metric's "
            f"legal dimensions {definition.dimensions}"
        )

    missing = [
        table
        for table in definition.tables
        if not lake.lake_path(table, lake_dir).exists()
    ]
    if missing:
        raise MetricNotComputable(
            f"{metric_id}: {', '.join(missing)}.parquet not present in the lake"
        )

    as_of = _lake_as_of(definition.tables, lake_dir)

    if group_by:
        columns = ", ".join(group_by)
        sql = (
            f"SELECT {columns}, {definition.measure} AS value "
            f"FROM {definition.source} GROUP BY {columns}"
        )
        frame = lake.query(sql, lake_dir)
        results = []
        for row in frame.iter_rows(named=True):
            value = int(row["value"])
            suppressed = value < SMALL_CELL_THRESHOLD
            results.append(
                MetricResult(
                    metric_id=metric_id,
                    value=None if suppressed else value,
                    denominator=definition.denominator,
                    dimensions={dim: row[dim] for dim in group_by},
                    suppressed=suppressed,
                    lake_as_of=as_of,
                )
            )
        return results

    frame = lake.query(definition.sql, lake_dir)
    value = int(frame["value"][0])
    return [
        MetricResult(
            metric_id=metric_id,
            value=value,
            denominator=definition.denominator,
            dimensions={},
            suppressed=False,
            lake_as_of=as_of,
        )
    ]
