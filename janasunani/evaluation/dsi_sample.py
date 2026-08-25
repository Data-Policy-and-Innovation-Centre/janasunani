"""Nested stratified draws over the DSI clinic reference corpus.

The DSI clinic's ``large_sample`` is the corpus their reported numbers were
measured on: 69,844 documents across 69,675 tickets, drawn from 100,000
complaints at seed 1337. Reproducing anything of theirs means working from it
rather than from a slice of our own choosing.

It is also 60 GB, and a single pipeline pass over all of it is days of compute.
So the work is tiered, and the tiers must nest:

    latency  (few hundred)  subset of
    quality  (few thousand) subset of
    corpus   (69,844)

**Nesting is by construction, never by re-seeding.** A seeded re-draw at a
smaller budget is not a subset of the larger one: ``allocate`` apportions a
per-category floor first and then the remainder by largest remainder, so
changing the budget reshuffles which categories get headroom and the two draws
share only what they happen to share. This module therefore draws the largest
tier from the corpus, then each smaller tier *from the tier above it*. That is
the only way the latency numbers and the quality numbers describe the same
documents, which is the whole point of doing it this way.

The stratifier is ``sarvam_sample_builder.allocate``, reused rather than
reimplemented. Its docstring records three failures it already survived; a
second copy here would get to rediscover them.

Reads only the paths it is given. Emits manifests of ticket ids, categories and
filenames -- never document text.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from janasunani.evaluation.sarvam_sample_builder import allocate

#: Frozen so a redraw is reproducible. Distinct from the DSI corpus seed (1337)
#: and from the Sambalpur slice seed (20260824) so the three cannot be confused
#: in a manifest.
DEFAULT_SEED = 20260825

#: Per-category minimum. The corpus has 35 categories with a very long tail --
#: Traffic has 12 rows against Housing's 17,103 -- so without a floor the rare
#: categories vanish and per-category accuracy is unreportable for most of the
#: taxonomy.
DEFAULT_FLOOR = 3

#: Category recorded as absent. Kept as an explicit bucket rather than dropped,
#: because "how many documents have no category at all" is itself a finding and
#: silently excluding them would overstate coverage.
UNCATEGORISED = "__uncategorised__"


@dataclass(frozen=True)
class DocumentRecord:
    """One document on disk, joined to its complaint's recorded category."""

    ticket: str
    filename: str
    category: str
    size_bytes: int

    @property
    def is_categorised(self) -> bool:
        return self.category != UNCATEGORISED


