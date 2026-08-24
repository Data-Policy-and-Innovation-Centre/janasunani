"""Three-state outcome: S (actionable) and C (action taken).

Definition 2.5 of `docs/experiments/routing-outcome-model.tex`. `S` is a
property of the grievance and does not vary with the flow, so it may be
conditioned on. `C` is moved by routing, so it is an outcome to be constrained
and never a conditioning event. The binary `correct` label in `dataset.py`
collapses the two and scores the correct closure of a duplicate as a failure.

WHAT DECIDES A TEMPLATE'S BUCKET
--------------------------------
Not the wording. The wording is ambiguous in exactly the cases that matter, so
the assignment rests on two structural facts measured over all 1,209,144
resolved grievances (13 Aug 2026 census, `scripts/` reconnaissance in the PR):

    bucket        chain length   median days
    discards        1.01-1.17         1-6
    handled         2.18-2.90        42-138

The separation is total; nothing high-volume sits between. A case that passed
through one officer and closed in two days was screened out. A case that passed
through two or three and closed in seventy was worked. Every assignment below
that is not a literal ladder template was checked against that profile, and the
profile is recorded beside it.

WHAT THE FIELD RECORD DID AND DID NOT SETTLE
--------------------------------------------
`docs/Janasunani_Canonical_Questions_14Aug_Demo.docx` Figure B.14 photographs
the closure dropdown in the CM Grievance Cell. It lists three templates that
`LADDER_SQL` does not match, which raised the hypothesis that `as reported` is a
truncation of the longer benefit-claiming template beside it in the list.

**That hypothesis is refuted.** Both longer strings occur fewer than 1,000 times
in the whole corpus; they are effectively absent. `as reported` cannot be read as
their truncation on the evidence of co-occurrence, and is not treated as a
benefit claim here.

What survives is stronger and comes from our own data rather than one office's
screen: `as reported` is 90,061 closures (8.61% of resolved), present in six of
seven intake offices, 12.68% of Collector closures and 19.66% of Chief
Secretary's. It is system-wide, and it profiles as handled (chain 2.18, median
78 days -- the longest median of any high-volume template). So the case was
worked; whether substantive action followed is precisely what the remark
declines to say. It is `S=1` with `C` unknown, which is a fourth bucket the
binary label has nowhere to put.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from janasunani.analytics.findings.discards import TEMPLATES as DISCARD_TEMPLATES

#: `S=0`: no action was available to any officer. `S=1,C=1`: substantive action.
#: `S=1,C=0`: actionable, none taken. `S=1,C=?`: worked, outcome not recorded.
#: `unknown`: free text; `S` undetermined and the row is excluded from both.
Bucket = Literal["s0", "s1_c1", "s1_c0", "s1_c_unknown", "unknown"]


@dataclass(frozen=True)
class Assignment:
    """One template's bucket, with the evidence that put it there."""

    bucket: Bucket
    source: str
    note: str = ""


#: The six-template disposal ladder. Unchanged from `dataset.py:LADDER_SQL`;
#: reproduced here so the ladder and the off-ladder map are one lookup.
#:
#: `C` comes from the rung alone. The binary `correct` in `dataset.py` also
#: counts `benefitted LIKE '%yes%'` on any rung, which adds about 59,000 cases
#: -- and the flag does not deserve that authority. Measured over the corpus it
#: runs *backwards* against the ladder: 9.2% of bare closures carry it against
#: 6.2% of `with appropriate action` ones. It only behaves on the two explicit
#: benefit rungs (70.5%). A signal that disagrees with the ladder on the ladder's
#: own terms cannot be used to overrule it, so it is dropped from the definition
#: and kept as a covariate. `s1_c1` here is 304,140, which is exactly the
#: independently reported count of action-rung closures in `docs/DELIVERY.md`.
LADDER: dict[str, Assignment] = {
    "the grievance has been disposed": Assignment("s1_c0", "ladder", "bare rung"),
    "the grievance has been resolved": Assignment("s1_c0", "ladder", "bare rung"),
    "the grievance has been disposed with appropriate action": Assignment(
        "s1_c1", "ladder", "action rung"
    ),
    "the grievance has been resolved with appropriate action": Assignment(
        "s1_c1", "ladder", "action rung"
    ),
    "the grievance has been disposed & beneficiary benefited": Assignment(
        "s1_c1", "ladder", "benefit rung"
    ),
    "the grievance has been resolved & beneficiary benefited": Assignment(
        "s1_c1", "ladder", "benefit rung"
    ),
}

