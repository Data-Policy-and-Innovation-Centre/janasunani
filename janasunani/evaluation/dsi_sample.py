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


@dataclass(frozen=True)
class ManifestEntry:
    """One row of the pinned reference manifest."""

    ticket: str
    size_bytes: int | None
    md5: str | None


def load_reference_manifest(path: Path | str) -> dict[str, ManifestEntry]:
    """``{s3_key: ManifestEntry}`` from the pinned DSI reference manifest (TSV).

    Columns are ``ticket``, ``s3_key``, ``size_bytes``, ``md5``. The key is
    the object key in ``janasunani-documents-dsi-reference``, which is also
    the path relative to the corpus root once synced -- so it is directly
    comparable to what :func:`load_corpus` walks.

    A repeated ``s3_key`` raises: the surviving row would decide the ticket,
    the category and the provenance for that document, so a duplicate makes
    stratification depend on manifest order.

    ``size_bytes`` and ``md5`` are kept, not discarded. A key comparison
    alone proves only that a file with the right name is present; a
    truncated, stale or replaced document under the expected key passes it,
    and ``draw_nested`` then emits well-formed tier manifests for altered
    bytes. See ``verify`` in :func:`load_corpus`.

    The md5 column is a true content hash for every object in the reference
    bucket, including the 80 over 8 MB: they arrived multipart in the source
    bucket, where the etag is not an MD5, and the server-side copy rewrote
    them single-part.
    """
    import csv

    path = Path(path)
    entries: dict[str, ManifestEntry] = {}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = {"ticket", "s3_key"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{path} is not a reference manifest: missing column(s) "
                f"{sorted(missing)}. Expected ticket, s3_key, size_bytes, md5."
            )
        duplicated: list[str] = []
        for row in reader:
            key = row.get("s3_key")
            if not key:
                continue
            # A repeated key is a broken manifest, not a last-one-wins
            # question. The disk-versus-manifest set comparison in
            # `load_corpus` still passes on a duplicate, but the surviving
            # row supplies the ticket used for category lookup and emitted
            # provenance -- so two rows for one key silently make
            # stratification depend on manifest order.
            if key in entries:
                duplicated.append(key)
                continue
            raw_size = (row.get("size_bytes") or "").strip()
            entries[key] = ManifestEntry(
                ticket=row["ticket"],
                size_bytes=int(raw_size) if raw_size.isdigit() else None,
                md5=(row.get("md5") or "").strip() or None,
            )
    if duplicated:
        raise ValueError(
            f"{path} lists {len(duplicated)} s3_key(s) more than once: "
            f"{sorted(set(duplicated))[:5]}"
            + (", ..." if len(set(duplicated)) > 5 else "")
            + ". The key comparison in load_corpus still passes on a "
            "duplicate, so the row that happens to come last would decide "
            "the ticket, the category and the provenance for that document."
        )
    return entries


