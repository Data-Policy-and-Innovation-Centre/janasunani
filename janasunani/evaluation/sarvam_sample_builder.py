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

Two more found by review rather than a live run:

4. **A missing page-counting backend must not look like a bad document.** The
   console script declares no runtime dependency on ``pdf2image`` (it lives
   only in the ``pipeline-core``/``ocr-deepseek`` extras), so
   ``uv run janasunani-build-sarvam-sample`` without an extra hit ``ImportError``
   here. A blanket ``except Exception`` caught it and treated it exactly like
   an unreadable PDF: page count 0, excluded by ``select_within_caps``. Every
   fetched PDF would fail the same way, and the CLI would exit 0 having
   written an empty or truncated manifest. An absent backend must fail loudly.
5. **A category floor must be checked against the budget before it is handed
   out.** Independent rounding was fixed to respect the budget (largest
   remainder, below), but the floor stage that runs before it was not: if the
   floors alone already exceed the budget, the allocation is infeasible from
   the start, and ``choose_documents`` was silently resolving that by
   truncating in largest-category order -- dropping exactly the small
   categories the floor exists to protect.

Archived storage is the other operational fact worth encoding: roughly 90% of
this corpus is in GLACIER, where ``GetObject`` fails outright. Restores must be
requested and waited for, and the restore window has to outlast whatever the
sample is for. A 3-day window set on 9 August expired on the 13th, the day
before the demo it was built for.
"""

from __future__ import annotations

import json
import random
import shutil
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from loguru import logger

DEFAULT_BUCKET = "janasunani-documents-main"
DEFAULT_REGION = "ap-south-1"

#: Sarvam bills per page; a document is one or more pages.
PRICE_PER_PAGE_BOTH = 1.50

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"})


class PageCountBackendUnavailable(RuntimeError):
    """The page-counting backend itself is missing -- not a bad document.

    Must never be swallowed by the generic "unreadable document" path: doing
    so is how a plain ``uv run janasunani-build-sarvam-sample`` (no extra)
    used to silently zero out every PDF's page count and ship an empty or
    truncated manifest while exiting 0.
    """


def page_count(path: Path) -> int:
    """Pages in a staged document. Images are one page; PDFs are asked.

    Returns 0 for a document that is itself unreadable (corrupt PDF, wrong
    magic bytes, ...), which ``select_within_caps`` treats as unusable. A
    document whose page count cannot be determined must not be billed for,
    since pages are the billed unit.

    Raises :class:`PageCountBackendUnavailable` instead of returning 0 when
    the *backend* -- ``pdf2image``, or the Poppler binaries it shells out to
    -- is not installed. That is an environment problem, not a bad document:
    ``pdf2image`` lives only in the ``pipeline-core``/``ocr-deepseek`` extras
    (see ``pyproject.toml``), so the bare console script hits this every time
    unless ``--extra pipeline-core`` was used to run it.
    """
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return 1
    try:
        from pdf2image import pdfinfo_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError
    except ImportError as exc:
        raise PageCountBackendUnavailable(
            "pdf2image is not installed, so PDF pages cannot be counted. It is "
            "not a base dependency of this CLI -- run with "
            "`uv run --extra pipeline-core janasunani-build-sarvam-sample ...`."
        ) from exc

    try:
        return int(pdfinfo_from_path(str(path))["Pages"])
    except PDFInfoNotInstalledError as exc:
        raise PageCountBackendUnavailable(
            "pdf2image is installed but Poppler (`pdfinfo`) is not, so PDF "
            "pages cannot be counted. Install Poppler (`apt-get install "
            "poppler-utils` / `brew install poppler`)."
        ) from exc
    except Exception:
        logger.warning("could not read a page count from {}; excluding it", path.name)
        return 0


def allocate(counts: Mapping[str, int], budget: int, floor: int) -> dict[str, int]:
    """Documents to draw per category: a floor, then proportional remainder.

    ``budget`` is a **document** count, not a page count. Passing a page target
    here is the first of the three failures in the module docstring: the
    selection loop stops at the document cap, so a page-sized budget lets the
    largest categories exhaust it before smaller ones are reached.

    The remainder is apportioned by largest remainder (Hamilton), not by
    rounding each category independently. Independent rounding does not sum to
    the budget it is meant to enforce: with the real slice,
    ``allocate(counts, budget=220, floor=3)`` returned 221, and three equal
    categories on a budget of 5 returned 6. The whole point of this function is
    to respect a budget, so overshooting it is not a rounding detail.

    The floor stage is checked against the budget before any remainder is
    apportioned. If the floors alone (each capped at what the category
    actually has) already exceed ``budget``, the allocation is infeasible and
    this raises ``ValueError`` rather than silently returning an
    over-allocation: three populated categories with ``budget=2, floor=1``
    used to return 3, and the caller (``choose_documents``) resolved that
    itself by truncating in largest-category order -- dropping exactly the
    small categories the floor exists to protect, and doing so differently
    depending on how the categories happened to sort. Raising forces the
    caller to pick a coherent floor/budget instead.

    Ties in the remainder break on category name so the result is
    reproducible rather than dependent on the input's ordering.
    """
    categories = sorted(counts)
    alloc = {c: min(floor, counts[c]) for c in categories}
    floor_total = sum(alloc.values())
    if floor_total > budget:
        raise ValueError(
            f"the floor alone ({floor_total} documents across {len(categories)} "
            f"categories at floor={floor}) exceeds the budget ({budget}); this "
            "allocation cannot be made to fit. Lower --floor, raise "
            "--max-documents, or narrow the category set."
        )
    remaining = budget - floor_total
    headroom = {c: counts[c] - alloc[c] for c in categories}
    total_headroom = sum(headroom.values())
    if not remaining or not total_headroom:
        return alloc

    # Largest remainder: floor everyone, then hand out what is left over to the
    # largest fractional parts until the budget is exactly spent.
    exact = {c: remaining * headroom[c] / total_headroom for c in categories}
    for category in categories:
        alloc[category] += min(int(exact[category]), headroom[category])

    spent = sum(alloc.values())
    leftover = budget - spent
    if leftover <= 0:
        return alloc

    order = sorted(
        categories,
        key=lambda c: (-(exact[c] - int(exact[c])), c),
    )
    for category in order:
        if leftover <= 0:
            break
        if alloc[category] < counts[category]:
            alloc[category] += 1
            leftover -= 1
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
    """Shuffle slice tickets per category under a fixed seed.

    Rows are canonicalised before the RNG is touched. ``rows`` comes from a
    lake query with no guaranteed ordering, and both the per-category ticket
    lists and the category iteration order would otherwise inherit that
    ordering: the same tickets and the same seed produced a different sample
    when the query returned them in a different order. The manifest claims the
    draw is reproducible from the recorded seed and slice, so a result that
    depends on unspecified row order makes that claim false.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for ticket, category in rows:
        if category:
            grouped[category].append(ticket)

    rng = random.Random(seed)
    by_category: dict[str, list[str]] = {}
    for category in sorted(grouped):
        tickets = sorted(grouped[category])
        rng.shuffle(tickets)
        by_category[category] = tickets
    return by_category, {c: len(t) for c, t in by_category.items()}


