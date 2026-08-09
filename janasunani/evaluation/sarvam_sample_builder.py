"""Build the category-stratified page sample the Sarvam benchmark runs on.

Selection lives here rather than inside ``sarvam_evaluate`` so the draw is
reproducible and auditable: every ticket, its recorded category, its S3 key and
its page count are written to a manifest *before* a page is sent anywhere.

Design, per #126 and #127:

* **Stratify by recorded category.** The label is administrative data we already
  hold, so the join is exact and costs no annotation.
* **Floor per category, then proportional.** The floor buys per-class signal;
  the proportional tail keeps the draw anchored to the slice.
* **Match on ticket, never on text.**
* **Seeded**, so a run can be reproduced or extended without repeating spend.

Three failures found by running this for real on 2026-08-09. Each is now a
named function with a test, because each produced a plausible-looking sample
that was quietly wrong:

1. **Allocation must be budgeted in documents, not pages.** Documents average
   2.8 pages and run to 22. Budgeting the draw against a page target let the
   two largest categories consume the whole cap before a third was reached:
   301 pages covering 3 of 31 categories.
2. **Download order must interleave across categories.** Walking the selection
   in category order and stopping at the page budget truncates the tail rather
   than thinning every class. Round-robin makes an early stop shrink cells
   evenly.
3. **A per-category page cap is not enough on its own.** One 22-page document
   filled almost the entire COVID-19 cell, a category with 3 tickets in the
   whole slice. Cap pages per document as well.

Archived storage is the other operational fact worth encoding: roughly 90% of
this corpus is in GLACIER, where ``GetObject`` fails outright. Restores must be
requested and waited for, and the restore window has to outlast whatever the
sample is for. A 3-day window set on 9 August expired on the 13th, the day
before the demo it was built for.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_BUCKET = "janasunani-documents-main"
DEFAULT_REGION = "ap-south-1"

#: Sarvam bills per page; a document is one or more pages.
PRICE_PER_PAGE_BOTH = 1.50


def allocate(counts: Mapping[str, int], budget: int, floor: int) -> dict[str, int]:
    """Documents to draw per category: a floor, then proportional remainder.

    ``budget`` is a **document** count, not a page count. Passing a page target
    here is the first of the three failures in the module docstring: the
    selection loop stops at the document cap, so a page-sized budget lets the
    largest categories exhaust it before smaller ones are reached.
    """
    categories = sorted(counts)
    alloc = {c: min(floor, counts[c]) for c in categories}
    remaining = max(0, budget - sum(alloc.values()))
    headroom = {c: counts[c] - alloc[c] for c in categories}
    total_headroom = sum(headroom.values())
    if remaining and total_headroom:
        for category in categories:
            extra = round(remaining * headroom[category] / total_headroom)
            alloc[category] += min(extra, headroom[category])
    return alloc


def interleave(chosen: Sequence[Mapping[str, Any]], counts: Mapping[str, int]) -> list[Mapping[str, Any]]:
    """Round-robin the selection across categories, largest class first.

    ``chosen`` arrives grouped by category. Downloading it in that order and
    stopping at a page budget yields only the largest few classes, so the
    stratification never survives to the sample. Interleaving makes an early
    stop shrink every cell instead of truncating the tail.
    """
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for document in chosen:
        groups[document["gold_category"]].append(document)
    ordered = sorted(groups, key=lambda c: -counts.get(c, 0))
    return [
        document
        for row in zip_longest(*(groups[c] for c in ordered))
        for document in row
        if document is not None
    ]


def is_ambiguous_key(key: str) -> bool:
    """Whether the evaluator would attribute this key to the wrong ticket.

    ``sarvam_evaluate`` derives the ticket from the file stem up to the first
    underscore, so ``AN063/E/2021/00001_complaint_….pdf`` reads as ticket
    ``00001``. Rare in this bucket, and silently joins to the wrong record.
    """
    return "/" in key


def select_within_caps(
    documents: Iterable[Mapping[str, Any]],
    *,
    page_counts: Mapping[str, int],
    target_pages: int,
    max_pages_per_category: int,
    max_pages_per_document: int,
) -> list[dict[str, Any]]:
    """Take documents in order until the page budget or a cap is reached.

    Both caps matter and they fail differently. Without the category cap, one
    class dominates the run. Without the document cap, one long PDF fills a
    class on its own: a 22-page scan took 22 of the 25 pages allowed to a
    category holding 3 tickets in a 36,909-ticket slice.
    """
    taken: list[dict[str, Any]] = []
    pages_total = 0
    per_category: dict[str, int] = defaultdict(int)
    for document in documents:
        if pages_total >= target_pages:
            break
        category = document["gold_category"]
        pages = page_counts.get(document["file"], 0)
        if pages <= 0 or pages > max_pages_per_document:
            continue
        if per_category[category] >= max_pages_per_category:
            continue
        if per_category[category] + pages > max_pages_per_category and per_category[category] > 0:
            # Keep the cap meaningful without discarding a category's only doc.
            continue
        taken.append({**document, "pages": pages})
        pages_total += pages
        per_category[category] += pages
    return taken


def build_manifest(
    documents: Sequence[Mapping[str, Any]],
    *,
    slice_label: str,
    seed: int,
    target_pages: int,
    floor: int,
    max_pages_per_category: int,
    max_pages_per_document: int,
    restore_expiry: str | None = None,
) -> dict[str, Any]:
    """The record that makes the draw auditable rather than asserted."""
    pages_by_category: dict[str, int] = defaultdict(int)
    for document in documents:
        pages_by_category[document["gold_category"]] += document.get("pages", 0)
    pages_total = sum(pages_by_category.values())
    return {
        "slice": slice_label,
        "seed": seed,
        "target_pages": target_pages,
        "floor_per_category": floor,
        "max_pages_per_category": max_pages_per_category,
        "max_pages_per_document": max_pages_per_document,
        "pages_staged": pages_total,
        "tickets": len(documents),
        "categories": len(pages_by_category),
        "estimated_cost_rupees_arm_both": round(pages_total * PRICE_PER_PAGE_BOTH, 2),
        "restore_expiry": restore_expiry,
        "pages_by_category": dict(sorted(pages_by_category.items(), key=lambda kv: -kv[1])),
        "documents": list(documents),
    }


def write_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    """Write the manifest.

    ⚠️ The manifest carries real ticket numbers and S3 keys, so it is citizen
    data by reference and must never be committed to Git. `data-check.yml`
    already refuses tracked files under ``data/`` and ``outputs/``; keep the
    manifest in one of those or under DVC.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))
    return path


def draw_tickets(
    rows: Iterable[tuple[str, str]],
    *,
    seed: int,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Shuffle slice tickets per category under a fixed seed."""
    by_category: dict[str, list[str]] = defaultdict(list)
    for ticket, category in rows:
        if category:
            by_category[category].append(ticket)
    rng = random.Random(seed)
    for tickets in by_category.values():
        rng.shuffle(tickets)
    return dict(by_category), {c: len(t) for c, t in by_category.items()}