def load_corpus(
    documents_dir: Path | str,
    complaints_path: Path | str,
    *,
    ticket_column: str = "ticket_no",
    category_column: str = "category",
    manifest: Mapping[str, "ManifestEntry"] | None = None,
    verify: str = "size",
) -> list[DocumentRecord]:
    """Join documents on disk to their complaint category.

    Object keys are ``<ticket>_complaint_<timestamp>.<ext>`` and the ticket may
    itself contain slashes: 1,446 of the corpus's 70,029 documents (2.06%)
    belong to tickets like ``OR159/P/2021/00535``. The reference bucket stores
    them under the full key, so a synced copy has those documents in nested
    directories.

    The walk is therefore recursive and the ticket comes from the path
    **relative to** ``documents_dir``, which reconstructs the object key
    exactly. A flat ``iterdir()`` did not mis-parse those documents, it never
    reached them: they sat one directory down and were silently absent from
    the corpus, and so from every tier drawn out of it. Taking the basename
    instead would be the other failure -- ``00535_complaint_...`` parses to
    ``00535``, which matches no complaint and collapses distinct tickets that
    share a trailing segment.

    A ticket may carry more than one document; each is its own record, because
    the pipeline processes documents and the latency measurement is per
    document.

    Documents whose ticket is absent from the complaints table, or whose
    complaint has no category, land in :data:`UNCATEGORISED` rather than being
    dropped.

    ``manifest`` is the pinned reference manifest
    (``load_reference_manifest``), and passing it is what makes the corpus
    *verified* rather than *whatever happens to be on disk*. Without it this
    walks any directory and hands the result to ``draw_nested``, which then
    produces valid-looking tier manifests for a different population -- the
    known-short Box copy (69,844 files against 70,029, see
    ``janasunani.samples``) being the case that already exists. With it, a
    key on disk that the manifest does not list, or a key the manifest lists
    that is not on disk, stops the draw.

    Ticket ids come from the manifest where it is given, rather than being
    re-parsed from the path. They are the same by construction -- the key is
    ``<ticket>_complaint_<timestamp>.<ext>`` -- but the manifest is the
    pinned record and the parser is a derivation, so where both exist the
    record wins.

    Raises:
        ValueError: a file does not carry the ``_complaint_`` marker and so
            has no ticket (including it as a document named after itself is
            how a stray ``manifest.tsv`` becomes a stratum of one), or the
            staged corpus does not match ``manifest``.
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
    unparsed: list[str] = []
    for path in sorted(documents_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(documents_dir)
        # Hidden anywhere in the path, not just the basename: a synced corpus
        # carries .dvc/ and .DS_Store, and neither is a document.
        if any(part.startswith(".") for part in relative.parts):
            continue
        key = relative.as_posix()
        if "_complaint_" not in key:
            unparsed.append(key)
            continue
        parsed = key.split("_complaint_")[0]
        entry = manifest.get(key) if manifest else None
        ticket = entry.ticket if entry is not None else parsed
        records.append(
            DocumentRecord(
                ticket=ticket,
                filename=key,
                category=categories.get(ticket, UNCATEGORISED),
                size_bytes=path.stat().st_size,
            )
        )

    if manifest is not None:
        if verify not in {"keys", "size", "md5"}:
            raise ValueError(
                f"verify must be 'keys', 'size' or 'md5', got {verify!r}"
            )
        on_disk = {r.filename for r in records}
        listed = set(manifest)
        missing = sorted(listed - on_disk)
        extra = sorted(on_disk - listed)
        if missing or extra:
            raise ValueError(
                f"the corpus under {documents_dir} does not match the pinned "
                f"reference manifest: {len(missing)} listed document(s) are "
                f"absent and {len(extra)} present document(s) are not listed "
                f"(missing e.g. {missing[:3]}, extra e.g. {extra[:3]}). "
                "Drawing tiers from it would produce valid-looking manifests "
                "for a different population -- the Box copy is 185 documents "
                "short of S3 in exactly this way. Re-sync from "
                "janasunani-documents-dsi-reference."
            )

        # Keys matching is not bytes matching. A truncated, stale or replaced
        # document under the expected key passes the set comparison above,
        # and draw_nested then emits well-formed tier manifests for altered
        # bytes -- the same class of silent-wrong-population failure, one
        # level down.
        #
        # Size is the default because it is a stat() per file: free at 70,029
        # documents, and it catches truncation and replacement, which is what
        # a partial sync actually produces. md5 is exact and reads all 56 GB,
        # so it is opt-in for when the corpus is being certified rather than
        # used. keys is the escape hatch for a manifest without the columns.
        if verify != "keys":
            # The requested tier must actually be available. `size_bytes` is
            # None for a blank or non-numeric column and `md5` is None for a
            # blank one, and both checks below are written as "compare it if
            # we have it" -- so a truncated or malformed manifest silently
            # downgraded verify="size" and verify="md5" to the key comparison
            # while the caller believed they had asked for bytes. That is the
            # certify-altered-documents failure this function exists to stop.
            # `keys` is the explicit escape hatch for a manifest without the
            # columns; it should not be reachable by accident.
            column = "size_bytes" if verify == "size" else "md5"
            unusable = sorted(
                record.filename
                for record in records
                if getattr(manifest[record.filename], column) is None
            )
            if unusable:
                raise ValueError(
                    f"verify={verify!r} needs a {column} for every document, "
                    f"and the pinned manifest has none for {len(unusable)} of "
                    f"them: {unusable[:3]}"
                    + (", ..." if len(unusable) > 3 else "")
                    + ". Comparing only the keys that do carry one would "
                    "certify the rest on their names alone. Re-sync the "
                    "manifest, or pass verify='keys' to say so deliberately."
                )

            corrupt: list[str] = []
            for record in records:
                entry = manifest[record.filename]
                if entry.size_bytes is not None and record.size_bytes != entry.size_bytes:
                    corrupt.append(
                        f"{record.filename} (size {record.size_bytes} != "
                        f"{entry.size_bytes})"
                    )
                    continue
                if verify == "md5" and entry.md5:
                    digest = hashlib.md5()  # noqa: S324 - matching S3 etags, not security
                    with (documents_dir / record.filename).open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != entry.md5:
                        corrupt.append(f"{record.filename} (md5 mismatch)")
            if corrupt:
                raise ValueError(
                    f"{len(corrupt)} document(s) under {documents_dir} do not "
                    f"match the pinned manifest's recorded bytes: "
                    f"{corrupt[:3]}"
                    + (", ..." if len(corrupt) > 3 else "")
                    + ". The keys are right and the content is not, so a draw "
                    "from this corpus would carry the manifest's identity and "
                    "different documents. Re-sync from "
                    "janasunani-documents-dsi-reference."
                )

    if unparsed:
        raise ValueError(
            f"{len(unparsed)} file(s) under {documents_dir} carry no "
            f"'_complaint_' marker and so have no ticket: {unparsed[:5]}"
            + (", ..." if len(unparsed) > 5 else "")
            + ". Move non-document files out of the corpus directory. Kept as "
            "documents they would each become their own uncategorised "
            "stratum and take a floor allocation from a real category."
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
