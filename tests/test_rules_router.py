from janasunani.routing.rules import (
    FALLBACK_DEPT,
    RouteRule,
    RuleRouter,
)


def test_rules_router_exact_subcategory_match():
    router = RuleRouter()

    result = router.route(
        category="Drinking Water Supply",
        subcategory="Hand pump repair",
        district="Cuttack",
    )

    assert result.method == "rules"
    assert result.dept == "Rural Water Supply & Sanitation"
    assert result.designation == "Block Development Officer"
    assert result.office == "Office of the Collector, Cuttack"
    assert result.escalation_authority == "District Magistrate, Cuttack"
    assert result.confidence == 0.9


def test_rules_router_known_category_without_subcategory_uses_category_rule():
    router = RuleRouter(
        (
            RouteRule(
                category="Water Supply",
                subcategory="Hand pump repair",
                dept="Specific Dept",
                designation="Specific Officer",
            ),
            RouteRule(
                category="Water Supply",
                dept="General Water Dept",
                designation="General Officer",
            ),
        )
    )

    result = router.route(category="Water Supply", district="Puri")

    assert result.method == "rules"
    assert result.dept == "General Water Dept"
    assert result.designation == "General Officer"
    assert result.confidence == 0.8


def test_rules_router_missing_district_uses_state_scope():
    result = RuleRouter().route(category="Electricity")

    assert result.method == "rules"
    assert result.office == "Office of the Collector, State"
    assert result.escalation_authority == "District Magistrate, State"


def test_rules_router_unknown_category_falls_back():
    result = RuleRouter().route(
        category="Unmapped Category",
        subcategory="Unmapped Subcategory",
        district="Ganjam",
    )

    assert result.method == "fallback"
    assert result.dept == FALLBACK_DEPT
    assert result.office == "Public Grievance Cell, Ganjam"
    assert result.confidence == 0.25