# ---------------------------------------------------------------------------
# The executable path
#
# Everything above is a pure function, and for a while that was all this module
# had: the operational draw lived in a scratchpad script that was never
# committed, so the checked-in code could not reproduce the sample it claimed
# to describe and `is_ambiguous_key` was never actually enforced against a real
# key. The orchestration below is the part that makes the rest true.
#
# Document lookup and archive restore are behind a Protocol so the whole path
# is testable without S3. That is not ceremony: ~90% of this corpus is in
# GLACIER, and a test that needed a restore would never run.
# ---------------------------------------------------------------------------


class DocumentSource(Protocol):
    """Where a ticket's scanned document comes from, and how to fetch it."""

    def find(self, ticket: str) -> tuple[str, str] | None:
        """Return ``(key, storage_class)`` for *ticket*, or None."""
        ...

    def ensure_readable(self, keys: Sequence[str]) -> set[str]:
        """Make *keys* readable, returning those that are not yet available."""
        ...

    def fetch(self, key: str, destination: Path) -> None:
        """Download *key* to *destination*."""
        ...


def choose_documents(
    by_category: Mapping[str, Sequence[str]],
    counts: Mapping[str, int],
    source: DocumentSource,
    *,
    max_documents: int,
    floor: int,
) -> list[dict[str, Any]]:
    """Pick documents per the allocation, skipping tickets with no usable scan."""
    alloc = allocate(counts, max_documents, floor)
    chosen: list[dict[str, Any]] = []
    for category in sorted(alloc, key=lambda c: (-counts.get(c, 0), c)):
        if len(chosen) >= max_documents:
            break
        drawn = 0
        for ticket in by_category.get(category, ()):
            if len(chosen) >= max_documents or drawn >= alloc[category]:
                break
            found = source.find(ticket)
            if found is None:
                continue
            key, storage_class = found
            if is_ambiguous_key(key):
                # Enforced here, on a real key, rather than only in a unit test.
                logger.warning(
                    "skipping {}: key {!r} would be attributed to the wrong ticket",
                    ticket,
                    key,
                )
                continue
            drawn += 1
            chosen.append(
                {
                    "ticket": ticket,
                    "gold_category": category,
                    "s3_key": key,
                    "storage_class": storage_class,
                    "file": Path(key).name,
                }
            )
    return chosen


