"""Pinned Sarvam Extract schema — frozen before any benchmark output is read.

This module is the single source for the JSON schema passed to
``SarvamVisionAdapter.extract(schema=...)`` in the benchmark. The schema is
versioned so a later change cannot silently shift the headline category
accuracy number; callers pin ``--schema-version v1`` and the scorecard
records the version.

The illustrative schema in the rehearsal plan (Part 5) is reproduced
verbatim here as ``GRIEVANCE_EXTRACT_SCHEMA_V1``. Field names are aligned
to the OLTP ``complaints`` taxonomy (``category`` / ``grievance_category``)
and to the pipeline ``documents`` table so the benchmark's category
comparison has a stable referee.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "v1"
SCHEMA_VERSION_V1 = "v1"

GRIEVANCE_EXTRACT_SCHEMA_V1: dict[str, dict[str, str]] = {
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
