"""Dedup index backfill runner (Phase 14, W6 — #71).

`pipeline/dedup.py` provides the algorithmic primitives (MinHash, LSH,
Jaccard verification, union-find) and is deliberately stdlib-only. Nothing
there walks a slice, computes signatures, buckets them and persists a
result — that is what this module does, one stage after
`janasunani-redact-grievance` in the backfill order ROADMAP §5.2 lays out:

    janasunani-redact-grievance -> dedup index build (this) -> spam_duplicate scoring

Mirrors `redact_grievance.py`'s shape on purpose (argparse entrypoint,
`def main() -> None` wrapping `asyncio.run`, its own `create_async_engine`,
`--district`/`--year` with no defaults, batched and resumable): they are the
same kind of job, one stage apart, over the same slice.

**Built from `grievance_redactions.grievance_redacted` only.** Never from
`complaints.grievance` — that column has never met Presidio, and indexing it
directly would put raw citizen PII into signatures and identity keys instead
of the typed placeholders the redaction pass exists to produce. The pending
query below joins `complaints` only for structured columns (district,
created_on, petitioner_mobile/email); it never selects `complaints.grievance`.

**Two stages, two persistence shapes.**

1. `_index_signatures()` — batched, resumable via a pending predicate
   (`grievance_redactions` rows in the slice with no `dedup_signatures` row
   yet), same pattern as `redact_grievance._load_pending_batch`. For each
   ticket: `strip_placeholders` + shingle + MinHash the redacted text
   (module docstring point 2 of `dedup.py` — skipping this step makes every
   redacted phone number the same `[PHONE]` shingle and inflates similarity
   between unrelated filings), detect script, compute a time-window block
   key, and separately compute salted identity keys from
   `petitioner_mobile`/`petitioner_email` — a path that never touches the
   redacted text and never feeds `shingles()` (dedup.py module docstring
   point 3; see `identity_key()`'s contract). Writes `dedup_signatures`.
2. `_group_duplicates()` — not batched. It loads every signature already
   written for the slice (tens of thousands of rows, comfortably in memory),
   buckets by `(block_key, lsh band)` to find text candidates, adds
   identity-key equality as a second, unblocked source of candidates
   (same-citizen resubmission does not need to fall in the same time
   window), re-fetches redacted text for the tickets each bucket needs, and
   verifies candidate pairs — text and identity alike — with
   `jaccard_similarity` before a pair is allowed to union (dedup.py module
   docstring point 6 — a shared LSH band *or* a shared identity is a
   candidate, not a confirmed duplicate: the same citizen filing two
   unrelated grievances is "same citizen", not "same issue"). Recomputed and
   upserted whole every run: cheap at this scale, and correct where an
   incremental update would drift (a late resubmission can change a group id
   that already exists).

   **Above `REPRESENTATIVE_COMPARISON_CAP` members, a bucket trades recall
   for time (#158).** A campaign-heavy district-year produces buckets in the
   thousands of members — 6,797 was the largest measured on Sambalpur 2024,
   with `itertools.combinations` alone generating 23 million pairs for that
   one bucket even though union-find's `find(a) == find(b)` short-circuit
   skips verifying most of them once a few unions have collapsed the
   component. Below the cap, `_verify_bucket` still does what #101
   established: every unordered pair in the bucket, via
   `itertools.combinations`, because a star topology (compare everything to
   one arbitrary first member) can leave two genuine near-duplicates
   elsewhere in the bucket uncompared with each other. At or above the cap,
   it instead exhaustively compares a fixed, deterministic set of anchor
   tickets, then compares every non-anchor against every anchor — O(members),
   with a hard, auditable comparison bound.
   **This is a real, accepted recall loss, not just a constant-factor
   trade:** a member that would verify only against another non-anchor ticket
   is missed and stays in its own group (or merges into a different one)
   instead of joining that component. `tests/test_dedup_index.py` pins the
   case directly — a duplicate which matches only another non-anchor ticket
   is deliberately not found — as documented behavior, not a bug.

**Blocked by district and time window, not compared pairwise.** A single
district-year slice runs to tens of thousands of rows — 55,544 for
Sambalpur 2024, ~1.5 billion unordered pairs if compared directly. Each
signature is assigned a `block_key` of `district:script:window_index`
(`window_index` is `(created_on - Jan 1 of --year).days // --window-days`,
`None`/"undated" for complaints with no `created_on`), and LSH candidate
generation only buckets within one block. Only pairs that land in the same
block can ever become candidates.

**Cross-script recall is explicitly unsupported.** `script` (`"odia"` if the
redacted text contains any Odia-script codepoint, else `"latin"`) is part of
the block key, so an Odia-script filing and a romanized-Odia filing are
never bucketed together and never compared — on top of the fact that
character shingles across the two scripts already share ~zero n-grams by
construction (disjoint Unicode ranges; see `dedup.py` module docstring point
4). This is a known, accepted gap: the transliteration step that would close
it is Phase 17, which runs after this phase. Do not "fix" this by comparing
across the block boundary.

**A matching identity key is a candidate, not duplicate evidence.** Same
mobile/email links two tickets to the same citizen, not necessarily the same
issue — a repeat filer with a water complaint and, months later, an
unrelated pension complaint must not be folded into one duplicate group.
"Duplicate-adjusted workload" is a number people act on (DELIVERY.md;
ROADMAP §5.2 warns deduplication destructively changes management's
counts), so identity equality only widens candidate generation across time
windows and scripts — the same `jaccard_similarity` check on the redacted
text that LSH candidates go through still decides whether the pair actually
unions. A "same citizen, different issue" pair that fails that check is not
discarded: `identity_key_mobile`/`identity_key_email` stay on every
`dedup_signatures` row, so the relationship is directly queryable by joining
on those columns — real signal for a spike-decomposition consumer, just not
this table's duplicate signal.

**`dpic-infra` classified.** Redaction lowers exposure; it does not
declassify what is derived from citizen prose. A MinHash signature or a
salted identity key is not directly readable, but distinctive phrasing
re-identifies a filing where a phone number no longer does, and a duplicate
group id links records the way a shared identity would (ROADMAP §3.2). Both
`dedup_signatures` and `dedup_groups` carry the same classification as the
raw lake: DPIC-controlled machines only, never an `authorized-external`
route, inside Phase 18's RBAC and audit scope once that lands. They are
deliberately **not** listed in `olap.materialize.LAKE_TABLES` — see the
comment on that tuple for the reasoning (the Parquet lake is not
access-controlled per column/table the way the OLTP store is, and widening
distribution of a derived-PII artifact ahead of Phase 18's RBAC is not this
issue's call to make).

**Identity keys need a salt.** `--salt` overrides `settings.DEDUP_SALT`
(`DEDUP_SALT` in the environment/`.env`); `build_dedup_index()` refuses to
start — before opening any DB connection — if neither is set, the same
"fail loud on a deployment-level problem" stance `identity_key()` itself
takes for a blank salt.

Run:

    uv run --extra pipeline-core janasunani-dedup-index \\
        --district Sambalpur --year 2024
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
from itertools import combinations
from typing import Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from janasunani.config import settings
from janasunani.db.models import Complaint, DedupGroup, DedupSignature, GrievanceRedaction
from janasunani.pipeline.dedup import (
    DEFAULT_NUM_BANDS,
    DEFAULT_NUM_HASHES,
    DEFAULT_SHINGLE_SIZE,
    identity_key,
    jaccard_similarity,
    lsh_bands,
    minhash_signature,
    shingles,
)

# Reuses the exporter's helper rather than writing a third upsert — same
# reasoning as redact_grievance.py.
from janasunani.pipeline.export import _dialect_upsert

BATCH_SIZE = 500


# Time-window blocking width. Campaigns and resubmissions cluster within
# days to a few weeks of each other (a citizen re-files, or many filers
# submit the same near-identical text in a burst); a filing from January
# and one from November within the same district-year are not usefully
# compared for near-duplicate text either way, so a 30-day window keeps
# blocks small without splitting a real burst across a boundary in the
# typical case. Tunable via --window-days; not load-bearing for identity-key
# matching, which is not blocked by time (see module docstring).
DEFAULT_WINDOW_DAYS = 30

# Jaccard threshold a candidate pair must clear to union -- LSH and identity
# candidates alike (see _group_duplicates). Matches the threshold used in
# tests/test_dedup.py's end-to-end candidate -> verify -> union example:
# comfortably above the ~0.3 two unrelated filings share via boilerplate
# alone, comfortably below the ~0.78 a lightly-reworded duplicate scores
# (see dedup.py's DEFAULT_NUM_BANDS comment for both numbers). Must be a
# finite value in [0, 1] -- build_dedup_index() enforces this before
# opening a DB connection, since this backfill persists what it computes.
DEFAULT_DUPLICATE_THRESHOLD = 0.5

# Bucket size at which `_verify_bucket` switches from exhaustive all-pairs
# comparison to fixed-anchor comparison (#158; see the module
# docstring for the recall trade this makes). The Sambalpur 2024 measurement
# that motivated this had 46,191 non-singleton buckets and only a handful of
# them -- the campaign-driven ones -- above a few thousand members; the top
# eight were 4,965-6,797. There is no observed middle ground between
# "ordinary duplicate cluster" (a handful of members) and "campaign bucket"
# (thousands), so 200 sits solidly above anything that looks like ordinary
# near-duplicate traffic -- keeping #101's all-pairs guarantee for it, where
# the cost is a few tens of thousands of comparisons at worst -- and solidly
# below every pathological bucket actually measured, so the fixed-anchor
# trade only ever applies where the alternative is tens of millions of
# generated pairs.
REPRESENTATIVE_COMPARISON_CAP = 200

# Large buckets compare against these deterministic anchors, rather than a
# representative list that can grow with every distinct member.  Keeping the
# anchor count separate from the switch point is important: the latter
# protects #101's exact behavior for ordinary buckets; this one is the actual
# linear-work budget for campaign buckets.
LARGE_BUCKET_ANCHOR_COUNT = 32

# Stable provenance marker for the bounded grouping policy. Increment this
# when the algorithm changes even if the cap/anchor defaults do not.
GROUPING_ALGORITHM = "fixed-anchor-v1"

# Odia Unicode block. Presence, not majority: any real Odia content in a
# filing is enough to route it to the Odia-script partition rather than the
# Latin one, and typed PII placeholders / stray punctuation are ASCII
# either way and never tip the balance.
_ODIA_BLOCK_START = 0x0B00
_ODIA_BLOCK_END = 0x0B7F


def _script_of(text: str) -> str:
    """``"odia"`` if ``text`` contains any Odia-script codepoint, else
    ``"latin"`` — the partition `_block_key` blocks on so an Odia-script and
    a romanized-Odia filing are never compared (module docstring)."""
    return (
        "odia"
        if any(_ODIA_BLOCK_START <= ord(ch) <= _ODIA_BLOCK_END for ch in text)
        else "latin"
    )


def _window_index(
    created_on: Optional[datetime], epoch: date, window_days: int
) -> Optional[int]:
    """Bucket ``created_on`` into a time-window index relative to ``epoch``
    (Jan 1 of the slice's ``--year``). ``None`` for a missing timestamp —
    deliberately not bucket 0, so an undated row does not silently
    block-match every row that happens to fall in the first window; see
    `_block_key`'s "undated" label."""
    if created_on is None:
        return None
    return (created_on.date() - epoch).days // window_days


def _block_key(district: str, script: str, window_index: Optional[int]) -> str:
    window_label = "undated" if window_index is None else str(window_index)
    return f"{district}:{script}:{window_label}"


def _salt_marker(salt: str) -> str:
    """A one-way marker identifying *which* salt produced a row (#136).

    The salt itself must never reach a database column -- it is the secret
    that stops stored identity hashes being reversed, and a column holding it
    beside the hashes it protects defeats the point. A truncated digest says
    "these rows came from a different salt than the current one" without
    saying anything about either.
    """
    return hashlib.blake2b(salt.encode("utf-8"), digest_size=6).hexdigest()


def _index_version(
    window_days: int,
    threshold: float,
    salt: str,
    *,
    grouping_algorithm: Optional[str] = None,
    representative_cap: Optional[int] = None,
    anchor_count: Optional[int] = None,
) -> str:
    """Stamp identifying the parameters a signature/group row was produced
    under — same purpose as `redact_grievance._analyzer_version`, scaled down:
    `dedup.py` is stdlib-only with no third-party package versions to track,
    but its own constants and this runner's blocking/verification parameters
    can still change what the index contains.

    With no grouping keywords, this returns the original signature provenance
    format unchanged. Group rows supply all three grouping keywords, appending
    the stable algorithm marker and effective bounded-policy parameters. This
    lets signatures remain current when only grouping changes, while group
    outputs from exhaustive and bounded policies cannot share a version stamp.

    The salt marker is included because rotating the salt changes every
    identity hash. Without it a rotation is undetectable: the compromised
    hashes stay stored, and new complaints hash under the new salt while old
    ones keep the old, so same-citizen linkage silently stops working across
    the boundary with no error and no visible symptom (#136).
    """
    base = (
        f"shingle_size={DEFAULT_SHINGLE_SIZE} num_hashes={DEFAULT_NUM_HASHES} "
        f"num_bands={DEFAULT_NUM_BANDS} window_days={window_days} "
        f"threshold={threshold} salt={_salt_marker(salt)}"
    )
    grouping_values = (grouping_algorithm, representative_cap, anchor_count)
    if all(value is None for value in grouping_values):
        return base
    if any(value is None for value in grouping_values):
        raise ValueError("grouping provenance requires algorithm, cap, and anchor count")
    return (
        f"{base} grouping_algorithm={grouping_algorithm} "
        f"representative_cap={representative_cap} anchor_count={anchor_count}"
    )


# --- stage 1: signatures ---------------------------------------------------


async def _count_signature_slice(conn, district: str, year: int) -> tuple[int, int]:
    """(redacted complaints in the slice, already indexed)."""
    total = await conn.scalar(
        select(func.count())
        .select_from(GrievanceRedaction)
        .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
        .where(
            Complaint.district == district,
            Complaint.created_year == year,
            GrievanceRedaction.grievance_redacted.isnot(None),
        )
    )
    done = await conn.scalar(
        select(func.count())
        .select_from(DedupSignature)
        .where(
            DedupSignature.district == district,
            DedupSignature.created_year == year,
        )
    )
    return int(total or 0), int(done or 0)


async def _count_stale_signatures(conn, district: str, year: int, version: str) -> int:
    """Signatures produced under different parameters than the current ones.

    Reported by every run whether or not it acts on them, so a salt rotation
    or a window change is visible rather than something you have to think to
    check (#136).
    """
    return int(
        await conn.scalar(
            select(func.count())
            .select_from(DedupSignature)
            .join(Complaint, Complaint.ticket_no == DedupSignature.ticket_no)
            .where(
                Complaint.district == district,
                Complaint.created_year == year,
                DedupSignature.index_version != version,
            )
        )
        or 0
    )


async def _load_pending_signature_batch(
    conn, district: str, year: int, limit: int, version: str | None = None
):
    """Redacted complaints in the slice with no current `dedup_signatures` row.

    Selects `GrievanceRedaction.grievance_redacted` — never
    `Complaint.grievance` — joined to `complaints` only for the structured
    columns (district, created_on, petitioner_mobile/email) the signature
    row and the identity keys need. The pending predicate is the resume
    mechanism (same reasoning as `redact_grievance._load_pending_batch`): it
    must stay a NOT EXISTS against the output table, not an offset.
    """
    done = select(DedupSignature.ticket_no).where(
        DedupSignature.ticket_no == GrievanceRedaction.ticket_no
    )
    if version is not None:
        # A row stamped with different parameters counts as pending (#136).
        # Without this the stamp is write-only: rotating the salt leaves every
        # compromised identity hash in place, and identity linkage silently
        # breaks across the rotation boundary.
        done = done.where(DedupSignature.index_version == version)
    stmt = (
        select(
            GrievanceRedaction.ticket_no,
            GrievanceRedaction.grievance_redacted,
            Complaint.district,
            Complaint.created_on,
            Complaint.petitioner_mobile,
            Complaint.petitioner_email,
        )
        .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
        .where(
            Complaint.district == district,
            Complaint.created_year == year,
            GrievanceRedaction.grievance_redacted.isnot(None),
            ~done.exists(),
        )
        .order_by(GrievanceRedaction.ticket_no)
        .limit(limit)
    )
    result = await conn.execute(stmt)
    return result.all()


async def _index_signatures(
    engine: AsyncEngine,
    district: str,
    year: int,
    salt: str,
    window_days: int,
    threshold: float,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
) -> dict[str, int]:
    version = _index_version(window_days, threshold, salt)
    epoch = date(year, 1, 1)
    processed = 0

    async with engine.begin() as conn:
        total, already = await _count_signature_slice(conn, district, year)
        stale = await _count_stale_signatures(conn, district, year, version)
    logger.info(
        "slice {}/{}: {} redacted complaints, {} already indexed",
        district,
        year,
        total,
        already,
    )
    if stale:
        logger.warning(
            "{} signature(s) were built under different parameters than this "
            "run. {}",
            stale,
            "Reprocessing them (--refresh-stale)."
            if refresh_stale
            else "Leaving them; pass --refresh-stale to rebuild. After a salt "
            "rotation this is not optional -- the old identity hashes stay "
            "stored and linkage breaks across the boundary (#136).",
        )

    while True:
        remaining = None if limit is None else limit - processed
        if remaining is not None and remaining <= 0:
            break
        size = BATCH_SIZE if remaining is None else min(BATCH_SIZE, remaining)

        async with engine.begin() as conn:
            batch = await _load_pending_signature_batch(
                conn, district, year, size, version=version if refresh_stale else None
            )
            if not batch:
                break

            # Naive UTC — every timestamp column in this schema is TIMESTAMP
            # WITHOUT TIME ZONE and asyncpg refuses a tz-aware value there,
            # while SQLite silently accepts one. Same normalisation as
            # redact_grievance.py / db/crud.py.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            rows = []
            for ticket_no, redacted_text, row_district, created_on, mobile, email in batch:
                text = redacted_text or ""
                shingle_set = shingles(text)
                signature = minhash_signature(shingle_set, num_hashes=DEFAULT_NUM_HASHES)
                script = _script_of(text)
                window_index = _window_index(created_on, epoch, window_days)
                rows.append(
                    {
                        "ticket_no": ticket_no,
                        "district": row_district,
                        "created_year": year,
                        "script": script,
                        "window_index": window_index,
                        "block_key": _block_key(row_district, script, window_index),
                        "num_shingles": len(shingle_set),
                        "signature": list(signature) if signature is not None else None,
                        # A separate path from `text` above: computed from
                        # the complaints columns directly, never from
                        # redacted_text (dedup.py module docstring point 3).
                        "identity_key_mobile": identity_key(mobile, salt) if mobile else None,
                        "identity_key_email": identity_key(email, salt) if email else None,
                        "index_version": version,
                        "indexed_at": now,
                    }
                )
            await conn.execute(
                _dialect_upsert(DedupSignature, conn.dialect.name, rows, "ticket_no")
            )

        processed += len(batch)
        logger.info(
            "indexed {} of {} ({} this batch)", processed + already, total, len(batch)
        )

    return {
        "total": total,
        "already_indexed": already,
        "processed": processed,
        "stale_at_start": stale,
    }


# --- stage 2: grouping ------------------------------------------------------


async def _load_slice_signatures(conn, district: str, year: int):
    stmt = select(
        DedupSignature.ticket_no,
        DedupSignature.block_key,
        DedupSignature.signature,
        DedupSignature.identity_key_mobile,
        DedupSignature.identity_key_email,
    ).where(
        DedupSignature.district == district,
        DedupSignature.created_year == year,
    )
    result = await conn.execute(stmt)
    return result.all()


async def _load_redacted_text(conn, ticket_nos: list[str]) -> dict[str, str]:
    """Redacted text for exactly the tickets an LSH candidate pair needs
    verifying — not the whole slice. `dedup_signatures` deliberately does
    not persist shingle sets (that would duplicate most of the text itself
    into a second table), so verification re-fetches from
    `grievance_redactions`, the one place this module ever reads grievance
    text from."""
    text_by_ticket: dict[str, str] = {}
    for i in range(0, len(ticket_nos), BATCH_SIZE):
        chunk = ticket_nos[i : i + BATCH_SIZE]
        stmt = select(
            GrievanceRedaction.ticket_no, GrievanceRedaction.grievance_redacted
        ).where(GrievanceRedaction.ticket_no.in_(chunk))
        result = await conn.execute(stmt)
        text_by_ticket.update({t: g or "" for t, g in result.all()})
    return text_by_ticket


def _text_candidate_pairs(rows, num_bands: int) -> set[tuple[str, str]]:
    """LSH candidate pairs, blocked by `block_key` — a shared band hash
    within the same block is a candidate, never across blocks. Rows with no
    signature (`minhash_signature` abstained) are skipped: there is nothing
    to band (dedup.py module docstring point 5).

    Every unordered pair within a bucket is emitted, via
    `itertools.combinations` — not just edges from an arbitrary first
    member. LSH membership has no "first": a star topology (first-to-other
    only) would leave two genuine near-duplicates elsewhere in a
    3+-member bucket uncompared with each other whenever neither happens to
    verify against whichever ticket landed first in iteration order.
    """
    buckets: dict[tuple[str, int, int], list[str]] = defaultdict(list)
    for row in rows:
        if row.signature is None:
            continue
        signature = tuple(row.signature)
        for band_index, band_hash in enumerate(lsh_bands(signature, num_bands=num_bands)):
            buckets[(row.block_key, band_index, band_hash)].append(row.ticket_no)

    pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) > 1:
            pairs.update(combinations(sorted(set(members)), 2))
    return pairs


