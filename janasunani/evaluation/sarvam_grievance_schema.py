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

SCHEMA_VERSION = "v1"
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

SUPPORTED_SCHEMA_VERSIONS: dict[str, dict[str, Any]] = {
    "v1": GRIEVANCE_EXTRACT_SCHEMA_V1,
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