#: Discard families already governed in `janasunani/analytics/findings/discards.py`.
#: Every one profiles in the discard cluster (chain ~1.0, median 1-6 days).
#:
#: Two of them -- `duplicate_copy` and `case_already_taken_up` -- carry
#: `WeakLabel(None, eligible_for_training=False)` in
#: `janasunani/evaluation/actionability.py`, because for the actionability
#: classifier the duplicate signal belongs to the dedup task. That exclusion is
#: right there and wrong here: a duplicate is the canonical `S=0` case, and
#: §2.3.2 leads with it. The divergence is deliberate.
#: Every family in that module is `S=0`; the mapping is applied in
#: `_assignments()` rather than restated here.

#: Templates absent from the governed families, added here on measured
#: frequency. Sources are the 13 Aug census; Figure B.16 corroborates the first
#: four but is one office's login and did not decide any of them.
LOCAL_S0_TEMPLATES: dict[str, Assignment] = {
    "thanks for the suggestions": Assignment(
        "s0", "census n=4,769; Figure B.16", "chain 1.03, median 3d -- a suggestion, not a grievance"
    ),
    "will be considered as per rule in due course of time": Assignment(
        "s0", "census n=3,917; Figure B.16", "chain 1.07, median 1d -- no action available now"
    ),
    "complaint details not legible": Assignment(
        "s0", "census n=2,110; Figure B.16", "chain 1.16, median 2d -- cannot be read"
    ),
    "advised to go through the due recruitment process": Assignment(
        "s0", "census n=1,432", "chain 1.07, median 2d -- redirected to a separate process"
    ),
    "you are requested to send your grievance/petition directly to vigilance organisation"
    " for redressal of your grievance": Assignment(
        "s0", "census n=1,300", "chain 1.00, median 2d -- outside this cell's jurisdiction"
    ),
    # Below the 1,000 floor, and included only because Figure B.16 shows both as
    # entries in the discard dropdown. This is the field record doing the one job
    # it can do here: corroborating a template the census already found, rather
    # than defining the set.
    "anonymous": Assignment(
        "s0", "census n=994; Figure B.16", "chain 1.23, median 2d -- no petitioner to act for"
    ),
    "cannot be considered beyond rule": Assignment(
        "s0", "census n=739; Figure B.16", "chain 1.45, median 15d -- no action permitted"
    ),
}

#: Off-ladder templates that profile as *handled* rather than screened out, so
#: they are `S=1`. The rung they belong on is a separate question and the
#: remark does not always answer it.
LOCAL_S1_TEMPLATES: dict[str, Assignment] = {
    "resolved": Assignment(
        "s1_c0", "census n=4,590", "chain 2.90, median 138d -- a bare rung the ladder misses"
    ),
    "as reported": Assignment(
        "s1_c_unknown",
        "census n=90,061 (8.61% of resolved)",
        "chain 2.18, median 78d. Worked, but the remark relays a subordinate's "
        "report without claiming an outcome. See the module docstring: the "
        "Figure B.14 truncation hypothesis is refuted.",
    ),
    "the grievance has been kept in priority category and shall be taken up after due"
    " government approval": Assignment(
        "s1_c0", "census n=1,094", "chain 2.07, median 36d -- explicitly deferred, no action yet"
    ),
}

