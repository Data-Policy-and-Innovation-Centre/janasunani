"""Dedup index backfill runner (Phase 14, W6 — #71).

`pipeline/dedup.py` provides the algorithmic primitives (MinHash, LSH,
Jaccard verification, union-find) and is deliberately stdlib-only. Nothing
there walks a slice, computes signatures, buckets them and persists a
result — that is what this module does, one stage after
`janasunani-redact-grievance` in the backfill order ROADMAP §5.2 lays out:

    janasunani-redact-grievance -> dedup index build (this) -> spam_duplicate scoring

Mirrors `redact_grievance.py`'s shape on purpose (argparse entrypoint,
`def main() -> None` wrapping `asyncio.run`, its own `create_async_engine`,
scope flags with no defaults, batched and resumable): they are the same kind
of job, one stage apart, over the same records. It diverges in one place:
this runner also accepts `--all`, because dedup is the only stage whose
answer depends on which *other* records are in scope.

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
   written for the scope (tens of thousands of rows for a district-year,
   comfortably in memory; 1.37M for `--all`, where that claim has not been
   verified and #317 tracks measuring it),
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

   **Source provenance is persisted with every group row (#137).** The
   current runner deliberately keeps the short redaction → index chain and
   reads `complaints` + `grievance_redactions` directly from OLTP.  Before it
   writes groups, it fingerprints the exact source records represented by the
   signature slice.  The resulting `source_name` and `source_snapshot_id` are
   deterministic and can be recomputed against a materialized lake by a
   duplicate-adjusted analytics consumer.  That consumer must assert the
   match before aggregation; a stale/incomplete lake or a mixture of group
   runs is an error, not a number to publish.

   The digest is per district-year, never per run, so a corpus-wide run
   still stamps each row with the digest of the slice that row belongs to
   and slice-scoped consumers keep verifying (`_source_snapshots_by_slice`).

   **One consequence of corpus grouping, for whoever reads these counts.**
   A group whose members straddle two district-years is counted as a
   distinct problem in *both* when each is aggregated on its own. That is
   the right answer per slice — each really did receive a filing about that
   problem — but it means per-slice distinct-problem counts no longer sum
   to the corpus figure. Report the corpus number from a corpus
   aggregation, not by adding slices up.

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
(`window_index` is `(created_on - DEDUP_WINDOW_EPOCH).days // --window-days`,
`None`/"undated" for complaints with no `created_on`), and LSH candidate
generation only buckets within one block. Only pairs that land in the same
block can ever become candidates. The epoch is a fixed absolute date, not
the run's own scope, so a record's block key is a property of the record;
it is part of `_index_version` for the same reason the salt marker is.

Because the block key still partitions by district and window, this bounds
work the same way at 1.37M rows as it does at 55,544 — a corpus-wide run
does not create one giant bucket.

**Scope is optional, and identity matching is why it matters.** `--slice`,
`--district` and `--year` narrow a run; `--all` indexes everything. Text
candidates are blocked, so per-slice runs find the same ones a corpus run
would. Identity candidates (mobile/email) are deliberately *not* blocked,
so a same-citizen resubmission that crosses a district or year boundary is
only visible to a run whose scope contains both sides of it. Looping over
every district-year finds every text duplicate and no cross-slice identity
duplicate at all.

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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from janasunani.config import settings
from janasunani.db.models import Complaint, DedupGroup, DedupSignature, GrievanceRedaction
from janasunani.pipeline.dedup import (
    DEDUP_SOURCE_NAME,
    DEFAULT_NUM_BANDS,
    DEFAULT_NUM_HASHES,
    DEFAULT_SHINGLE_SIZE,
    identity_key,
    jaccard_similarity,
    lsh_bands,
    minhash_signature,
    source_record_digest,
    grouping_scope_id,
    source_snapshot_id_from_record_digests,
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

# Absolute origin for `_window_index`. Time windows have to be a property of
# the *record*, not of the run that indexed it, or the same complaint lands in
# a different block depending on what scope it was indexed under and the two
# never become candidates.
#
# This used to be `date(year, 1, 1)` -- Jan 1 of the slice's --year -- which is
# well defined only while every run is one district-year. It is not defined at
# all for a corpus-wide run, and worse, it silently made block keys
# run-dependent: `_index_version` never carried the epoch, so signatures built
# under different origins were stamped as mutually current and grouping mixed
# them with no error. Same failure class the salt marker exists to catch
# (#136). The epoch is now in the version stamp, so anything built under the
# old per-year origin is detected as stale and rebuilt.
#
# The specific date is arbitrary and only has to be stable and earlier than any
# complaint in the corpus. It must never be "tidied" -- changing it rewrites
# every block key, which is why it is versioned rather than merely fixed.
DEDUP_WINDOW_EPOCH = date(2000, 1, 1)

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
    (`DEDUP_WINDOW_EPOCH`, a fixed absolute date — never the run's own scope;
    see that constant for why). ``None`` for a missing timestamp —
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
        f"epoch={DEDUP_WINDOW_EPOCH.isoformat()} "
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


def _indexable_filters(model) -> list:
    """Require the dimensions a signature row cannot be written without.

    ``Complaint.district`` and ``Complaint.created_year`` are nullable, while
    ``DedupSignature`` and ``DedupGroup`` declare both NOT NULL. A scoped run
    never saw such a record, because SQL ``NULL = 'Sambalpur'`` is NULL and
    the row was silently filtered out by the scope predicate itself. Removing
    that predicate for a corpus-wide run removes the accidental filter with
    it, and the run dies on an integrity error partway through the backfill.

    Stated explicitly so the exclusion is a decision rather than a side effect
    of how the scope happened to be expressed, and applied on every path so a
    scoped and an unscoped run agree on which records are indexable.
    """
    return [model.district.isnot(None), model.created_year.isnot(None)]


def _slice_filters(model, district: Optional[str], year: Optional[int]) -> list:
    """Scope predicates for ``model``, empty when the scope is unbounded.

    ``None`` means "every district" / "every year", so a corpus-wide run is
    the absence of a predicate rather than a wildcard value. Both are
    independently optional: ``--district`` with no ``--year`` is a whole
    district across all years, which the identity path in particular needs,
    since a resubmission can cross a year boundary.

    ``model`` is `Complaint` or `DedupSignature` -- both carry ``district``
    and ``created_year``, and the caller picks whichever table the query is
    already filtering on.
    """
    filters = []
    if district is not None:
        filters.append(model.district == district)
    if year is not None:
        filters.append(model.created_year == year)
    return filters


def _scope_label(district: Optional[str], year: Optional[int]) -> str:
    """Human-readable run scope for logs and errors."""
    if district is None and year is None:
        return "the whole corpus"
    return f"{district or 'all districts'}/{year or 'all years'}"


async def _count_signature_slice(
    conn, district: Optional[str], year: Optional[int]
) -> tuple[int, int]:
    """(redacted complaints in the slice, already indexed)."""
    total = await conn.scalar(
        select(func.count())
        .select_from(GrievanceRedaction)
        .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
        .where(
            *_slice_filters(Complaint, district, year),
            *_indexable_filters(Complaint),
            GrievanceRedaction.grievance_redacted.isnot(None),
        )
    )
    done = await conn.scalar(
        select(func.count())
        .select_from(DedupSignature)
        .where(*_slice_filters(DedupSignature, district, year))
    )
    return int(total or 0), int(done or 0)


async def _count_stale_signatures(
    conn, district: Optional[str], year: Optional[int], version: str
) -> int:
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
                *_slice_filters(Complaint, district, year),
                DedupSignature.index_version != version,
            )
        )
        or 0
    )


async def _load_pending_signature_batch(
    conn,
    district: Optional[str],
    year: Optional[int],
    limit: int,
    version: str | None = None,
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
        DedupSignature.ticket_no == GrievanceRedaction.ticket_no,
        # Existing rows from before #137 cannot truthfully be stamped with a
        # current source manifest. Rebuild their signatures automatically;
        # grouping fails closed if a missing digest survives for any reason.
        DedupSignature.source_record_digest.isnot(None),
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
            Complaint.created_year,
            Complaint.created_on,
            Complaint.petitioner_mobile,
            Complaint.petitioner_email,
        )
        .join(Complaint, Complaint.ticket_no == GrievanceRedaction.ticket_no)
        .where(
            *_slice_filters(Complaint, district, year),
            *_indexable_filters(Complaint),
            GrievanceRedaction.grievance_redacted.isnot(None),
            ~done.exists(),
        )
        .order_by(GrievanceRedaction.ticket_no)
        .limit(limit)
    )
    result = await conn.execute(stmt)
    return result.all()


def _source_record(
    ticket_no: str,
    redacted_text: str | None,
    district: str,
    created_year: int,
    created_on: datetime | None,
    mobile: str | None,
    email: str | None,
) -> dict[str, object]:
    """The exact source fields captured by a signature provenance digest."""
    return {
        "ticket_no": ticket_no,
        "district": district,
        "created_year": created_year,
        "created_on": created_on,
        "petitioner_mobile": mobile,
        "petitioner_email": email,
        "grievance_redacted": redacted_text,
    }


def _signature_rows_for_source_batch(
    batch,
    *,
    salt: str,
    epoch: date,
    window_days: int,
    version: str,
    now: datetime,
) -> list[dict[str, object]]:
    """Build persisted signature rows from the exact source batch supplied."""
    rows = []
    for (
        ticket_no,
        redacted_text,
        row_district,
        row_year,
        created_on,
        mobile,
        email,
    ) in batch:
        text = redacted_text or ""
        shingle_set = shingles(text)
        signature = minhash_signature(shingle_set, num_hashes=DEFAULT_NUM_HASHES)
        script = _script_of(text)
        window_index = _window_index(created_on, epoch, window_days)
        source = _source_record(
            ticket_no, redacted_text, row_district, row_year, created_on, mobile, email
        )
        rows.append(
            {
                "ticket_no": ticket_no,
                "district": row_district,
                "created_year": row_year,
                "script": script,
                "window_index": window_index,
                "block_key": _block_key(row_district, script, window_index),
                "num_shingles": len(shingle_set),
                "signature": list(signature) if signature is not None else None,
                # A separate path from text above: computed from the complaints
                # columns directly, never from redacted_text (dedup.py module
                # docstring point 3).
                "identity_key_mobile": identity_key(mobile, salt) if mobile else None,
                "identity_key_email": identity_key(email, salt) if email else None,
                "source_record_digest": source_record_digest(source),
                "index_version": version,
                "indexed_at": now,
            }
        )
    return rows


async def _source_digest_mismatches(conn, district: Optional[str], year: Optional[int]):
    """Current source records whose existing signature digest is no longer true.

    This is deliberately a source read before grouping. Candidate generation
    relies on persisted signatures while exact Jaccard verification reads the
    current redacted text, so allowing these two inputs to differ would create
    an unprovable mixed run. Missing legacy digests are handled separately as
    pending signature rows; non-NULL disagreement requires --refresh-stale.
    """
    stmt = (
        select(
            DedupSignature.ticket_no,
            DedupSignature.source_record_digest,
            Complaint.district,
            Complaint.created_year,
            Complaint.created_on,
            Complaint.petitioner_mobile,
            Complaint.petitioner_email,
            GrievanceRedaction.grievance_redacted,
        )
        .select_from(DedupSignature)
        .outerjoin(Complaint, Complaint.ticket_no == DedupSignature.ticket_no)
        .outerjoin(GrievanceRedaction, GrievanceRedaction.ticket_no == DedupSignature.ticket_no)
        .where(*_slice_filters(DedupSignature, district, year))
        .order_by(DedupSignature.ticket_no)
    )
    result = await conn.execute(stmt)
    mismatches = []
    missing_source = []
    for (
        ticket_no,
        stored_digest,
        row_district,
        row_year,
        created_on,
        mobile,
        email,
        redacted_text,
    ) in result:
        if row_district is None or row_year is None or redacted_text is None:
            missing_source.append(ticket_no)
            continue
        current = source_record_digest(
            _source_record(
                ticket_no, redacted_text, row_district, row_year, created_on, mobile, email
            )
        )
        if stored_digest is not None and stored_digest != current:
            mismatches.append(
                (ticket_no, redacted_text, row_district, row_year, created_on, mobile, email)
            )
    return mismatches, missing_source


def _source_digest_mismatch_error(count: int) -> ValueError:
    return ValueError(
        f"{count} signature(s) no longer match their current OLTP source input. "
        "Refusing to group old candidates with current redacted text; rerun "
        "with --refresh-stale to rebuild them."
    )


def _missing_source_error(tickets: list[str]) -> ValueError:
    return ValueError(
        f"{len(tickets)} signature(s) no longer have a current complaint/redaction "
        "source row. Refusing to group orphan signatures; reconcile the slice "
        "before rebuilding it."
    )


def _source_membership_changed_error(count: int) -> ValueError:
    return ValueError(
        f"{count} signature(s) moved outside this district-year source slice. "
        "Refusing to rewrite an old slice under --refresh-stale; reconcile both "
        "affected slices explicitly before rebuilding."
    )


def _moved_sources(source_mismatches, district: Optional[str], year: Optional[int]) -> list:
    """Mismatched sources that left the scope their signature was indexed under.

    Only a *bound* dimension can be left. For a corpus-wide run both are
    ``None`` and nothing can move out of the corpus, so this compares against
    the dimensions actually pinned rather than against ``None`` -- otherwise
    every mismatch reads as moved and the run fails with the wrong error.

    Deliberately one function rather than a copy at each call site: this
    predicate is evaluated both during signature refresh and again before
    groups are persisted, and the two drifting apart is exactly how a scope
    fix gets applied to one path and not the other.
    """
    return [
        row
        for row in source_mismatches
        if (district is not None and row[2] != district)
        or (year is not None and row[3] != year)
    ]


def _raise_if_source_is_not_current(
    source_mismatches, missing_source: list[str], district: Optional[str], year: Optional[int]
) -> None:
    """Fail grouping before a stored signature and current text can diverge."""
    if missing_source:
        raise _missing_source_error(missing_source)
    moved_sources = _moved_sources(source_mismatches, district, year)
    if moved_sources:
        raise _source_membership_changed_error(len(moved_sources))
    if source_mismatches:
        raise _source_digest_mismatch_error(len(source_mismatches))


async def _index_signatures(
    engine: AsyncEngine,
    district: Optional[str],
    year: Optional[int],
    salt: str,
    window_days: int,
    threshold: float,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
) -> dict[str, int]:
    version = _index_version(window_days, threshold, salt)
    epoch = DEDUP_WINDOW_EPOCH
    processed = 0
    refreshed_tickets: set[str] = set()

    async with engine.begin() as conn:
        total, already = await _count_signature_slice(conn, district, year)
        stale = await _count_stale_signatures(conn, district, year, version)
        source_mismatches, missing_source = await _source_digest_mismatches(
            conn, district, year
        )
    logger.info(
        "{}: {} redacted complaints, {} already indexed",
        _scope_label(district, year),
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
    if missing_source:
        raise _missing_source_error(missing_source)
    moved_sources = _moved_sources(source_mismatches, district, year)
    if moved_sources:
        raise _source_membership_changed_error(len(moved_sources))
    if source_mismatches and not refresh_stale:
        raise _source_digest_mismatch_error(len(source_mismatches))
    if source_mismatches:
        if limit is not None:
            raise ValueError(
                "--limit cannot be combined with --refresh-stale when source "
                "inputs changed; remove --limit to rebuild every mismatch."
            )
        logger.warning(
            "{} signature(s) no longer match current OLTP source input; "
            "rebuilding them because --refresh-stale was supplied.",
            len(source_mismatches),
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = _signature_rows_for_source_batch(
            source_mismatches,
            salt=salt,
            epoch=epoch,
            window_days=window_days,
            version=version,
            now=now,
        )
        async with engine.begin() as conn:
            await conn.execute(
                _dialect_upsert(DedupSignature, conn.dialect.name, rows, "ticket_no")
            )
        refreshed_tickets.update(row[0] for row in source_mismatches)
        processed += len(rows)

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

            batch_ticket_nos = [row[0] for row in batch]
            existing_in_batch = set(
                (
                    await conn.execute(
                        select(DedupSignature.ticket_no).where(
                            DedupSignature.ticket_no.in_(batch_ticket_nos)
                        )
                    )
                )
                .scalars()
                .all()
            )

            # Naive UTC — every timestamp column in this schema is TIMESTAMP
            # WITHOUT TIME ZONE and asyncpg refuses a tz-aware value there,
            # while SQLite silently accepts one. Same normalisation as
            # redact_grievance.py / db/crud.py.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            rows = _signature_rows_for_source_batch(
                batch,
                salt=salt,
                epoch=epoch,
                window_days=window_days,
                version=version,
                now=now,
            )
            await conn.execute(
                _dialect_upsert(DedupSignature, conn.dialect.name, rows, "ticket_no")
            )

        refreshed_tickets.update(existing_in_batch)
        processed += len(batch)
        unchanged_existing = already - len(refreshed_tickets)
        logger.info(
            "indexed {} of {} ({} this batch)",
            unchanged_existing + processed,
            total,
            len(batch),
        )

    unchanged_existing = already - len(refreshed_tickets)
    return {
        "total": total,
        # Existing rows overwritten during this run belong in processed, not
        # here. This keeps the reconciliation contract auditable:
        # unchanged existing + inserted/refreshed <= total, including a
        # one-row source refresh inside an otherwise complete slice.
        "already_indexed": unchanged_existing,
        "processed": processed,
        "stale_at_start": stale,
        "source_mismatches_at_start": len(source_mismatches),
    }


# --- stage 2: grouping ------------------------------------------------------


async def _load_slice_signatures(conn, district: Optional[str], year: Optional[int]):
    stmt = select(
        DedupSignature.ticket_no,
        DedupSignature.district,
        DedupSignature.created_year,
        DedupSignature.block_key,
        DedupSignature.signature,
        DedupSignature.identity_key_mobile,
        DedupSignature.identity_key_email,
    ).where(*_slice_filters(DedupSignature, district, year))
    result = await conn.execute(stmt)
    return result.all()


async def _raise_if_scope_would_split_existing_groups(
    conn, district: Optional[str], year: Optional[int]
) -> None:
    """Refuse a scoped run that would tear apart a wider existing group.

    Grouping recomputes whole and upserts only the tickets it loaded. That is
    correct while every group is contained in the scope that built it, which
    was guaranteed while the only possible scope was one district-year.

    Once ``--all`` has unioned a same-citizen resubmission across a district
    or year boundary, it stops being guaranteed. A later scoped run loads one
    side of that group, cannot see the other, recomputes its half as a
    singleton (or a smaller local group) and upserts only those rows. The
    other half keeps the old ``duplicate_group_id`` and a ``group_size`` that
    no longer matches how many rows carry it. Nothing errors, and the
    duplicate-adjusted counts downstream are quietly wrong in both directions.

    So this fails closed instead, in the same spirit as the source-currency
    check above: the operator is told to widen the scope rather than being
    allowed to half-rebuild. An unbounded run can never trip it, because
    nothing lies outside the corpus.
    """
    if district is None and year is None:
        return

    in_scope = select(DedupGroup.duplicate_group_id).where(
        *_slice_filters(DedupGroup, district, year)
    )
    # "Outside the scope" is the negation of the whole conjunction, so the
    # negated predicates are OR-ed. AND-ing them would only find rows outside
    # *every* bound dimension at once and miss, for example, the same district
    # in a different year -- which is the common case.
    outside = or_(*[~predicate for predicate in _slice_filters(DedupGroup, district, year)])
    straddling = await conn.scalar(
        select(func.count(func.distinct(DedupGroup.duplicate_group_id))).where(
            DedupGroup.duplicate_group_id.in_(in_scope),
            outside,
        )
    )
    if straddling:
        raise ValueError(
            f"{straddling} existing duplicate group(s) have members outside "
            f"{_scope_label(district, year)}, so regrouping this scope alone "
            "would split them and leave the outside half stamped with a group "
            "id and size that no longer hold. Rerun with --all, or widen the "
            "scope to contain them."
        )


async def _source_snapshots_by_slice(
    conn, district: Optional[str], year: Optional[int], grouping_version: str
) -> tuple[dict[tuple[str, int], str], str]:
    """Manifest the indexed inputs, **one digest per district-year**.

    This intentionally reads the digest stored at signature time, never the
    current redaction row. If OLTP changes without re-indexing, persisted
    groups continue to identify their older actual input, and a prospective
    lake join fails the public assertion rather than being stamped as current.
    A partial signature run likewise has a manifest only for its actual subset
    and cannot validate a full lake slice.

    Keyed by district-year rather than by run, because that is the unit
    consumers verify against. `analytics/findings/workload.py` and `spike.py`
    read the lake for one district-year, recompute `source_snapshot_id` over
    it, and assert every group row carries that value. A corpus-wide run that
    stamped one whole-corpus digest onto every row would fail that assertion
    for every slice, breaking both findings — the digest has to describe the
    slice the *record* belongs to, not the scope the run happened to use.

    A scoped run yields exactly one entry, so its behaviour is unchanged.
    """
    stmt = (
        select(
            DedupSignature.ticket_no,
            DedupSignature.district,
            DedupSignature.created_year,
            DedupSignature.source_record_digest,
        )
        .where(*_slice_filters(DedupSignature, district, year))
        .order_by(DedupSignature.ticket_no)
    )
    result = await conn.execute(stmt)
    rows = result.all()
    missing = [row.ticket_no for row in rows if row.source_record_digest is None]
    if missing:
        raise ValueError(
            "dedup groups cannot be stamped with source provenance while "
            f"{len(missing)} signature(s) lack source_record_digest; rerun "
            "janasunani-dedup-index so #137 can rebuild them"
        )

    by_slice: dict[tuple[str, int], list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        by_slice[(row.district, row.created_year)].append(
            (row.ticket_no, row.source_record_digest)
        )
    per_slice = {
        slice_key: source_snapshot_id_from_record_digests(record_digests)
        for slice_key, record_digests in by_slice.items()
    }
    # Everything the grouping run read, which is the input set that actually
    # determined the assignments -- see DedupGroup.grouping_scope_snapshot_id.
    # For a single-slice run this equals that slice's own digest, so the two
    # fields agree exactly where they always did.
    scope = grouping_scope_id(
        grouping_version, [(row.ticket_no, row.source_record_digest) for row in rows]
    )
    return per_slice, scope


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
    district: Optional[str],
    year: Optional[int],
    window_days: int,
    threshold: float,
    salt: str,
    representative_cap: int = REPRESENTATIVE_COMPARISON_CAP,
    anchor_count: int = LARGE_BUCKET_ANCHOR_COUNT,
) -> dict[str, int]:
    # Computed before the snapshot read, because the grouping scope digest
    # has to cover the parameters that produced an assignment and not only
    # which records were read.
    version = _index_version(
        window_days,
        threshold,
        salt,
        grouping_algorithm=GROUPING_ALGORITHM,
        representative_cap=representative_cap,
        anchor_count=anchor_count,
    )

    async with engine.begin() as conn:
        rows = await _load_slice_signatures(conn, district, year)
        source_mismatches, missing_source = await _source_digest_mismatches(
            conn, district, year
        )
        _raise_if_source_is_not_current(
            source_mismatches, missing_source, district, year
        )
        await _raise_if_scope_would_split_existing_groups(conn, district, year)
        snapshots_by_slice, scope_snapshot = await _source_snapshots_by_slice(
            conn, district, year, version
        )

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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # district/created_year describe the *record*, taken from its signature
    # row -- not the scope the run was invoked with. Those coincided while
    # every run was a single district-year, and stop coinciding the moment one
    # run spans more than one: a corpus-wide run would otherwise stamp NULL
    # over every group row, which the NOT NULL constraint catches, and a
    # district-wide run would silently stamp the wrong year, which it does not.
    signature_by_ticket = {row.ticket_no: row for row in rows}
    group_rows = [
        {
            "ticket_no": ticket_no,
            "district": signature_by_ticket[ticket_no].district,
            "created_year": signature_by_ticket[ticket_no].created_year,
            "block_key": signature_by_ticket[ticket_no].block_key,
            "duplicate_group_id": group_id,
            "group_size": group_sizes[group_id],
            "source_name": DEDUP_SOURCE_NAME,
            "source_snapshot_id": snapshots_by_slice[
                (
                    signature_by_ticket[ticket_no].district,
                    signature_by_ticket[ticket_no].created_year,
                )
            ],
            "grouping_scope_snapshot_id": scope_snapshot,
            "index_version": version,
            "grouped_at": now,
        }
        for ticket_no, group_id in groups.items()
    ]

    async with engine.begin() as conn:
        # A source update can race the initial check while candidate text is
        # being fetched and verified. Check again immediately before persisting
        # a certified group assignment; this narrows the READ COMMITTED race
        # window, though it is not a substitute for a transaction-wide lock.
        source_mismatches, missing_source = await _source_digest_mismatches(
            conn, district, year
        )
        _raise_if_source_is_not_current(
            source_mismatches, missing_source, district, year
        )
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


# --- held-out recall ----------------------------------------------------


def _groups_to_pairs(groups: dict[str, str]) -> set[frozenset[str]]:
    """All unordered ticket pairs that share a duplicate_group_id (size>1).

    Singletons contribute no pairs. Each non-singleton group of *m* members
    contributes C(m,2) pairs. Used only for recall measurement against
    officer-confirmed duplicates (#72); the grouping path itself streams
    buckets without ever materialising this set for the full slice.
    """
    members_by_group: dict[str, list[str]] = defaultdict(list)
    for ticket, gid in groups.items():
        members_by_group[gid].append(ticket)
    pairs: set[frozenset[str]] = set()
    for members in members_by_group.values():
        if len(members) > 1:
            for a, b in combinations(sorted(members), 2):
                pairs.add(frozenset((a, b)))
    return pairs


def evaluate_held_out_recall(
    officer_pairs: set[frozenset[str]],
    dedup_groups: dict[str, str],
) -> dict[str, int | float]:
    """Recall of dedup groups against officer-confirmed duplicate pairs.

    ``officer_pairs`` is the held-out set: unordered ticket pairs officers
    already marked as duplicate via the two governed families
    (``case already taken up`` + ``duplicate copy`` — ~34k baseline per
    ROADMAP §5.2, queryable via ``duplicate_recall.sql``). ``dedup_groups``
    is the post-``build_dedup_index`` mapping ``{ticket: duplicate_group_id}``.

    Returns ``{"officer_pairs": int, "true_positives": int, "recall": float,
    "dedup_pairs": int, "incremental_pairs": int, "incremental_groups": int}``
    where ``incremental_pairs`` are dedup pairs with no officer label — the
    actual capability claim — and ``incremental_groups`` counts dedup groups
    that contain at least one incremental pair. Recall is ``1.0`` when
    ``officer_pairs`` is empty (nothing to miss).

    Callers that join dedup_groups to a lake must call
    ``assert_group_source_snapshot`` first (#137); this helper does not touch
    the DB and assumes its inputs already passed that guard.
    """
    dedup_pairs = _groups_to_pairs(dedup_groups)
    true_positives = len(officer_pairs & dedup_pairs)
    recall = true_positives / len(officer_pairs) if officer_pairs else 1.0
    incremental_pairs = dedup_pairs - officer_pairs
    # Groups that contributed at least one incremental pair.
    incremental_groups = 0
    members_by_group: dict[str, list[str]] = defaultdict(list)
    for ticket, gid in dedup_groups.items():
        members_by_group[gid].append(ticket)
    for members in members_by_group.values():
        if len(members) > 1:
            has_incremental = any(
                frozenset(pair) in incremental_pairs
                for pair in combinations(sorted(members), 2)
            )
            if has_incremental:
                incremental_groups += 1
    return {
        "officer_pairs": len(officer_pairs),
        "true_positives": true_positives,
        "recall": recall,
        "dedup_pairs": len(dedup_pairs),
        "incremental_pairs": len(incremental_pairs),
        "incremental_groups": incremental_groups,
    }


# --- orchestration -----------------------------------------------------


def build_dedup_index(
    district: Optional[str],
    year: Optional[int],
    oltp_url: Optional[str] = None,
    salt: Optional[str] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    limit: Optional[int] = None,
    refresh_stale: bool = False,
    representative_cap: int = REPRESENTATIVE_COMPARISON_CAP,
    anchor_count: int = LARGE_BUCKET_ANCHOR_COUNT,
) -> dict[str, int]:
    """Index a scope and (re)compute its duplicate groups.

    ``district`` and ``year`` are independently optional; ``None`` means
    unbounded on that dimension, so ``(None, None)`` indexes the whole
    corpus. Scope is not merely a convenience: identity buckets
    (mobile/email) are deliberately unblocked, because a resubmission can
    land months later under any district, so a same-citizen duplicate that
    crosses a district or year boundary is only ever visible to a run whose
    scope contains both sides of it. Running 150 district-years in a loop
    finds the text duplicates and none of those.

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


def _parse_slice(slice_label: str) -> tuple[str, int]:
    """Parse ``District/YYYY`` into ``(district, year)`` for ``--slice``."""
    if "/" not in slice_label:
        raise ValueError(f"--slice must be District/YYYY, got {slice_label!r}")
    district, year_str = slice_label.split("/", 1)
    district = district.strip()
    year_str = year_str.strip()
    if not district or not year_str:
        raise ValueError(f"--slice must be District/YYYY, got {slice_label!r}")
    try:
        year = int(year_str)
    except ValueError as exc:
        raise ValueError(f"--slice year must be an integer, got {year_str!r}") from exc
    return district, year


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the MinHash/LSH dedup index over grievance_redacted "
            "text for one district-year slice, and persist the resulting "
            "duplicate groups."
        )
    )
    parser.add_argument("--district", required=False, default=None, help="District name, as stored in complaints.district.")
    parser.add_argument("--year", required=False, type=int, default=None, help="created_year to process.")
    parser.add_argument("--slice", required=False, default=None, help="Shorthand for --district/--year as District/YYYY (e.g. Sambalpur/2024).")
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Index every district and year. Required to run corpus-wide: an "
            "unscoped invocation errors rather than silently rebuilding 1.37M "
            "rows. Needed for same-citizen duplicates that cross a district or "
            "year boundary, which no per-slice run can see."
        ),
    )
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
        help="Time-window width (days) for blocking. Windows are measured from a "
        "fixed absolute epoch, so a record's window is the same whatever scope "
        "indexed it; changing this value changes every block key and is "
        "recorded in the index version.",
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
            "(salt, window, threshold, or dedup.py's own constants), or whose "
            "current OLTP source record no longer matches its stored digest. "
            "Off by default: a backfill over derived citizen data should not "
            "change scope because an unrelated value moved. Required after a "
            "salt rotation or redaction/source update; otherwise grouping fails "
            "closed rather than mixing old candidates with current text."
        ),
    )
    args = parser.parse_args()
    # --slice is syntactic sugar for --district/--year (Sambalpur/2024).
    district = args.district
    year = args.year
    if args.slice:
        slice_district, slice_year = _parse_slice(args.slice)
        if district is not None and district != slice_district:
            parser.error("--district conflicts with --slice district")
        if year is not None and year != slice_year:
            parser.error("--year conflicts with --slice year")
        district, year = slice_district, slice_year
    # No scope at all is a corpus-wide run, and it has to be asked for
    # explicitly. A bare invocation is far more often a forgotten --slice than
    # a deliberate 1.37M-row rebuild, and the two are indistinguishable from
    # argv alone.
    if district is None and year is None and not args.all:
        parser.error(
            "no scope given. Pass --slice/--district/--year for part of the "
            "corpus, or --all to index every district and year. --all is a "
            "full rebuild over the whole corpus, so it is never implied."
        )
    if args.all and (district is not None or year is not None):
        parser.error("--all cannot be combined with --slice/--district/--year")

    counts = build_dedup_index(
        district,
        year,
        oltp_url=args.oltp_url,
        salt=args.salt,
        window_days=args.window_days,
        threshold=args.threshold,
        limit=args.limit,
        refresh_stale=args.refresh_stale,
    )
    logger.info(
        "done: {} processed this run, {} of {} indexed, {} duplicate groups "
        "over {} signatures (comparison_pairs={}, large_buckets={}) in {}",
        counts["processed"],
        counts["already_indexed"] + counts["processed"],
        counts["total"],
        counts["groups"],
        counts["slice_signatures"],
        counts["comparison_pairs"],
        counts["large_buckets"],
        _scope_label(district, year),
    )


if __name__ == "__main__":
    main()