def _identity_candidate_pairs(rows) -> set[tuple[str, str]]:
    """Same-citizen pairs from identity-key equality — unblocked by time
    window or script on purpose (module docstring): a resubmission can land
    months later, and a citizen's phone number does not carry a script.

    **Candidates, not confirmed duplicates.** A matching identity means the
    same citizen, not the same issue — a repeat filer with a water complaint
    and, months later, an unrelated pension complaint shares an identity key
    but is not a duplicate. `_group_duplicates` runs these pairs through the
    same `jaccard_similarity` check as LSH text candidates before any of
    them are allowed to union; identity equality only widens the search
    (across time/script), it does not decide the match on its own (module
    docstring). A pair that fails that check is not lost information —
    `identity_key_mobile`/`identity_key_email` remain on every
    `dedup_signatures` row, so "same citizen, different issue" stays
    directly queryable even when it is not folded into a duplicate group.

    Every unordered pair within one identity group is emitted (same
    `itertools.combinations` reasoning as `_text_candidate_pairs` — star
    edges under-connect once every pair needs its own verification, not
    just connectivity).
    """
    pairs: set[tuple[str, str]] = set()
    for key_of in (lambda r: r.identity_key_mobile, lambda r: r.identity_key_email):
        groups: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            key = key_of(row)
            if key is not None:
                groups[key].append(row.ticket_no)
        for members in groups.values():
            if len(members) > 1:
                pairs.update(combinations(sorted(set(members)), 2))
    return pairs


