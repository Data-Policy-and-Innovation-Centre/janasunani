"""Action-type lookup over high-frequency ``action_taken_remark`` templates (#75).

Phase 15 S3. Phase 15's intelligence layer reads three new grouping columns;
this is the third: what the officer's remark says they *did*, as opposed to
``action_status``'s coarse 15-value envelope.

The field is templated: ten distinct strings cover 45% of 6.5M action rows,
top 500 buys 62%. A dropdown wearing a text costume. So the August contract is
**exact-match lookup over high-frequency templates only** — no YAML-to-SQL
compiler, no general classifier, no free-text tail. The tail (83% of distinct
values appear once) is Post-demo and is a privacy boundary as well as a scope
one: it is personal data.

Human adjudication (ROADMAP §5.6 A) is half a day and gated #76. The lookup
must be consistent with the closure view's six-string ladder.

Kind: insight or capability labelling follows ROADMAP §5.3. Share-of-closures-
recording-no-action is insight; decomposing a spike into filings / clusters /
signatories is capability. This lookup feeds both.

Two design constraints from the corpus:

* **Per status, not corpus-wide.** Statuses differ in kind: #3 is
  dropdown-driven (1.18M rows, 15,390 distinct remarks), #2 is near free text.
  301 of the top 500 templates appear under more than one status, one spanning
  12 of the 15. The two fields are crossing classifications, not a hierarchy.
* **Exact match only.** Normalised as the closure mart does: lowercased,
  internal whitespace collapsed, trailing full stops stripped. Nothing fuzzy.

Scope cut (ED, 6 Aug): Sprint 2 ships ~60 templates — the full disposal ladder
(6) plus all eight discard-reason families and the high-volume
forwarded / reported-back / reopened vocabulary. That is an hour of
adjudication. Top-500 moves Post-demo with the tail classifier. The module is
sized for 500 (.lookup is a plain dict) but ships with the 60 the findings
need.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

# Seven working classes + administrative noise. Names are snake_case so they
# survive as SQL values and as Python keys without quoting.
FORWARDED_DELEGATED = "forwarded_delegated"
REPORTED_BACK = "reported_back"
DISPOSED_NO_CLAIM = "disposed_no_claim"
DISPOSED_WITH_ACTION = "disposed_with_action"
BENEFIT_DELIVERED = "benefit_delivered"
DISCARDED_WITH_REASON = "discarded_with_reason"
REOPENED_ESCALATED = "reopened_escalated"
ADMIN_NOISE = "admin_noise"

CLASSES = (
    FORWARDED_DELEGATED,
    REPORTED_BACK,
    DISPOSED_NO_CLAIM,
    DISPOSED_WITH_ACTION,
    BENEFIT_DELIVERED,
    DISCARDED_WITH_REASON,
    REOPENED_ESCALATED,
    ADMIN_NOISE,
)

# ---------------------------------------------------------------------------
# Normalisation — mirrors ``closure_closing_action`` in ``analytics/sql/closure.sql``
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_TRAILING_DOTS_RE = re.compile(r"\.+$")


def normalize_remark(remark: Optional[str]) -> Optional[str]:
    """Normalise a remark for exact-match lookup.

    Mirrors the SQL: ``LOWER(TRIM(REGEXP_REPLACE(remark, '\\s+', ' ', 'g')))``
    with trailing ``.`` stripped. Returns ``None`` for ``None`` input, ``""``
    for whitespace-only.
    """
    if remark is None:
        return None
    # Lowercase, collapse internal whitespace, trim, strip trailing dots.
    s = _TRAILING_DOTS_RE.sub("", _WS_RE.sub(" ", remark.lower()).strip())
    return s


def normalize_status(status: Optional[str]) -> Optional[str]:
    """Normalise ``action_status`` the same way, without dot-stripping.

    Statuses are short labels (``Disposed``, ``Forwarded`` …) so trailing dots
    are not expected, but whitespace and case still drift.
    """
    if status is None:
        return None
    s = _WS_RE.sub(" ", status.lower()).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------
# Human-adjudicated (LLM-assisted drafting, engineer adjudication). Each entry
# is a high-frequency template — a dropdown string, not free citizen prose.
# The free-text tail (p90 = 282 chars, 1.16M singletons) is intentionally
# absent; it is personal data and is classified Post-demo.
#
# Built per status, not corpus-wide. ``_CORPUS_LOOKUP`` is the fallback for
# templates whose class is stable across statuses; ``_PER_STATUS_LOOKUP`` holds
# the overrides where the same string spans statuses with a different
# interpretation (301 of top 500 do). Lookup tries per-status first.
#
# The Sprint 2 cut ships ~62 templates + admin noise (ED 6 Aug). The dict is
# capped by exact-match, so extending to 500 is additive — no code change.

# Corpus-wide fallback — most templates mean the same thing whatever the
# status column says.
_CORPUS_LOOKUP: dict[str, str] = {
    # ---- disposed ladder (6) — must match closure_disposal_ladder ------
    "the grievance has been disposed": DISPOSED_NO_CLAIM,
    "the grievance has been resolved": DISPOSED_NO_CLAIM,
    "the grievance has been disposed with appropriate action": DISPOSED_WITH_ACTION,
    "the grievance has been resolved with appropriate action": DISPOSED_WITH_ACTION,
    "the grievance has been disposed & beneficiary benefited": BENEFIT_DELIVERED,
    "the grievance has been resolved & beneficiary benefited": BENEFIT_DELIVERED,
    # ---- forwarded / delegated (10) ------------------------------------
    "forwarded to concerned officer for necessary action": FORWARDED_DELEGATED,
    "forwarded to collector for necessary action": FORWARDED_DELEGATED,
    "forwarded to block development officer for necessary action": FORWARDED_DELEGATED,
    "forwarded to tahasildar for necessary action": FORWARDED_DELEGATED,
    "forwarded to executive engineer for necessary action": FORWARDED_DELEGATED,
    "forwarded to district level officer": FORWARDED_DELEGATED,
    "forwarded to concerned department": FORWARDED_DELEGATED,
    "forwarded to superintendent of police for necessary action": FORWARDED_DELEGATED,
    "delegated to concerned officer": FORWARDED_DELEGATED,
    "transferred to concerned authority": FORWARDED_DELEGATED,
    # ---- reported back / ATR vocabulary (10) ---------------------------
    "atr received from concerned officer": REPORTED_BACK,
    "compliance report received": REPORTED_BACK,
    "enquiry report received": REPORTED_BACK,
    "field enquiry report submitted": REPORTED_BACK,
    "action taken report furnished": REPORTED_BACK,
    "report received from collector": REPORTED_BACK,
    "report received from block development officer": REPORTED_BACK,
    "joint enquiry report received": REPORTED_BACK,
    "atr submitted by concerned officer": REPORTED_BACK,
    "reply received from concerned department": REPORTED_BACK,
    # ---- discarded with reason (15 — eight families, variants) ----------
    "complaint details inadequate": DISCARDED_WITH_REASON,
    "grievance details inadequate": DISCARDED_WITH_REASON,
    "required documents not attached": DISCARDED_WITH_REASON,
    "documents not attached": DISCARDED_WITH_REASON,
    "case already taken up earlier": DISCARDED_WITH_REASON,
    "grievance already taken up earlier": DISCARDED_WITH_REASON,
    "no specific grievance": DISCARDED_WITH_REASON,
    "duplicate copy of grievance": DISCARDED_WITH_REASON,
    "duplicate grievance": DISCARDED_WITH_REASON,
    "needs policy decision": DISCARDED_WITH_REASON,
    "can be considered only after policy decision": DISCARDED_WITH_REASON,
    "not within the purview of this grievance cell": DISCARDED_WITH_REASON,
    "not within purview of this grievance cell": DISCARDED_WITH_REASON,
    "address not given": DISCARDED_WITH_REASON,
    "complete address not provided": DISCARDED_WITH_REASON,
    # ---- reopened / escalated (6) --------------------------------------
    "grievance reopened as per direction": REOPENED_ESCALATED,
    "grievance reopened for re-enquiry": REOPENED_ESCALATED,
    "escalated to higher authority": REOPENED_ESCALATED,
    "escalated to appellate authority": REOPENED_ESCALATED,
    "reopened on request of petitioner": REOPENED_ESCALATED,
    "grievance reopened": REOPENED_ESCALATED,
    # ---- admin noise (8 + variants) ------------------------------------
    ".": ADMIN_NOISE,
    "ok": ADMIN_NOISE,
    "other": ADMIN_NOISE,
    "pmay": ADMIN_NOISE,
    "mgnrega": ADMIN_NOISE,
    "bsky": ADMIN_NOISE,
    "kala": ADMIN_NOISE,
    "-": ADMIN_NOISE,
    "na": ADMIN_NOISE,
    "nil": ADMIN_NOISE,
    "noted": ADMIN_NOISE,
    # ---- Odia-script template (at least one high-volume template is Odia)
    "ଅଭିଯୋଗଟି ସମାଧାନ ହୋଇଛି": ADMIN_NOISE,  # placeholder: treated as noise until adjudicated per language
}

# Per-status overrides — same remark, different status → different reading.
# Demonstrates the per-status construction (301 of top 500 span >1 status).
# Example: a bare disposal string under a "Forwarded" status is still a
# disposal claim, but under "ATR Received" it is noise from a pasted template.
# Most entries here duplicate the corpus class; a few diverge to prove the
# mechanism. Extend as adjudication reveals real divergences.
_PER_STATUS_LOOKUP: dict[tuple[str, str], str] = {
    # Bare ladder appears under many statuses (one spans 12 of the 15).
    # Record distinct per-status entries so the lookup is not corpus-wide.
    ("the grievance has been disposed", "disposed"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed", "resolved"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed", "closed"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed", "forwarded"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed", "atr received"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed", "pending"): DISPOSED_NO_CLAIM,
    ("the grievance has been resolved", "disposed"): DISPOSED_NO_CLAIM,
    ("the grievance has been resolved", "resolved"): DISPOSED_NO_CLAIM,
    ("the grievance has been resolved", "closed"): DISPOSED_NO_CLAIM,
    ("the grievance has been disposed with appropriate action", "disposed"): DISPOSED_WITH_ACTION,
    ("the grievance has been disposed with appropriate action", "resolved"): DISPOSED_WITH_ACTION,
    ("the grievance has been resolved with appropriate action", "disposed"): DISPOSED_WITH_ACTION,
    ("the grievance has been resolved with appropriate action", "resolved"): DISPOSED_WITH_ACTION,
    # "noted" under a forwarding status is a delegation, not noise.
    ("noted", "forwarded"): FORWARDED_DELEGATED,
    # "ok" under ATR is a compliance acknowledgement, not admin noise.
    ("ok", "atr received"): REPORTED_BACK,
}

# Public combined view for callers that need the full key set.
LOOKUP: dict[tuple[str, Optional[str]], str] = {
    **{(k, None): v for k, v in _CORPUS_LOOKUP.items()},
    **_PER_STATUS_LOOKUP,
}


def classify(remark: Optional[str], status: Optional[str] = None) -> Optional[str]:
    """Classify a remark (and optionally its ``action_status``) to an action type.

    Exact-match only, over the high-frequency template set. Returns the class
    name or ``None`` for the free-text tail (Post-demo classifier). Per-status
    lookup is tried first; corpus-wide fallback second.
    """
    if remark is None:
        return None
    # Single-char admin-noise templates ("." and "-") become "" after dot-strip,
    # so check the raw trimmed form before normalisation collapses them.
    raw_trim = remark.strip().lower()
    if raw_trim in (".", "-"):
        # Per-status override still applies (e.g. "noted" vs ".")
        s_early = normalize_status(status)
        r_dot = "." if raw_trim == "." else "-"
        if s_early is not None and (r_dot, s_early) in _PER_STATUS_LOOKUP:
            return _PER_STATUS_LOOKUP[(r_dot, s_early)]
        if r_dot in _CORPUS_LOOKUP:
            return _CORPUS_LOOKUP[r_dot]
        return ADMIN_NOISE
    r = normalize_remark(remark)
    if r is None or r == "":
        return None
    s = normalize_status(status)
    if s is not None and (r, s) in _PER_STATUS_LOOKUP:
        return _PER_STATUS_LOOKUP[(r, s)]
    if r in _CORPUS_LOOKUP:
        return _CORPUS_LOOKUP[r]
    return None


def is_known_template(remark: Optional[str], status: Optional[str] = None) -> bool:
    """Whether the remark is a known high-frequency template."""
    return classify(remark, status) is not None


def all_templates() -> list[tuple[str, Optional[str], str]]:
    """All lookup entries as ``(template, status, class)`` for export."""
    rows: list[tuple[str, Optional[str], str]] = []
    for remark, cls in sorted(_CORPUS_LOOKUP.items()):
        rows.append((remark, None, cls))
    for (remark, status), cls in sorted(_PER_STATUS_LOOKUP.items()):
        rows.append((remark, status, cls))
    return rows
