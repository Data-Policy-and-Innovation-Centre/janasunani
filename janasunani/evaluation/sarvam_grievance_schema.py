"""Pinned Sarvam Extract schema — frozen before any benchmark output is read.

This module is the single source for the JSON schema passed to
``SarvamVisionAdapter.extract(schema=...)`` in the benchmark. The schema is
versioned so a later change cannot silently shift the headline category
accuracy number; callers pin ``--schema-version v1`` and the scorecard
records the version.

The illustrative schema in the rehearsal plan (Part 5) is reproduced
verbatim here as ``GRIEVANCE_EXTRACT_FIELDS_V1``. Field names are aligned
to the OLTP ``complaints`` taxonomy (``category`` / ``grievance_category``)
and to the pipeline ``documents`` table so the benchmark's category
comparison has a stable referee.

⚠️ The value handed to ``extract(schema=...)`` must be a **whole JSON Schema
document**, not the bare field map. Sarvam requires a root of
``{"type": "object", "properties": {...}}`` and rejects anything else with
HTTP 400. Sending the bare map is what broke every Extract submission until
2026-08-09: digitise succeeded on the same pages while extract failed 5 of 5,
so the arm carrying the only real ground truth (recorded category) produced
nothing. Every test mocked the transport, so no test could see the 400.
``GRIEVANCE_EXTRACT_SCHEMA_V1`` is therefore the wrapped document, and the
field map is exported separately for readability.
"""

from __future__ import annotations

from typing import Any

#: v2 is the default because v1 cannot answer its own declared primary
#: outcome: its category field has no enum, so the 2026-08-25 run billed 200
#: pages and returned free-text subject lines. v1 stays reachable so that run
#: can be reproduced exactly; nothing else should select it.
SCHEMA_VERSION = "v2"
SCHEMA_VERSION_V1 = "v1"

#: The field map. Not sendable on its own — see ``GRIEVANCE_EXTRACT_SCHEMA_V1``.
GRIEVANCE_EXTRACT_FIELDS_V1: dict[str, dict[str, str]] = {
    "grievance_category": {
        "type": "string",
        "description": "ROS grievance category label (taxonomy of complaints.grievance_category / complaints.category)",
    },
    "summary": {
        "type": "string",
        "description": "One-paragraph grievance summary",
    },
    "district": {
        "type": "string",
        "description": "District name",
    },
    "grievance_text": {
        "type": "string",
        "description": "Full grievance text (redacted prose)",
    },
}

#: The document actually sent to Sarvam Extract.
GRIEVANCE_EXTRACT_SCHEMA_V1: dict[str, Any] = {
    "type": "object",
    "properties": GRIEVANCE_EXTRACT_FIELDS_V1,
}

SCHEMA_VERSION_V2 = "v2"

#: The 35 labels ``complaints.category`` actually takes, read off the lake on
#: 2026-08-25. ``Scheme & Benefits`` is stored double-escaped as
#: ``Scheme &amp;amp; Benefits``; the enum carries the readable form and the
#: comparison unescapes before matching, because asking a model to emit an
#: HTML entity would be absurd and would fail for the wrong reason.
GRIEVANCE_CATEGORIES: tuple[str, ...] = (
    "Accident",
    "Agriculture & Farming",
    "BSKY",
    "CMRF",
    "COVID-19",
    "Culture",
    "Disaster Management",
    "Education",
    "Energy",
    "Environment",
    "Excise",
    "Financial Assistance",
    "General",
    "Health Care",
    "Housing",
    "ICDS",
    "Infrastructure",
    "Irrigation",
    "Land Matters",
    "Legal",
    "Miscellaneous",
    "Pension/Retirement Benefits",
    "Police Case",
    "Public Utility",
    "Scheme & Benefits",
    "School & College",
    "Service Matters",
    "Social Welfare",
    "Sports",
    "Tourism",
    "Traffic",
    "Transport",
    "Waste Management",
    "Water Supply",
    "Women Issues",
)

#: v2 field map. Two changes, both forced by the 2026-08-25 run.
#:
#: ``grievance_category`` gains an ``enum``. v1 described it as the
#: "taxonomy of complaints.grievance_category" and then never said what that
#: taxonomy was, so the model had nothing to choose from and answered with
#: free-text subject lines — the specific relief each petitioner asked for, or
#: the name of an administrative wing — none of which is a value the taxonomy
#: takes. It matched gold on 0 of the 11 grievances where it answered at all,
#: which measured our schema rather than the provider.
#:
#: ``district`` is dropped. It is a structured column on ``complaints`` that is
#: recorded at intake and known with certainty for every grievance, so paying
#: an extraction endpoint to read it back off a scan buys nothing. It was also
#: the best-populated field in the run, 100 of 198 pages against the category's
#: 11, because it sits on the letterhead: the budget went to the one field we
#: already had. Reinstate it only as an explicit intake-accuracy cross-check,
#: and say so if you do.
GRIEVANCE_EXTRACT_FIELDS_V2: dict[str, dict[str, Any]] = {
    "grievance_category": {
        "type": "string",
        "enum": list(GRIEVANCE_CATEGORIES),
        "description": (
            "The single grievance category. Choose exactly one value from enum."
        ),
    },
    "summary": {
        "type": "string",
        "description": "One-paragraph grievance summary",
    },
    "grievance_text": {
        "type": "string",
        "description": "Full grievance text (redacted prose)",
    },
}

GRIEVANCE_EXTRACT_SCHEMA_V2: dict[str, Any] = {
    "type": "object",
    "properties": GRIEVANCE_EXTRACT_FIELDS_V2,
}

SUPPORTED_SCHEMA_VERSIONS: dict[str, dict[str, Any]] = {
    "v1": GRIEVANCE_EXTRACT_SCHEMA_V1,
    "v2": GRIEVANCE_EXTRACT_SCHEMA_V2,
}


def get_schema(version: str = SCHEMA_VERSION) -> dict[str, Any]:
    """Return the pinned schema for *version*.

    Raises ``ValueError`` for an unknown version so a typo cannot silently
    fall back to a different schema.
    """
    try:
        return SUPPORTED_SCHEMA_VERSIONS[version]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ValueError(f"unknown schema version {version!r}; supported: {supported}") from exc