def _candidate_buckets(rows, num_bands: int) -> list[tuple[Optional[str], list[str]]]:
    """Candidate buckets as ``(block_key, members)``, band and identity
    together.

    Returns buckets rather than the pairs inside them. A bucket is quadratic
    in its membership, so materialising its pairs is what exhausted memory on
    the Sambalpur slice; the caller streams each bucket and unions as it goes.

    Band buckets are keyed by block, so they respect district/script/time
    blocking, and carry that block's key in the returned tuple. `_group_duplicates`
    uses the key to organize overlapping bands before fetching all candidate
    text once (#158), rather than once per bucket. Identity
    buckets deliberately are not blocked: a resubmission can land months
    later and a phone number carries no script (module docstring). They come
    back with ``None`` in place of a block key -- there is no single block to
    cache text against, since one identity bucket's members can span the
    whole slice.

    Singleton buckets are dropped -- no pairs, and they would only cost a
    round trip.

    ``_text_candidate_pairs`` and ``_identity_candidate_pairs`` are retained
    as the pure, directly-testable statement of what a bucket means, including
    the all-pairs invariant from #101. This function is the streaming path
    that production uses.
    """
    band_buckets: dict[tuple[str, int, int], list[str]] = defaultdict(list)
    for row in rows:
        if row.signature is None:
            continue
        signature = tuple(row.signature)
        for band_index, band_hash in enumerate(lsh_bands(signature, num_bands=num_bands)):
            band_buckets[(row.block_key, band_index, band_hash)].append(row.ticket_no)

    identity_buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        for kind, key in (
            ("mobile", row.identity_key_mobile),
            ("email", row.identity_key_email),
        ):
            if key is not None:
                identity_buckets[(kind, key)].append(row.ticket_no)

    return [
        (block_key, members)
        for (block_key, _band_index, _band_hash), members in band_buckets.items()
        if len(set(members)) > 1
    ] + [(None, members) for members in identity_buckets.values() if len(set(members)) > 1]