#: The exact text of the longest template, kept out of the map above only
#: because it is 357 characters. It is a standing Odia notice that the PMAY-G
#: beneficiary survey is open and how to enrol.
PMAY_SURVEY_NOTICE = (
    "ପ୍ରଧାନମନ୍ତ୍ରୀ ଆବାସ ଯୋଜନା (ଗ୍ରାମୀଣ) ରେ ନୂତନ ହିତାଧିକାରୀ ଚୟନ ନିମନ୍ତେ ବର୍ତ୍ତମାନ ସର୍ଭେ ଚାଲୁଅଛି i "
    "ଆଶାୟୀ ପରିବାର awaasplus2024 ମୋବାଇଲ ଆପ୍ ଜରିଆରେ ନିଜେ କିମ୍ବା ବ୍ଲକ ଅଧିକାରୀଙ୍କ ସହାୟତାରେ ନିଜ ନାମ "
    "ସର୍ଭେ ତାଲିକାଭୁକ୍ତ କରିପାରିବେ i ବିସ୍ତୃତ ସୂଚନା https://pmayg.nic.in / "
    "https://www.rhodisha.gov.in ରେ ଉପଲବ୍ଧ i ତାଲିକାଭୁକ୍ତ ପରିବାରଙ୍କୁ ଯୋଗ୍ୟତା ମାନଦଣ୍ଡ ଅନୁଯାୟୀ "
    "ପକ୍କା ଘର ମଞ୍ଜୁର ହେବ i"
)

#: Templates that are genuinely undecided and must not be guessed. They are
#: excluded from both the `S` conditioning set and the `C` constraint, and their
#: share is reported.
DEFERRED_TEMPLATES: dict[str, Assignment] = {
    "advised to place the grievance for house before the collector in joint hearing of"
    " grievances on monday": Assignment(
        "unknown",
        "census n=10,999",
        "chain 1.01, median 4d -- profiles as a screen-out, but it is a referral "
        "onward rather than a refusal. Whether the citizen was helped depends on "
        "what happened at the hearing, which is not in this record. Needs officer "
        "adjudication.",
    ),
    "other": Assignment(
        "unknown", "census n=2,434", "chain 1.51, median 6d -- literally uninformative"
    ),
    PMAY_SURVEY_NOTICE: Assignment(
        "unknown",
        "census n=3,589",
        "chain 3.96, median 144d -- the most-handled template in the corpus, and a "
        "form notice that grants nothing. Whether telling a petitioner the survey "
        "is open counts as substantive action is a policy question, not a reading "
        "of the text. Deferred rather than guessed.",
    ),
}


def _assignments() -> dict[str, Assignment]:
    merged: dict[str, Assignment] = dict(LADDER)
    for family, templates in DISCARD_TEMPLATES.items():
        for template in templates:
            merged[template] = Assignment("s0", f"discards.py:{family}")
    merged.update(LOCAL_S0_TEMPLATES)
    merged.update(LOCAL_S1_TEMPLATES)
    merged.update(DEFERRED_TEMPLATES)
    return merged


#: The single lookup. Later dicts intentionally override earlier ones only where
#: no key collides; a collision would be a genuine disagreement and is caught by
#: `test_no_template_is_assigned_twice`.
ASSIGNMENTS: dict[str, Assignment] = _assignments()


def classify(remark: str | None) -> Bucket:
    """Bucket for an already-normalised closing remark.

    Normalisation is `LADDER_SQL`'s: lowercase, collapse whitespace, strip
    trailing full stops. An unrecognised remark is `unknown`, never a default
    bucket -- guessing here is how a free-text tail silently becomes evidence.
    """
    if remark is None:
        return "unknown"
    assignment = ASSIGNMENTS.get(remark.strip())
    return assignment.bucket if assignment else "unknown"


def sql_case(column: str = "normalized_remark", alias: str = "s_bucket") -> str:
    """The same lookup as a DuckDB CASE, so the mart cannot drift from `classify`."""
    lines = [f"    CASE {column}"]
    for template, assignment in sorted(ASSIGNMENTS.items()):
        escaped = template.replace("'", "''")
        lines.append(f"        WHEN '{escaped}' THEN '{assignment.bucket}'")
    lines.append("        ELSE 'unknown'")
    lines.append(f"    END AS {alias}")
    return "\n".join(lines)


def bucket_to_s_c(bucket: Bucket) -> tuple[int | None, int | None]:
    """`(S, C)` for a bucket. `None` means not determined, never zero."""
    return {
        "s0": (0, None),
        "s1_c1": (1, 1),
        "s1_c0": (1, 0),
        "s1_c_unknown": (1, None),
        "unknown": (None, None),
    }[bucket]
