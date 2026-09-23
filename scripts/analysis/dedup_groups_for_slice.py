"""Duplicate groups for one slice of the corpus, for the grievance notebooks.

Runs on the OLTP box (the redacted text and DEDUP_SALT live only there) and
writes a ticket -> group CSV. Nothing but that mapping leaves the box: no
citizen text, no identity hash, no MinHash signature.

    ssh ubuntu@<box>
    cd ~/janasunani && set -a && . ./.env && set +a
    ./.venv/bin/python scripts/analysis/dedup_groups_for_slice.py \
        --where "c.dept_id = 40" --out /tmp/ssepd_dedup
    # then, locally:
    scp ubuntu@<box>:/tmp/ssepd_dedup_identity.csv \
        outputs/dedup/ssepd_dedup_groups.csv

**Why this exists rather than `janasunani-dedup-index`.** The corpus-wide
grouping pass has not finished: of the 40,126 SSEPD grievances in scope 39,720
have a signature and only 250 carry a group. `_group_duplicates` is the
unbatched stage, and its own comments record an OOM at 7.4 GB on a 9,405-row
block. A department-sized slice is small enough that the problem does not
arise. Run it per slice until the corpus index is built, then delete this.

**Scope is one run, not a loop over years.** Identity buckets are deliberately
unblocked by district and time window, so a resubmission that lands months
later elsewhere is only visible to a run whose scope contains both sides of it.
Slicing by year would silently lose exactly those.

**Two definitions, because they answer different questions.**

A. identity-verified -- candidates come only from a shared petitioner identity
   key (mobile/email) and must still pass Jaccard text verification. This is
   "the same complaint filed by the same person more than once", and is what
   the notebooks use.
B. text-or-identity -- the pipeline's own grouping, which additionally unions
   filings whose text matches across *different* petitioners. Campaign filings
   are separate claims, not duplicates of each other, so B is reported for
   comparison only.

Writes CSVs rather than upserting `dedup_groups`: a slice is not a
(district, year), so persisting it would collide with the scope guards and
with the partial corpus run already in the table.
"""

import argparse
import asyncio
import csv
import sys
from collections import Counter
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from janasunani.config import settings
from janasunani.pipeline.dedup_index import (
    DEFAULT_DUPLICATE_THRESHOLD,
    DEFAULT_NUM_BANDS,
    IDENTITY_BATCH_TICKETS,
    _band_buckets,
    _band_signature_row,
    _find,
    _identity_buckets,
    _load_redacted_text,
    _verify_bucket,
)

QUERY = """
  SELECT s.ticket_no, s.district, s.created_year, s.block_key, s.signature,
         s.identity_key_mobile, s.identity_key_email
  FROM dedup_signatures s JOIN complaints c ON c.ticket_no = s.ticket_no
  WHERE {where}
    AND c.created_on >= :start AND c.created_on < :end
"""


async def _verify_batch(conn, batch, parent, cache):
    """Verify one batch of buckets, then let its text and shingles go.

    ``cache`` is passed in and dropped by the caller rather than kept for the
    run. That is not an optimisation: an unbounded cache is text and shingles
    for every ticket the slice touches, and on a department the size of
    Panchayati Raj (521,937 signatures) it OOM-killed this script at 15.3 GB.
    `_group_duplicates` in the pipeline batches for the same reason and by the
    same measure; this mirrors it rather than inventing a second policy.
    """
    tickets = sorted({t for members in batch for t in members})
    tbt = await _load_redacted_text(conn, tickets)
    verified = comparisons = 0
    for members in batch:
        v, c, _ = _verify_bucket(members, tbt, cache, parent,
                                 DEFAULT_DUPLICATE_THRESHOLD)
        verified += v
        comparisons += c
    return verified, comparisons


async def _group(conn, rows, buckets, label, path):
    parent: dict[str, str] = {}
    verified = comparisons = 0
    batch: list[list[str]] = []
    batch_tickets = 0

    # Batched by member count, not by bucket count: identity buckets are
    # wildly uneven, so "N buckets" is a few hundred tickets or a quarter of
    # the slice depending on which ones it gets.
    for members in buckets:
        members = sorted(set(members))
        if len(members) < 2:
            continue
        batch.append(members)
        batch_tickets += len(members)
        if batch_tickets >= IDENTITY_BATCH_TICKETS:
            v, c = await _verify_batch(conn, batch, parent, {})
            verified += v
            comparisons += c
            batch, batch_tickets = [], 0
    if batch:
        v, c = await _verify_batch(conn, batch, parent, {})
        verified += v
        comparisons += c

    groups = {r.ticket_no: _find(parent, r.ticket_no) for r in rows}
    sizes = Counter(groups.values())
    print(f"[{label}] verified={verified} comparisons={comparisons} "
          f"cases={len(sizes)} of {len(rows)} "
          f"({100 * (1 - len(sizes) / len(rows)):.1f}% collapsed) "
          f"largest={max(sizes.values())}", flush=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticket_no", "duplicate_group_id", "group_size"])
        for t, g in sorted(groups.items()):
            w.writerow([t, g, sizes[g]])
    print(f"wrote {path}", flush=True)


async def run(where: str, out: str, start: date, end: date,
              compare_b: bool = False) -> None:
    engine = create_async_engine(settings.OLTP_DB_URL)
    try:
        async with engine.begin() as conn:
            res = await conn.execute(
                text(QUERY.format(where=where)), {"start": start, "end": end}
            )
            rows = [_band_signature_row(r, DEFAULT_NUM_BANDS) for r in res]
            print(f"signatures in slice: {len(rows)}", flush=True)
            if not rows:
                raise SystemExit("no signatures in scope; nothing to group")
            ident = list(_identity_buckets(rows))
            print(f"identity buckets: {len(ident)}", flush=True)
            await _group(conn, rows, ident, "A same person + same text",
                         f"{out}_identity.csv")
            if compare_b:
                # Band buckets over a department the size of Panchayati Raj are
                # a different order of work from the identity ones, and B is
                # only ever a comparison, so it is opt-in.
                band = [m for _, m in _band_buckets(rows, DEFAULT_NUM_BANDS)]
                print(f"band buckets: {len(band)}", flush=True)
                await _group(conn, rows, ident + band, "B text or identity",
                             f"{out}_full.csv")
    finally:
        await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--where", required=True,
                    help="SQL predicate over complaints aliased `c`, "
                         "e.g. \"c.dept_id = 40\"")
    ap.add_argument("--out", required=True,
                    help="output path prefix; _identity.csv and _full.csv are appended")
    ap.add_argument("--start", default="2021-07-01")
    ap.add_argument("--end", default="2025-07-01")
    ap.add_argument("--compare-b", action="store_true",
                    help="also run definition B (text-or-identity), for comparison")
    args = ap.parse_args()
    asyncio.run(run(args.where, args.out,
                    date.fromisoformat(args.start),
                    date.fromisoformat(args.end),
                    compare_b=args.compare_b))


if __name__ == "__main__":
    sys.exit(main())