def _find(parent: dict[str, str], item: str) -> str:
    """Union-find lookup with path compression, over a `parent` dict shared
    across the whole grouping run (module-level so `_verify_bucket` and
    `_group_duplicates` share the exact same implementation, not a copy each
    with its own closure)."""
    parent.setdefault(item, item)
    root = item
    while parent[root] != root:
        root = parent[root]
    while parent[item] != root:
        parent[item], item = root, parent[item]
    return root


def _union(parent: dict[str, str], a: str, b: str) -> None:
    """Union the components containing ``a`` and ``b``. A no-op if they are
    already the same component -- callers still call this unconditionally
    after a passing verification; the check that makes repeat unions cheap
    lives in `_find`'s path compression, not here."""
    root_a, root_b = _find(parent, a), _find(parent, b)
    if root_a != root_b:
        parent[root_a] = root_b


def _verify_bucket(
    members: list[str],
    text_by_ticket: dict[str, str],
    shingle_cache: dict[str, set[str]],
    parent: dict[str, str],
    threshold: float,
    cap: int = REPRESENTATIVE_COMPARISON_CAP,
    anchor_count: int = LARGE_BUCKET_ANCHOR_COUNT,
) -> tuple[int, int, bool]:
    """Verify Jaccard similarity for one candidate bucket and union whatever
    passes ``threshold``. Returns ``(duplicate_pairs, comparisons,
    used_large_bucket_policy)`` for per-run audit metadata.

    ``members`` must already be deduplicated (the caller's `sorted(set(...))`
    -- see `_group_duplicates`). ``shingle_cache`` is a per-ticket memo the
    caller controls the lifetime of, so text loaded once can be reused across
    every bucket sharing that cache (#158 -- see `_group_duplicates`'s
    per-block cache).

    Below ``cap`` members: exhaustive all-pairs, via `itertools.combinations`
    -- #101's invariant. A star topology (compare everything to one arbitrary
    first member) can leave two genuine near-duplicates elsewhere in the
    bucket uncompared with each other, so every unordered pair is checked,
    same as before #158. Every pair is actually scored, even after an earlier
    union: #101's all-pairs correctness contract is about verification, not
    merely the final connectivity.

    At ``cap`` members or more: fixed-anchor comparison (#158; see the module
    docstring for the full rationale and the accepted recall trade). The first
    ``anchor_count`` sorted tickets are deterministic anchors. Every unordered
    anchor pair is scored once, then every remaining member is scored against
    every anchor, including when it already matched another anchor. Exact work
    is ``C(anchor_count, 2) + anchor_count * (members - anchor_count)``, at most
    ``anchor_count * len(members)``. Only non-anchor/non-anchor pairs are
    omitted. This is deliberately not an adaptive representative list: an
    adversarial bucket of unrelated filings must not quietly turn the
    large-bucket path back into quadratic work.
    """

    def shingles_for(ticket: str) -> set[str]:
        cached = shingle_cache.get(ticket)
        if cached is None:
            cached = shingles(text_by_ticket.get(ticket, ""))
            shingle_cache[ticket] = cached
        return cached

    verified = 0
    comparisons = 0

    if len(members) < cap:
        for a, b in combinations(members, 2):
            comparisons += 1
            if jaccard_similarity(shingles_for(a), shingles_for(b)) >= threshold:
                _union(parent, a, b)
                verified += 1
        return verified, comparisons, False

    # Leave at least one non-anchor to score. This also keeps the test-only
    # ability to lower ``cap`` useful for a two-member bucket.
    anchors = members[: min(anchor_count, len(members) - 1)]
    for a, b in combinations(anchors, 2):
        comparisons += 1
        if jaccard_similarity(shingles_for(a), shingles_for(b)) >= threshold:
            _union(parent, a, b)
            verified += 1
    for member in members[len(anchors) :]:
        for anchor in anchors:
            comparisons += 1
            if jaccard_similarity(shingles_for(anchor), shingles_for(member)) >= threshold:
                _union(parent, anchor, member)
                verified += 1
    return verified, comparisons, True