def prepare_out_dir(out_dir: Path, *, clean: bool = False) -> None:
    """Make *out_dir* ready to stage into: empty, and created if missing.

    A pre-existing non-empty directory is not harmless leftover. The cleanup
    at the end of ``build_sample`` only removes files it fetched itself, so
    documents a prior draw left behind survive untouched; `sarvam_evaluate`
    then enumerates every supported document under the directory without
    consulting the manifest, so a rerun with a different seed or cap submits
    stale pages that are absent from the new manifest -- corrupting both the
    paired sample and its reported cost. Reject a dirty directory by default;
    only clear it when the caller explicitly asks to.
    """
    if not out_dir.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        return
    leftover = list(out_dir.iterdir())
    if not leftover:
        return
    if not clean:
        raise FileExistsError(
            f"{out_dir} already has {len(leftover)} entr{'y' if len(leftover) == 1 else 'ies'} "
            "in it. A prior draw's documents would be re-submitted alongside this one "
            "without appearing in its manifest. Pass clean=True (CLI: --clean) to clear "
            "it first, or point --out at an empty directory."
        )
    for entry in leftover:
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def build_sample(
    rows: Iterable[tuple[str, str]],
    source: DocumentSource,
    *,
    out_dir: Path,
    slice_label: str,
    seed: int,
    target_pages: int,
    floor: int,
    max_documents: int,
    max_pages_per_category: int,
    max_pages_per_document: int,
    page_counter: Callable[[Path], int] | None = None,
    restore_expiry: str | None = None,
    clean_out_dir: bool = False,
) -> dict[str, Any]:
    """Draw, stage and record one reproducible sample. The whole path.

    Returns the manifest. Writes the staged documents and the manifest under
    *out_dir*, which must not be inside the repository: the manifest carries
    real ticket numbers and S3 keys. *out_dir* must be empty (or absent); pass
    ``clean_out_dir=True`` to clear a leftover directory from a prior draw
    rather than mixing its files into this one's manifest.
    """
    prepare_out_dir(out_dir, clean=clean_out_dir)
    count_pages = page_counter or page_count
    by_category, counts = draw_tickets(rows, seed=seed)
    chosen = choose_documents(
        by_category,
        counts,
        source,
        max_documents=max_documents,
        floor=floor,
    )
    logger.info("selected {} documents across {} categories", len(chosen), len(counts))

    pending = source.ensure_readable([doc["s3_key"] for doc in chosen])
    if pending:
        logger.warning("{} document(s) are still unavailable and will be skipped", len(pending))

    out_dir.mkdir(parents=True, exist_ok=True)
    available = [doc for doc in chosen if doc["s3_key"] not in pending]
    ordered = interleave(available, counts)

    page_counts: dict[str, int] = {}
    fetched: list[Mapping[str, Any]] = []
    for doc in ordered:
        destination = out_dir / doc["file"]
        try:
            source.fetch(doc["s3_key"], destination)
        except Exception as exc:
            logger.warning("skipping {}: {}", doc["file"], type(exc).__name__)
            continue
        page_counts[doc["file"]] = count_pages(destination)
        fetched.append(doc)

    selected = select_within_caps(
        fetched,
        page_counts=page_counts,
        target_pages=target_pages,
        max_pages_per_category=max_pages_per_category,
        max_pages_per_document=max_pages_per_document,
    )

    # Anything fetched but not selected is not part of the sample, and leaving
    # it beside the manifest would let a later run read pages the manifest does
    # not account for.
    kept = {doc["file"] for doc in selected}
    for doc in fetched:
        if doc["file"] not in kept:
            (out_dir / doc["file"]).unlink(missing_ok=True)

    manifest = build_manifest(
        selected,
        slice_label=slice_label,
        seed=seed,
        target_pages=target_pages,
        floor=floor,
        max_pages_per_category=max_pages_per_category,
        max_pages_per_document=max_pages_per_document,
        restore_expiry=restore_expiry,
    )
    write_manifest(manifest, out_dir / "sample_manifest.json")
    logger.success(
        "staged {} pages over {} tickets in {} categories",
        manifest["pages_staged"],
        manifest["tickets"],
        manifest["categories"],
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    """``janasunani-build-sarvam-sample``."""
    import argparse

    parser = argparse.ArgumentParser(description="Draw the Sarvam benchmark sample.")
    parser.add_argument("--out", type=Path, required=True, help="Staging directory (keep outside the repo).")
    parser.add_argument("--lake-dir", type=Path, default=Path("data/interim"))
    parser.add_argument("--district", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--target-pages", type=int, default=300)
    parser.add_argument("--floor", type=int, default=3)
    parser.add_argument("--max-documents", type=int, default=220)
    parser.add_argument("--max-pages-per-category", type=int, default=25)
    parser.add_argument("--max-pages-per-document", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--restore-days", type=int, default=21, help="Must outlast whatever the sample is for.")
    parser.add_argument("--restore-tier", default="Expedited", choices=["Expedited", "Standard", "Bulk"])
    parser.add_argument(
        "--restore-timeout-seconds",
        type=int,
        default=None,
        help="Overrides the tier-appropriate default (Expedited minutes, Standard/Bulk hours).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Named AWS profile. Leave unset to use the instance-role credential chain.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clear a non-empty --out directory left by a prior draw instead of rejecting it.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only; fetch nothing.")
    args = parser.parse_args(argv)

    from janasunani.config import DEMO_SLICE_DISTRICT, DEMO_SLICE_YEAR
    from janasunani.olap import lake

    district = args.district or DEMO_SLICE_DISTRICT
    year = args.year or DEMO_SLICE_YEAR
    frame = lake.query(
        "SELECT ticket_no, category FROM complaints "
        f"WHERE lower(district) = lower('{district}') AND created_year = {year} "
        "AND category IS NOT NULL",
        lake_dir=args.lake_dir,
        tables=["complaints"],
    )
    rows = list(frame.iter_rows())
    logger.info("slice {}/{}: {} labelled tickets", district, year, len(rows))

    if args.dry_run:
        by_category, counts = draw_tickets(rows, seed=args.seed)
        try:
            alloc = allocate(counts, args.max_documents, args.floor)
        except ValueError as exc:
            logger.error(str(exc))
            return 1
        for category in sorted(alloc, key=lambda c: -counts[c]):
            logger.info("  {}: {} of {} available", category, alloc[category], counts[category])
        logger.info("planned documents: {}", sum(alloc.values()))
        return 0

    from janasunani.evaluation.sarvam_s3 import S3DocumentSource

    source = S3DocumentSource(
        bucket=args.bucket,
        restore_days=args.restore_days,
        restore_tier=args.restore_tier,
        restore_timeout_seconds=args.restore_timeout_seconds,
        profile=args.profile,
    )
    try:
        build_sample(
            rows,
            source,
            out_dir=args.out,
            slice_label=f"{district}/{year}",
            seed=args.seed,
            target_pages=args.target_pages,
            floor=args.floor,
            max_documents=args.max_documents,
            max_pages_per_category=args.max_pages_per_category,
            max_pages_per_document=args.max_pages_per_document,
            clean_out_dir=args.clean,
        )
    except (ValueError, PageCountBackendUnavailable) as exc:
        logger.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