def _norm_category(value: Any) -> str:
    """Readable category, or the explicit uncategorised bucket.

    Whitespace and Unicode form only. The DSI corpus stores its 34 categories
    cleanly -- checked on 2026-08-25, the only ampersands are the literal ones
    in ``Agriculture & Farming`` and ``School & College``.

    Our own lake is different: it holds ``Scheme & Benefits`` double-escaped as
    ``Scheme &amp;amp; Benefits``, and a stratifier keying on the raw string
    would split one category into two strata. That does not happen here, so
    rather than carry a second copy of the scoring-side unescaper this raises
    if the assumption ever stops holding. Silently coping would hide a corpus
    swap; the scoring path has ``sarvam_scorecard.unescape_label`` for the
    case where escaping is expected.
    """
    if value is None:
        return UNCATEGORISED
    text = unicodedata.normalize("NFC", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return UNCATEGORISED
    if "&amp;" in text or "&lt;" in text or "&gt;" in text or "&quot;" in text:
        raise ValueError(
            f"category {text!r} carries an HTML entity, so this corpus needs "
            "unescaping before stratification or one category will split into "
            "two strata. Use sarvam_scorecard.unescape_label and update this "
            "function's assumption."
        )
    return text


def load_corpus(
    documents_dir: Path | str,
    complaints_path: Path | str,
    *,
    ticket_column: str = "ticket_no",
    category_column: str = "category",
) -> list[DocumentRecord]:
    """Join documents on disk to their complaint category.

    Filenames are ``<ticket>_complaint_<timestamp>.<ext>``. A ticket may carry
    more than one document; each is its own record, because the pipeline
    processes documents and the latency measurement is per document.

    Documents whose ticket is absent from the complaints table, or whose
    complaint has no category, land in :data:`UNCATEGORISED` rather than being
    dropped.
    """
    import polars as pl

    documents_dir = Path(documents_dir)
    frame = pl.read_parquet(complaints_path, columns=[ticket_column, category_column])
    categories: dict[str, str] = {}
    for ticket, category in zip(
        frame[ticket_column].cast(pl.Utf8).to_list(),
        frame[category_column].to_list(),
        strict=True,
    ):
        if ticket:
            categories[str(ticket)] = _norm_category(category)

    records: list[DocumentRecord] = []
    for path in sorted(documents_dir.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        ticket = path.name.split("_complaint_")[0]
        records.append(
            DocumentRecord(
                ticket=ticket,
                filename=path.name,
                category=categories.get(ticket, UNCATEGORISED),
                size_bytes=path.stat().st_size,
            )
        )
    return records


def _draw(
    pool: Sequence[DocumentRecord],
    budget: int,
    floor: int,
    rng: random.Random,
) -> list[DocumentRecord]:
    """Stratified draw of *budget* documents from *pool*.

    Categories are shuffled independently so the choice within a stratum is
    random, while the allocation across strata stays deterministic given the
    counts. Sorting the pool first makes the shuffle depend on the seed rather
    than on filesystem order.
    """
    if budget >= len(pool):
        return sorted(pool, key=lambda r: r.filename)

    by_category: dict[str, list[DocumentRecord]] = {}
    for record in sorted(pool, key=lambda r: r.filename):
        by_category.setdefault(record.category, []).append(record)

    counts = {c: len(rs) for c, rs in by_category.items()}
    quota = allocate(counts, budget=budget, floor=floor)

    chosen: list[DocumentRecord] = []
    for category in sorted(by_category):
        candidates = list(by_category[category])
        rng.shuffle(candidates)
        chosen.extend(candidates[: quota[category]])
    return sorted(chosen, key=lambda r: r.filename)


def manifest_digest(records: Sequence[DocumentRecord]) -> str:
    """Stable digest over the drawn set: ticket, filename and size.

    Size is included so that re-hydrating a document as a different file is
    visible as a different sample rather than silently reusing the id.
    """
    payload = "\n".join(f"{r.ticket}\t{r.filename}\t{r.size_bytes}" for r in sorted(records, key=lambda r: r.filename))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarise(records: Sequence[DocumentRecord]) -> dict[str, Any]:
    counts = Counter(r.category for r in records)
    categorised = [r for r in records if r.is_categorised]
    return {
        "documents": len(records),
        "tickets": len({r.ticket for r in records}),
        "categorised_documents": len(categorised),
        "distinct_categories": len({r.category for r in categorised}),
        "bytes": sum(r.size_bytes for r in records),
        "per_category": dict(sorted(counts.items())),
        "digest": manifest_digest(records),
    }


def draw_nested(
    corpus: Sequence[DocumentRecord],
    tiers: Mapping[str, int],
    *,
    seed: int = DEFAULT_SEED,
    floor: int = DEFAULT_FLOOR,
) -> dict[str, list[DocumentRecord]]:
    """Draw each tier from the tier above it, largest first.

    ``tiers`` maps a name to a document budget, e.g.
    ``{"quality": 10_000, "latency": 500}``. They are sorted descending and
    each draw uses the previous tier as its pool, so the result is a strict
    chain of subsets. Verify it with :func:`assert_nested` rather than trusting
    it: this is the property that a re-seeded redraw silently breaks.
    """
    ordered = sorted(tiers.items(), key=lambda kv: -kv[1])
    out: dict[str, list[DocumentRecord]] = {}
    pool: Sequence[DocumentRecord] = corpus
    for index, (name, budget) in enumerate(ordered):
        # A distinct stream per tier, derived from one seed, so adding a tier
        # does not perturb the tiers above it.
        rng = random.Random(f"{seed}:{name}:{index}")
        drawn = _draw(pool, budget=budget, floor=floor, rng=rng)
        out[name] = drawn
        pool = drawn
    return out


def assert_nested(tiers: Mapping[str, Sequence[DocumentRecord]]) -> None:
    """Raise unless every smaller tier is a subset of every larger one."""
    ordered = sorted(tiers.items(), key=lambda kv: -len(kv[1]))
    for (outer_name, outer), (inner_name, inner) in zip(ordered, ordered[1:], strict=False):
        outer_files = {r.filename for r in outer}
        stray = sorted({r.filename for r in inner} - outer_files)
        if stray:
            raise ValueError(
                f"{inner_name} is not a subset of {outer_name}: "
                f"{len(stray)} document(s) absent from the larger tier, "
                f"first {stray[0]!r}"
            )


def write_manifest(
    records: Sequence[DocumentRecord],
    path: Path | str,
    *,
    name: str,
    seed: int,
    floor: int,
    source: str,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "name": name,
        "source": source,
        "seed": seed,
        "floor_per_category": floor,
        **summarise(records),
        "documents_list": [asdict(r) for r in records],
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