async def _group_duplicates(
    engine: AsyncEngine,
    district: str,
    year: int,
    window_days: int,
    threshold: float,
    salt: str,
    representative_cap: int = REPRESENTATIVE_COMPARISON_CAP,
    anchor_count: int = LARGE_BUCKET_ANCHOR_COUNT,
) -> dict[str, int]:
    async with engine.begin() as conn:
        rows = await _load_slice_signatures(conn, district, year)

    if not rows:
        return {
            "slice_signatures": 0,
            "verified_pairs": 0,
            "comparison_pairs": 0,
            "large_buckets": 0,
            "groups": 0,
        }

    # Both sources are candidates only, never confirmed duplicates on their
    # own: an LSH band collision needs Jaccard verification (dedup.py module
    # docstring point 6), and so does an identity-key match, which means
    # "same citizen", not "same issue" (see _identity_candidate_pairs). One
    # verification pass covers both, in `_verify_bucket`.
    # Pairs are streamed per bucket and unioned immediately, never collected.
    #
    # The Sambalpur 2024 run OOM-killed twice at 7.4 GB on an 8 GB box, after
    # all 55,544 signatures had been written. The allocation was the pair set,
    # not the text: a bucket is quadratic in its membership and this slice has
    # a 9,405-row block, so one campaign bucket alone is tens of millions of
    # pairs. Grievance subjects are a couple of hundred characters, so every
    # text in the slice together is only megabytes.
    #
    # That fixed memory; it left runtime quadratic (#158). Two changes here:
    # `_verify_bucket` (above) stops generating every pair once a bucket
    # crosses `representative_cap`, and this loop fetches each candidate
    # ticket once for all its overlapping LSH and identity buckets. Text and
    # shingle memory are linear in candidate tickets, not candidate pairs.
    parent: dict[str, str] = {}

    band_buckets_by_block: dict[str, list[list[str]]] = defaultdict(list)
    identity_buckets: list[list[str]] = []
    for block_key, members in _candidate_buckets(rows, DEFAULT_NUM_BANDS):
        unique = sorted(set(members))
        if block_key is None:
            identity_buckets.append(unique)
        else:
            band_buckets_by_block[block_key].append(unique)

    # Text is small relative to the former pair set. Fetch each candidate
    # ticket once, in BATCH_SIZE chunks, then reuse it across overlapping LSH
    # bands *and* identity buckets. This makes database reads linear in unique
    # candidate tickets rather than one query sequence per candidate bucket.
    candidate_tickets = sorted(
        {
            ticket
            for buckets in band_buckets_by_block.values()
            for members in buckets
            for ticket in members
        }
        | {ticket for members in identity_buckets for ticket in members}
    )
    async with engine.begin() as conn:
        text_by_ticket = await _load_redacted_text(conn, candidate_tickets)

    verified = 0
    comparisons = 0
    large_buckets = 0
    shingle_cache: dict[str, set[str]] = {}
    for buckets_in_block in band_buckets_by_block.values():
        for members in buckets_in_block:
            matches, checked, used_large_policy = _verify_bucket(
                members,
                text_by_ticket,
                shingle_cache,
                parent,
                threshold,
                representative_cap,
                anchor_count,
            )
            verified += matches
            comparisons += checked
            large_buckets += used_large_policy

    # Identity buckets are unblocked by construction (module docstring), but
    # share the same candidate-text/shingle cache as LSH buckets above.
    for members in identity_buckets:
        matches, checked, used_large_policy = _verify_bucket(
            members,
            text_by_ticket,
            shingle_cache,
            parent,
            threshold,
            representative_cap,
            anchor_count,
        )
        verified += matches
        comparisons += checked
        large_buckets += used_large_policy

    all_tickets = [row.ticket_no for row in rows]
    # Singletons still need a group id, so every ticket is seeded through find().
    groups = {ticket: _find(parent, ticket) for ticket in all_tickets}
    group_sizes = Counter(groups.values())

    version = _index_version(
        window_days,
        threshold,
        salt,
        grouping_algorithm=GROUPING_ALGORITHM,
        representative_cap=representative_cap,
        anchor_count=anchor_count,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    block_key_by_ticket = {row.ticket_no: row.block_key for row in rows}
    group_rows = [
        {
            "ticket_no": ticket_no,
            "district": district,
            "created_year": year,
            "block_key": block_key_by_ticket[ticket_no],
            "duplicate_group_id": group_id,
            "group_size": group_sizes[group_id],
            "index_version": version,
            "grouped_at": now,
        }
        for ticket_no, group_id in groups.items()
    ]

    async with engine.begin() as conn:
        for i in range(0, len(group_rows), BATCH_SIZE):
            chunk = group_rows[i : i + BATCH_SIZE]
            await conn.execute(
                _dialect_upsert(DedupGroup, conn.dialect.name, chunk, "ticket_no")
            )

    return {
        "slice_signatures": len(rows),
        "verified_pairs": verified,
        "comparison_pairs": comparisons,
        "large_buckets": large_buckets,
        "groups": len(group_sizes),
    }


# --- orchestration -----------------------------------------------------


def build_dedup_index(
    district: str,
    year: int,
    oltp_url: Optional[str] = None,
    salt: Optional[str] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
    representative_cap: int = REPRESENTATIVE_COMPARISON_CAP,
    anchor_count: int = LARGE_BUCKET_ANCHOR_COUNT,
) -> dict[str, int]:
    """Index one district-year slice and (re)compute its duplicate groups.

    Returns per-run counts from both stages. Raises ``ValueError``
    immediately — before opening any DB connection — if no salt is
    configured, or if ``threshold`` is not a finite value in ``[0, 1]``; see
    the module docstring.

    ``representative_cap`` overrides `REPRESENTATIVE_COMPARISON_CAP` (#158)
    -- the bucket-size threshold above which grouping trades recall for
    time. Exposed mainly for tests that need to force one path or the other
    deterministically; production runs should use the default.
    """
    effective_salt = salt if salt is not None else settings.DEDUP_SALT
    if not effective_salt or not effective_salt.strip():
        raise ValueError(
            "build_dedup_index() requires a non-blank salt: set DEDUP_SALT in "
            "the deployment environment/.env, or pass salt= explicitly. A "
            "blank/missing salt would make identity_key() offline-invertible "
            "over the ~10^10 mobile-number space -- see identity_key()'s "
            "docstring in janasunani.pipeline.dedup. Fix the deployment "
            "config; do not retry with an empty string."
        )

    # This backfill persists duplicate_group_id, not just a report, so a bad
    # threshold corrupts the slice instead of failing fast: -1 unions every
    # LSH/identity candidate as a duplicate, and >1 or NaN rejects every real
    # one silently (NaN comparisons are just always False). Same "fail loud
    # before touching the DB" stance as the salt check above.
    if not math.isfinite(threshold) or not (0.0 <= threshold <= 1.0):
        raise ValueError(
            f"build_dedup_index() requires threshold in [0, 1], got "
            f"{threshold!r}. Jaccard similarity is only ever in [0, 1]; a "
            "value outside that range is a misconfiguration, not a stricter "
            "or looser real threshold."
        )

    engine = create_async_engine(oltp_url or settings.OLTP_DB_URL)

    async def run() -> dict[str, int]:
        try:
            signature_counts = await _index_signatures(
                engine,
                district,
                year,
                effective_salt,
                window_days,
                threshold,
                limit=limit,
                refresh_stale=refresh_stale,
            )
            group_counts = await _group_duplicates(
                engine,
                district,
                year,
                window_days,
                threshold,
                effective_salt,
                representative_cap=representative_cap,
                anchor_count=anchor_count,
            )
            return {**signature_counts, **group_counts}
        finally:
            await engine.dispose()

    return asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the MinHash/LSH dedup index over grievance_redacted "
            "text for one district-year slice, and persist the resulting "
            "duplicate groups."
        )
    )
    parser.add_argument("--district", required=True, help="District name, as stored in complaints.district.")
    parser.add_argument("--year", required=True, type=int, help="created_year to process.")
    parser.add_argument(
        "--oltp-url", default=None, help="OLTP DB URL (default: settings.OLTP_DB_URL)."
    )
    parser.add_argument(
        "--salt",
        default=None,
        help="Overrides DEDUP_SALT from settings/.env. Prefer the environment; a "
        "CLI flag lands in shell history.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="Time-window width (days) for blocking, within the district-year slice.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_DUPLICATE_THRESHOLD,
        help="Jaccard similarity an LSH/identity candidate pair must clear to be "
        "unioned. Must be in [0, 1].",
    )
    parser.add_argument(
        "--limit",
        default=None,
        type=int,
        help="Stop signature indexing after this many complaints. For a smoke "
        "test before the full run.",
    )
    parser.add_argument(
        "--refresh-stale",
        action="store_true",
        help=(
            "Also rebuild signatures produced under different parameters "
            "(salt, window, threshold, or dedup.py's own constants). Off by "
            "default: a backfill over derived citizen data should not change "
            "scope because an unrelated value moved. Required after a salt "
            "rotation, or the old identity hashes stay stored."
        ),
    )
    args = parser.parse_args()

    counts = build_dedup_index(
        args.district,
        args.year,
        oltp_url=args.oltp_url,
        salt=args.salt,
        window_days=args.window_days,
        threshold=args.threshold,
        limit=args.limit,
        refresh_stale=args.refresh_stale,
    )
    logger.info(
        "done: {} processed this run, {} of {} indexed, {} duplicate groups "
        "over {} signatures (comparison_pairs={}, large_buckets={}) in slice {}/{}",
        counts["processed"],
        counts["already_indexed"] + counts["processed"],
        counts["total"],
        counts["groups"],
        counts["slice_signatures"],
        counts["comparison_pairs"],
        counts["large_buckets"],
        args.district,
        args.year,
    )


if __name__ == "__main__":
    main()
