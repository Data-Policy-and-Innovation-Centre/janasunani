"""Which grievances an analysis stands on, defined once as data.

A note is three things that change at different rates: *which grievances*,
*what is computed over them*, and *which document says it*. This module is the
first. :class:`Sample` is a base filter over ``grievance_base`` plus an ordered
list of narrowing steps; ``janasunani.analytics.exhibits`` is the second, and
takes a sample; notebooks and docx scripts are the third, and only compose the
two. A new note is a new notebook and a new list of steps, never a new class.

**The sample narrows and never widens.** Each step's count is computed once
into ``FUNNEL`` and every exhibit takes its n from there, so an exhibit cannot
quietly stand on a population the reader has not been shown. The constructor
asserts the funnel only narrows, and that any partition it is given accounts
for every row of the steps it partitions.

``ns`` prefixes every table the sample creates (``{ns}g``, ``{ns}steps``,
``{ns}phases_clean`` ...), so two samples can live on one connection without
one silently overwriting the tables the other reads. The empty prefix keeps the
historical names, which raw SQL in ``scripts/analysis`` still uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import polars as pl

from janasunani.analytics import journey
from janasunani.analytics.journey import ROLE_CASE

__all__ = ["Sample"]

Step = tuple[str, str, str]   # (key, label, SQL predicate over {ns}g)


class Sample:
    """A base population and the funnel of steps that narrows it.

    ``base`` is a predicate over ``grievance_base``. ``flags`` adds named
    boolean columns to the sample table, so a step can say ``is_sub`` rather
    than repeating the subcategory literal. ``narrows`` lists the ``(wider,
    narrower)`` pairs the funnel must respect; by default each step narrows the
    one before it.

    ``dedup_csv`` collapses repeat filings of the same complaint to the
    earliest: ``is_primary`` is true for that one. Without it every filing is
    its own claim and ``is_primary`` is always true, so steps can use it either
    way. The mapping is derived from citizen prose and stays out of the lake
    and out of git (ROADMAP 3.2); regenerate with
    ``scripts/analysis/dedup_groups_for_slice.py``.
    """

    def __init__(self, con, *, base: str, steps: Sequence[Step], ns: str = "",
                 dedup_csv: Optional[Path] = None, flags: Optional[dict] = None,
                 narrows: Optional[Sequence[tuple[str, str]]] = None,
                 partition: Sequence[tuple[str, str]] = (),
                 partition_steps: Sequence[str] = ()) -> None:
        self.con, self.ns = con, ns
        self.deduped = dedup_csv is not None
        self._build_g(base, dedup_csv, flags or {})

        self.STEPS = {k: (label, w) for k, label, w in steps}
        self.WHERE = {k: w for k, _, w in steps}
        self.FUNNEL = {k: self.count(f"SELECT COUNT(*) FROM {self.t('g')} WHERE {w}")
                       for k, _, w in steps}

        keys = [k for k, _, _ in steps]
        # A funnel that ever grows is a funnel that has lost track of its base.
        for a, b in (narrows if narrows is not None else zip(keys, keys[1:])):
            assert self.FUNNEL[a] >= self.FUNNEL[b], (
                f"{a} ({self.FUNNEL[a]:,}) < {b} ({self.FUNNEL[b]:,})")

        # A partition must account for every row in the step it partitions, or
        # an exhibit can drop grievances without anyone noticing.
        for key in partition_steps:
            parts = sum(self.count(
                f"SELECT COUNT(*) FROM {self.t('g')} WHERE {self.WHERE[key]} AND ({p})")
                for _, p in partition)
            assert parts == self.FUNNEL[key], (
                f"{key}: parts {parts:,} != {self.FUNNEL[key]:,}")

    # -- plumbing ------------------------------------------------------------

    def t(self, table: str) -> str:
        """This sample's name for one of its tables."""
        return f"{self.ns}{table}"

    def q(self, sql: str) -> pl.DataFrame:
        return self.con.execute(sql).pl()

    def count(self, sql: str) -> int:
        return self.con.execute(sql).fetchone()[0]

    def _build_g(self, base: str, dedup_csv, flags: dict) -> None:
        groups = (pl.read_csv(Path(dedup_csv)) if self.deduped else pl.DataFrame(
            schema={"ticket_no": pl.Utf8, "duplicate_group_id": pl.Utf8,
                    "group_size": pl.Int64}))
        self.con.register(self.t("dedup_groups"), groups)
        extra = "".join(f", ({sql}) AS {name}" for name, sql in flags.items())
        self.con.execute(f"""
        CREATE OR REPLACE TABLE {self.t('g')} AS
        WITH base AS (SELECT * FROM grievance_base WHERE {base}),
        ranked AS (
            SELECT b.*, d.duplicate_group_id, d.group_size,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(d.duplicate_group_id, b.ticket_no)
                       ORDER BY b.created_on, b.ticket_no) AS filing_index
            FROM base b LEFT JOIN {self.t('dedup_groups')} d USING (ticket_no))
        SELECT *, (filing_index = 1) AS is_primary{extra}
        FROM ranked
        """)

    # -- funnel presentation -------------------------------------------------

    def note(self, key: str, extra: str = "") -> str:
        """The subtitle stamp every exhibit carries: which sample, and how big."""
        return (f"{key} · {self.FUNNEL[key]:,} — {self.STEPS[key][0]}."
                + (f" {extra}" if extra else ""))

    def funnel_table(self, keys) -> pl.DataFrame:
        keys = list(keys)
        return pl.DataFrame({
            "Step": keys,
            "Sample": [self.STEPS[k][0] for k in keys],
            "Grievances": [self.FUNNEL[k] for k in keys],
            "% of the step above": [
                None if i == 0
                else round(100 * self.FUNNEL[k] / self.FUNNEL[keys[i - 1]], 1)
                for i, k in enumerate(keys)],
        })

    # -- journey -------------------------------------------------------------

    def install_route(self, scope: str, disposed: str) -> None:
        """Per-action steps and routes over ``scope``; opens step P3.

        P3 is the claims in ``disposed`` whose dated history names an office
        the rung ladder recognises at every step.
        """
        journey.install_steps(self.con, self.WHERE[scope], ns=self.ns)
        journey.install_route(self.con, ns=self.ns)
        self.FUNNEL["P3"] = self.count(
            f"""SELECT COUNT(*) FROM {self.t('route')} r
                JOIN {self.t('g')} USING (ticket_no) WHERE {self.WHERE[disposed]}""")
        self.STEPS["P3"] = ("Dated history, every office on the rung ladder", None)
        self.WHERE["P3"] = self.WHERE[disposed]
        assert self.FUNNEL["P3"] <= self.FUNNEL[disposed], (
            "route coverage cannot exceed its own base")

    def install_phases(self, scope: str, disposed: str) -> dict:
        """The five phase spans over ``scope``; opens step P4.

        P4 is the claims whose five spans tile their own time to close. Builds
        ``{ns}phases_pop``: the phases with the route and entry point beside
        them, so an exhibit can cut the decomposition either way without
        rebuilding the join. Returns ``install_phases``' row counts.
        """
        journey.install_phase_bounds(self.con, ns=self.ns)
        chk = journey.install_phases(self.con, self.WHERE[scope], ns=self.ns)
        self.con.execute(f"""
        CREATE OR REPLACE VIEW {self.t('phases_pop')} AS
        SELECT p.*, g.entry_route, g.received_directly,
               LIST_REDUCE(LIST_TRANSFORM(r.roles, x -> {ROLE_CASE}),
                           (a, b) -> a || ' > ' || b) AS route
        FROM {self.t('phases_clean')} p JOIN {self.t('g')} g USING (ticket_no)
        LEFT JOIN {self.t('route')} r USING (ticket_no)""")
        self.FUNNEL["P4"] = chk["tiling_rows"]
        self.STEPS["P4"] = ("The five phase spans tile the total", None)
        self.WHERE["P4"] = "TRUE"
        assert self.FUNNEL["P4"] <= self.FUNNEL[disposed]
        return chk
