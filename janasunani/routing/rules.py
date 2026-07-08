"""Deterministic category/district routing rules.

This is the demo-sufficient Phase 9 backbone: category/subcategory plus an
optional district maps to the existing ``RoutingResult`` API shape. It does not
read the proprietary ``data/`` mappings; those can replace ``DEFAULT_RULES``
later once a specific path is approved for that task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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


DEFAULT_ROUTER = RuleRouter()
