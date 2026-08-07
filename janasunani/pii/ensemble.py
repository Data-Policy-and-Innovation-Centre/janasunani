"""Ensemble labelling helpers for PII gold (issue #15).

Amendment 2026-08-05 replaces the human-only pass with an ensemble that
spans vendors. Each member labels independently and zero-shot (no Presidio
draft, no sight of other members). This module provides the pure helpers
that the protocol needs; it does not call any model.

- union_spans: recall-favouring union over members (the §5.1 false-negative
  priority).
- agreement_report: unanimous vs contested, per-entity, with inter-model
  agreement Y (overall and per-entity) — the confidence signal.
- adjudication_queue: contested spans only, with context.
- human_verification_sample: 15-20 page random sample to bound the
  all-missed rate (the one direction union cannot self-check).

Spans are dicts with keys {start, end, entity} plus optional extra keys
(text, page_id). start inclusive, end exclusive, character offsets.
Members are dicts of {model: str, vendor: str, spans: list[span]}.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass


Span = dict
MemberSpans = dict


def _span_key(s: Span) -> tuple[int, int, str]:
    return (int(s["start"]), int(s["end"]), str(s["entity"]).upper())


def _overlap(a: Span, b: Span) -> bool:
    # Any overlap, irrespective of entity — for agreement counting the entity
    # matters, but for queue surfacing the geometry matters.
    return not (a["end"] <= b["start"] or b["end"] <= a["start"])


def union_spans(members: list[MemberSpans]) -> list[Span]:
    """Recall-favouring union over members.

    De-duplicates exact (start,end,entity) triples across members; does not
    merge overlapping but non-identical spans — those are the disagreement
    signal and must surface as contested rather than be silently merged.
    """
    seen: set[tuple[int, int, str]] = set()
    out: list[Span] = []
    for m in members:
        for s in m.get("spans", []):
            k = _span_key(s)
            if k not in seen:
                seen.add(k)
                out.append({"start": k[0], "end": k[1], "entity": k[2]})
    out.sort(key=lambda x: (x["start"], x["end"], x["entity"]))
    return out


@dataclass(frozen=True)
class AgreementReport:
    n_members: int
    vendors: list[str]
    total_union: int
    unanimous: int
    contested: int
    agreement_rate: float
    per_entity: dict[str, dict[str, int | float]]
    by_span: list[dict]


def agreement_report(members: list[MemberSpans]) -> AgreementReport:
    """Inter-model agreement over the union.

    Y is overall unanimous / union. Per-entity breakdown is also reported
    because NAME is the weak spot on English and the blind spot on Odia.
    """
    n = len(members)
    vendors = sorted({str(m.get("vendor", "unknown")) for m in members})
    union = union_spans(members)
    # For each union span, count how many members had that exact triple
    exact_counts: dict[tuple[int, int, str], int] = Counter()
    for m in members:
        member_keys = {_span_key(s) for s in m.get("spans", [])}
        for k in member_keys:
            exact_counts[k] += 1
    unanimous = sum(1 for v in exact_counts.values() if v == n)
    contested = len(union) - unanimous
    rate = unanimous / len(union) if union else 0.0

    # Per-entity
    per: dict[str, Counter] = defaultdict(Counter)
    for s in union:
        ent = s["entity"]
        cnt = exact_counts[_span_key(s)]
        per[ent]["total"] += 1
        if cnt == n:
            per[ent]["unanimous"] += 1
        else:
            per[ent]["contested"] += 1
    per_entity = {
        ent: {
            "total": int(c["total"]),
            "unanimous": int(c["unanimous"]),
            "contested": int(c["contested"]),
            "agreement_rate": (c["unanimous"] / c["total"]) if c["total"] else 0.0,
        }
        for ent, c in per.items()
    }

    by_span = [
        {
            "start": s["start"],
            "end": s["end"],
            "entity": s["entity"],
            "members_agreeing": int(exact_counts[_span_key(s)]),
            "unanimous": exact_counts[_span_key(s)] == n,
        }
        for s in union
    ]
    return AgreementReport(
        n_members=n,
        vendors=vendors,
        total_union=len(union),
        unanimous=unanimous,
        contested=contested,
        agreement_rate=rate,
        per_entity=per_entity,
        by_span=by_span,
    )


def adjudication_queue(members: list[MemberSpans]) -> list[dict]:
    """Contested spans only — the human queue.

    One row per union span where not every member agreed. Each row carries the
    text window is not here (no data/ access); the caller joins it from the
    page text. This queue is a few hundred items, not 85 pages.
    """
    report = agreement_report(members)
    queue: list[dict] = []
    for row in report.by_span:
        if not row["unanimous"]:
            queue.append(
                {
                    "start": row["start"],
                    "end": row["end"],
                    "entity": row["entity"],
                    "members_agreeing": row["members_agreeing"],
                    "n_members": report.n_members,
                    "status": "needs_adjudication",
                }
            )
    queue.sort(key=lambda x: (x["entity"], x["start"]))
    return queue


def human_verification_sample(
    page_ids: list[str], n: int = 20, seed: int = 7
) -> list[str]:
    """Random 15-20 page sample fully human-verified to bound all-missed rate.

    The one direction union cannot self-check: every model missed the same
    PII. This sample is the irreducible human step (an evening, not a
    workstream). Deterministic by seed so the manifest can name which pages
    were read.
    """
    if n < 15 or n > 20:
        raise ValueError("sample must be 15-20 pages")
    if len(page_ids) < n:
        raise ValueError("not enough pages for sample")
    rng = random.Random(seed)
    shuffled = list(page_ids)
    rng.shuffle(shuffled)
    return sorted(shuffled[:n])


def claim_sentence(report: AgreementReport, n_human_pages: int) -> str:
    """Publishable claim with N, Y, Z filled in — prevents overstatement.

    Not publishable without these numbers; the harness refuses to emit a bare
    coverage percentage when the gold provenance is ensemble.
    """
    return (
        f"Presidio missed X% of PII on a gold set labelled by an ensemble of "
        f"{report.n_members} independent frontier models spanning {len(report.vendors)} "
        f"vendor(s) ({', '.join(report.vendors)}), which agreed on "
        f"{report.agreement_rate:.1%} of spans ({report.unanimous}/{report.total_union} unanimous), "
        f"with disagreements human-adjudicated and a {n_human_pages}-page sample fully human-verified. "
        f"See the report for per-entity and per-language breakdown."
    )
