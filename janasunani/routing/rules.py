"""Deterministic category/district routing rules.

This is the demo-sufficient Phase 9 backbone: category/subcategory plus an
optional district maps to the existing ``RoutingResult`` API shape.

Two layers live here:

* ``RuleRouter`` / ``DEFAULT_RULES`` -- the original illustrative, hand-written
  rules. Kept as-is (tests depend on the exact fixtures) and used as the
  fallback layer described below.
* ``MappingRouter`` -- sources routing from the real ORTPSA master tables in
  ``data/raw/janasunani-mappings/`` (via ``janasunani.routing.mappings``),
  falling back to ``RuleRouter`` when the CSVs are unavailable or a category
  has no derivable department, and to the shared generic fallback when
  neither layer matches. ``DEFAULT_ROUTER`` is a ``MappingRouter``, so the
  demo is now steered off real master data as far as it reliably reaches, per
  Phase 9 -- see ``janasunani.routing.mappings`` for exactly what closes and
  what doesn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

from janasunani.routing.mappings import (
    MappingTables,
    _GENERIC_OFFICE_NAME,
    load_mapping_tables,
)
from janasunani.serving.schemas import RoutingResult


@dataclass(frozen=True)
class RouteInput:
    category: str
    subcategory: Optional[str] = None
    district: Optional[str] = None


@dataclass(frozen=True)
class RouteRule:
    category: str
    dept: str
    designation: str
    office_template: str = "Office of the Collector, {district}"
    escalation_template: str = "District Magistrate, {district}"
    subcategory: Optional[str] = None


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


def _district(value: Optional[str]) -> str:
    return " ".join((value or "State").split())


DEFAULT_RULES: tuple[RouteRule, ...] = (
    RouteRule(
        category="Drinking Water Supply",
        subcategory="Hand pump repair",
        dept="Rural Water Supply & Sanitation",
        designation="Block Development Officer",
    ),
    RouteRule(
        category="Water Supply",
        dept="Rural Water Supply & Sanitation",
        designation="Block Development Officer",
    ),
    RouteRule(
        category="Electricity",
        dept="Energy",
        designation="Executive Engineer",
    ),
    RouteRule(
        category="Roads & Bridges",
        dept="Works",
        designation="Executive Engineer",
    ),
    RouteRule(
        category="Public Health",
        dept="Health & Family Welfare",
        designation="Chief District Medical Officer",
    ),
    RouteRule(
        category="Land & Revenue",
        dept="Revenue & Disaster Management",
        designation="Tahasildar",
    ),
    RouteRule(
        category="Certificates",
        dept="Revenue & Disaster Management",
        designation="Tahasildar",
    ),
)

FALLBACK_DEPT = "General Administration & Public Grievance"
FALLBACK_DESIGNATION = "Public Grievance Officer"


class RuleRouter:
    """Route from normalized category/subcategory strings to a public office."""

    def __init__(self, rules: tuple[RouteRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult:
        district_name = _district(district)
        rule = self._match(category=category, subcategory=subcategory)
        if rule is None:
            return RoutingResult(
                dept=FALLBACK_DEPT,
                office=f"Public Grievance Cell, {district_name}",
                designation=FALLBACK_DESIGNATION,
                escalation_authority=f"Collectorate, {district_name}",
                confidence=0.25,
                method="fallback",
            )

        exact_subcategory = bool(rule.subcategory and _norm(rule.subcategory) == _norm(subcategory))
        return RoutingResult(
            dept=rule.dept,
            office=rule.office_template.format(district=district_name),
            designation=rule.designation,
            escalation_authority=rule.escalation_template.format(
                district=district_name
            ),
            confidence=0.9 if exact_subcategory else 0.8,
            method="rules",
        )

    def route_input(self, route_input: RouteInput) -> RoutingResult:
        return self.route(
            category=route_input.category,
            subcategory=route_input.subcategory,
            district=route_input.district,
        )

    def _match(
        self, *, category: str, subcategory: Optional[str] = None
    ) -> Optional[RouteRule]:
        category_key = _norm(category)
        subcategory_key = _norm(subcategory)
        category_matches = [
            rule for rule in self._rules if _norm(rule.category) == category_key
        ]
        for rule in category_matches:
            if rule.subcategory and _norm(rule.subcategory) == subcategory_key:
                return rule
        for rule in category_matches:
            if rule.subcategory is None:
                return rule
        return category_matches[0] if category_matches else None


class MappingRouter:
    """Routes off the real ORTPSA master tables, falling back gracefully.

    Resolution order:

    1. Real category -> department -> escalation chain, derived from the
       master CSVs (see ``janasunani.routing.mappings`` for the join path and
       its documented gaps). Hit -> ``method="rules"``, dept/designation/
       escalation authority are real values from the tables.
    2. The illustrative ``RuleRouter``/``DEFAULT_RULES`` layer (used whenever
       the CSVs are unavailable, or the category text isn't a real category,
       or it is a real category with no derivable department -- the common
       case today, since no category->department FK exists in the source
       data). Hit -> ``method="rules"``.
    3. The shared generic fallback (``method="fallback"``), via ``RuleRouter``.

    The CSVs are loaded once at construction. The module-level
    ``DEFAULT_ROUTER`` defers that construction until its first route so
    import-only API and test paths do not probe optional local artifacts.
    """

    def __init__(
        self,
        mapping_dir: Optional[Path] = None,
        tables: Optional[MappingTables] = None,
        legacy_router: Optional[RuleRouter] = None,
    ) -> None:
        self._tables = tables if tables is not None else load_mapping_tables(mapping_dir)
        self._legacy = legacy_router if legacy_router is not None else RuleRouter()

    @property
    def mapping_loaded(self) -> bool:
        """Whether the real master-table CSVs were found and parsed."""
        return self._tables is not None

    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult:
        if self._tables is not None:
            resolved = self._tables.resolve(category)
            if resolved is not None:
                department, chain = resolved
                district_name = _district(district)
                if chain is not None:
                    designation = chain.designations[0]
                    escalation_authority = chain.designations[-1]
                    confidence = 0.9
                else:
                    designation = None
                    escalation_authority = None
                    confidence = 0.75
                office_label = (
                    chain.office_name
                    if chain is not None
                    and chain.office_name
                    and chain.office_name != _GENERIC_OFFICE_NAME
                    else f"{department.name_en} Department"
                )
                return RoutingResult(
                    dept=department.name_en,
                    office=f"{office_label}, {district_name}",
                    designation=designation,
                    escalation_authority=escalation_authority,
                    confidence=confidence,
                    method="rules",
                )
        return self._legacy.route(
            category=category, subcategory=subcategory, district=district
        )

    def route_input(self, route_input: RouteInput) -> RoutingResult:
        return self.route(
            category=route_input.category,
            subcategory=route_input.subcategory,
            district=route_input.district,
        )


class _LazyDefaultRouter:
    """Construct the CSV-backed demo router and optional empirical crosswalk.

    The live default deliberately leaves ``enable_crosswalk`` false. The
    empirical artifact describes historic dispatch rather than validated
    routing correctness, and the routing gold set does not yet exist. This
    preserves the committed demo behaviour (mapping/rules/fallback) while the
    artifact and its synthetic-path tests can mature independently. Enabling
    it requires an explicit code/configuration change made alongside routing
    gold validation; it must never happen merely because an artifact appears
    on disk.

    When explicitly enabled for a validated deployment, order is crosswalk,
    then mapping tables, then the generic fallback inside ``RuleRouter``. The
    crosswalk goes first because it is the only layer with an empirical
    estimate and the only one that covers categories the masters cannot bridge
    at all -- ``intCategoryGrp`` is NULL on every category, so name equality
    is all ``MappingRouter`` has.

    A missing or unreadable crosswalk artifact yields ``None`` and this falls
    straight through, which is #33's stated degradation: routing reverts to the
    placeholder and nothing else in the demo is affected.
    """

    def __init__(self, *, enable_crosswalk: bool = False) -> None:
        self._enable_crosswalk = enable_crosswalk
        self._router: MappingRouter | None = None
        self._crosswalk = None
        self._crosswalk_loaded = False
        self._lock = Lock()

    def _get(self) -> MappingRouter:
        if self._router is None:
            with self._lock:
                if self._router is None:
                    self._router = MappingRouter()
        return self._router

    def _get_crosswalk(self):
        if not self._crosswalk_loaded:
            with self._lock:
                if not self._crosswalk_loaded:
                    from janasunani.routing.crosswalk import load_crosswalk

                    self._crosswalk = load_crosswalk()
                    self._crosswalk_loaded = True
        return self._crosswalk

    def route(
        self,
        *,
        category: str,
        subcategory: Optional[str] = None,
        district: Optional[str] = None,
    ) -> RoutingResult:
        crosswalk = self._get_crosswalk() if self._enable_crosswalk else None
        if crosswalk is not None:
            hit = crosswalk.lookup(category, subcategory, district)
            if hit is not None:
                district_name = _district(district)
                # `hit.office` is the department's actual office, jointly
                # aggregated with dept (crosswalk.OFFICE_MIN_SHARE) -- not
                # asserted. When history is too fragmented across offices to
                # name one, `hit.office` is None and the label names the
                # department only, same pattern MappingRouter uses when its
                # escalation chain carries no specific office.
                office = (
                    f"{hit.office}, {district_name}"
                    if hit.office
                    else f"{hit.dept} Department, {district_name}"
                )
                return RoutingResult(
                    dept=hit.dept,
                    office=office,
                    designation=None,
                    escalation_authority=f"District Magistrate, {district_name}",
                    confidence=hit.confidence,
                    # "learned" rather than "rules": this came from what the
                    # record shows, not from a hand-written table.
                    method="learned",
                )
        return self._get().route(
            category=category,
            subcategory=subcategory,
            district=district,
        )

    def route_input(self, route_input: RouteInput) -> RoutingResult:
        return self.route(
            category=route_input.category,
            subcategory=route_input.subcategory,
            district=route_input.district,
        )


DEFAULT_ROUTER = _LazyDefaultRouter()
