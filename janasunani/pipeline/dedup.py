"""MinHash/LSH duplicate index over character n-grams (Phase 14, W6).

Deterministic, CPU-only, no model — the algorithmic core of "component (b)"
in docs/ROADMAP.md §5.2. Lives at the light `janasunani.pipeline` level
(like `ocr_quality.py` and `ticket.py`), not inside `stages/`, because it is
deliberately **stdlib-only**: `hashlib`, `re`, `random`, `unicodedata`,
`collections.abc`. No numpy, no presidio, no torch, no datasketch. That
keeps this module (and `tests/test_dedup.py`) importable and runnable in
CI, which installs no heavy extras — every other pipeline test guards with
`pytest.importorskip` and skips there. This is the one pipeline module that
doesn't, and is therefore the only real CI coverage over the whole
intelligence layer that Phase 15 converges on. Keep it that way: do not add
a dependency here to make an implementation nicer.

Wiring this in as the 7th pipeline stage (`spam_duplicate`, after
`pii_tagger`) is deliberately out of scope for this module — see the
tracking issue. This file provides the primitives:
`shingles`, `minhash_signature`, `lsh_bands`, `jaccard_similarity`,
`union_find_groups`, `strip_placeholders`, `identity_key`.

Six things that shape every function below:

1. **Character n-grams, not word tokens.** Word tokenization does not work
   across Odia, romanized Odia and English in one index; character grams
   also survive OCR noise that would break word boundaries. See
   docs/ROADMAP.md §5.2.
2. **Typed PII placeholders are stripped before shingling.** Redacted text
   (the input here — this index is built from `grievance_redacted` /
   `pages.redacted_text`) carries typed tokens like `[PHONE]`, `[NAME]`
   in place of real PII. Left in, every redacted phone number is the
   identical literal token, so two unrelated grievances that each mention a
   name and a phone number share those n-grams and score as more similar
   than they are. `shingles()` calls `strip_placeholders()` for exactly
   this reason — there is no shingling path that skips it.
3. **Identity keys are a separate path from the text path.** `identity_key`
   salts and hashes a citizen identity value (mobile, email) for
   resubmission linkage ("same citizen, same issue"). It never touches
   `shingles`/`minhash_signature`/`lsh_bands`, and the reverse must also
   hold: identity values must never be concatenated into the text that gets
   shingled, and shingled text must never carry a raw identity value. A raw
   mobile number in a dedup index is a PII store by another name.
4. **Cross-script recall is explicitly unsupported.** Odia-script and
   romanized-Odia filings must be indexed and matched *separately* — never
   against each other. Character n-grams operate on raw codepoints, and
   Odia script and Latin script occupy disjoint Unicode ranges, so a
   romanized filing and its Odia-script twin share ~zero shingles and will
   not be found as duplicates by this module. That is a known, accepted
   gap, not a bug: the transliteration step that would close it is Phase 17
   (§5.5), which runs after this phase. Callers must partition documents by
   script (or language field) before building an index and never run one
   index across both. `test_dedup.py` pins this as a limitation, not a
   feature to "fix" by loosening the shingle function.
5. **Refuse rather than hash a value with nothing meaningful in it.**
   Several review rounds found the same shape of bug under different
   clothes: an input that looks valid enough to hash but silently collapses
   distinct records onto one identity or one group. `minhash_signature`
   returns ``None`` (not a shared sentinel) for an empty shingle set;
   `identity_key` returns ``None`` (not a hash of the empty string) for a
   blank/whitespace-only value — `petitioner_mobile`/`petitioner_email` are
   nullable and blank strings are not normalized to ``None`` at ingestion,
   so hashing `""` would otherwise merge every citizen with a missing
   contact field into one "identity"; `minhash_signature` also rejects
   ``num_hashes <= 0`` outright rather than silently returning an empty
   signature that would band-collapse an entire indexed slice into one
   group. One rule, three call sites: **if there is nothing real to
   compare, refuse rather than produce a comparable-looking output.**
6. **An LSH candidate is not a duplicate until verified.** `lsh_bands`
   reports a shared band as a candidate worth checking, not a confirmed
   match — a chance collision between two unrelated documents is expected
   at some rate by construction. `union_find_groups` performs blind
   transitive closure over whatever pairs it is given and does not verify
   anything itself, so passing raw LSH candidate pairs straight into it
   turns every chance collision into a confirmed duplicate group, and
   transitive merging can pull further, unrelated documents into that
   false group. `jaccard_similarity` is the verification step: compute it
   on the real shingle sets for every candidate pair and only pass pairs
   above a chosen threshold into `union_find_groups`. See the end-to-end
   test in `test_dedup.py` for the full candidate -> verify -> union
   pattern.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date, datetime

# Typed PII placeholder tokens look like "[PHONE]", "[NAME]", "[AADHAAR]"
# (see janasunani/pipeline/stages/pii_tagger.py:ENTITY_TOKENS) — a bracketed
# run of uppercase letters, nothing else ever looks like this in redacted
# grievance text.
_PLACEHOLDER_RE = re.compile(r"\[[A-Z]+\]")

# Odia (U+0B66) and Devanagari (U+0966) decimal digits -> ASCII, for phone
# canonicalization in identity_key(). Same two blocks and the same rationale
# as pii_tagger.py's _INDIC_DIGITS_TO_ASCII: these are the digit scripts
# that actually show up in this corpus (Odia-script filings; Devanagari for
# the occasional Hindi-medium form). Deliberately narrow — see
# _canonical_phone_digits()'s docstring for what happens to every other
# Unicode digit system.
_INDIC_DIGITS_TO_ASCII = str.maketrans(
    {base + i: str(i) for base in (0x0966, 0x0B66) for i in range(10)}
)

# Character shingle width. Short enough to survive OCR noise and short
# grievance text, long enough that shingles carry real content rather than
# matching on common letter pairs.
DEFAULT_SHINGLE_SIZE = 5

# Number of hash functions in a MinHash signature. Higher = better Jaccard
# estimate, more compute; 128 is the conventional default (e.g. datasketch).
DEFAULT_NUM_HASHES = 128

# Default LSH bands. The candidate probability for a pair at true Jaccard
# similarity s, with b bands of r = num_hashes / b rows each, is
# P(s) = 1 - (1 - s**r)**b. That function only crosses 0.5 at s ~= 0.86 for
# 8 bands of 16 rows (128 // 8) — nowhere near the "campaign, but retyped"
# or "resubmission, reworded" similarity this index exists to catch (a
# lightly reworded duplicate scores ~0.78 in tests/test_dedup.py, which
# 8-band LSH surfaces as a candidate only ~14% of the time). 16 bands of 8
# rows moves the crossover to s ~= 0.67 and gives that same ~0.78-similarity
# pair a ~90% chance of becoming a candidate, while a genuinely different
# document that only shares boilerplate ("respected sir", "kindly look
# into the matter") at s ~= 0.3 still has under a 0.1% chance — narrow
# enough to keep candidate volume sane, wide enough that a caller does not
# have to remember to widen it for the primitive to do its job. Verified
# numerically, not just approximated: see the banding tests.
DEFAULT_NUM_BANDS = 16

# 2**61 - 1, a Mersenne prime — the modulus for the MinHash permutation
# family below. Large enough to keep collisions negligible, small enough to
# stay in a 64-bit int and keep the arithmetic cheap.
_MERSENNE_PRIME = (1 << 61) - 1

# Fixed seed for the permutation coefficients (not a security secret — it
# only needs to make minhash_signature() reproducible across processes and
# runs, not unpredictable). Changing this value changes every signature
# ever produced; don't.
_PERMUTATION_SEED = 0x4A53_6465_6475_70  # "JSdedup" packed into hex, arbitrary

# The historical backfill currently reads its inputs directly from the OLTP
# tables below, not from a materialized Parquet lake.  Keep the source name and
# the canonical record format here, in the stdlib-only module, so a downstream
# analytics job can calculate exactly the same identifier from either source.
# A group row stores this source name plus the digest returned by
# `source_snapshot_id()`.  That makes a lake/OLTP mismatch an explicit error,
# not a slightly wrong duplicate-adjusted count (#137).
DEDUP_SOURCE_NAME = "oltp:complaints+grievance_redactions"
_SOURCE_SNAPSHOT_FORMAT = "janasunani-dedup-source-v1"


class DedupSourceSnapshotMismatch(ValueError):
    """Raised when duplicate groups do not describe the asserted source slice."""


def _snapshot_value(value: object) -> str | int | None:
    """Canonical JSON value for one source-snapshot field.

    SQLite returns ``datetime`` values while a Parquet/DuckDB consumer can
    return ``date``/``datetime`` values with a different concrete type.  ISO
    text makes the provenance digest source-independent; all other fields are
    intentionally kept as their actual scalar values so e.g. ``None`` does
    not collapse into an empty string.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int)):
        return value
    raise TypeError(f"unsupported dedup source snapshot value: {type(value)!r}")


def _canonical_source_record(record: Mapping[str, object]) -> tuple[str, bytes]:
    """Return the ticket and canonical bytes of one dedup index input."""
    fields = (
        "ticket_no",
        "district",
        "created_year",
        "created_on",
        "petitioner_mobile",
        "petitioner_email",
        "grievance_redacted",
    )
    try:
        payload = {field: _snapshot_value(record[field]) for field in fields}
    except KeyError as exc:
        raise ValueError(f"dedup source snapshot record is missing {exc.args[0]!r}") from exc
    ticket_no = payload["ticket_no"]
    if not isinstance(ticket_no, str) or not ticket_no.strip():
        raise ValueError("dedup source snapshot requires a non-blank ticket_no")
    return (
        ticket_no,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )


def source_record_digest(record: Mapping[str, object]) -> str:
    """Stable digest of the exact source values used for one signature row.

    This is persisted beside a DedupSignature at indexing time. A later
    grouping run must describe the rows that were actually indexed, rather
    than re-read current OLTP values and accidentally relabel stale signatures
    as current (#137).
    """
    _, payload = _canonical_source_record(record)
    return _source_record_digest_from_payload(payload)


def _source_record_digest_from_payload(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def source_snapshot_id_from_record_digests(
    record_digests: Iterable[tuple[str, str]],
) -> str:
    """Stable slice manifest from (ticket_no, source_record_digest) pairs."""
    canonical = sorted(record_digests, key=lambda item: item[0])
    digest = hashlib.sha256()
    digest.update(f"{_SOURCE_SNAPSHOT_FORMAT}\n".encode("ascii"))
    previous_ticket: str | None = None
    for ticket_no, record_digest in canonical:
        if not isinstance(ticket_no, str) or not ticket_no:
            raise ValueError("dedup source snapshot requires a non-blank ticket_no")
        if ticket_no == previous_ticket:
            raise ValueError(f"dedup source snapshot has duplicate ticket_no {ticket_no!r}")
        if not isinstance(record_digest, str) or not record_digest.startswith("sha256:"):
            raise ValueError(f"invalid source record digest for {ticket_no!r}")
        digest.update(ticket_no.encode("utf-8"))
        digest.update(b"\0")
        digest.update(record_digest.encode("ascii"))
        digest.update(b"\n")
        previous_ticket = ticket_no
    return f"sha256:{digest.hexdigest()}"


def source_snapshot_id(records: Iterable[Mapping[str, object]]) -> str:
    """Stable manifest of the exact historical inputs used for a dedup slice.

    Each record must carry these OLTP/lake columns: ``ticket_no``, ``district``,
    ``created_year``, ``created_on``, ``petitioner_mobile``,
    ``petitioner_email``, and ``grievance_redacted``.  They are every source
    value that changes the runner's membership, blocking, text signature, or
    identity candidates.  The digest contains no reversible text or identity
    value, but remains ``dpic-infra`` provenance because it is derived from
    citizen data.

    Records are sorted by ticket and a duplicate ticket is rejected.  Therefore
    the result is stable across SQL/Parquet row order and cannot quietly treat
    an accidental duplicated join as the same snapshot.
    """
    record_digests = []
    for record in records:
        ticket_no, payload = _canonical_source_record(record)
        record_digests.append((ticket_no, _source_record_digest_from_payload(payload)))
    return source_snapshot_id_from_record_digests(record_digests)


def assert_group_source_snapshot(
    group_rows: Iterable[Mapping[str, object]], source_records: Iterable[Mapping[str, object]]
) -> str:
    """Assert that persisted groups and a prospective analytics source agree.

    ``source_records`` is normally the district-year join of lake
    ``complaints`` and ``grievance_redactions``.  A #72-style consumer calls
    this before duplicate-adjusted aggregation; a changed or incomplete lake,
    a missing/extra/duplicate group ticket, legacy rows without provenance,
    and a mix of group runs all raise instead of being combined silently. The
    verified digest is returned for callers that need to record it in their
    own output provenance.
    """
    source_records = list(source_records)
    expected = source_snapshot_id(source_records)
    source_tickets = {record["ticket_no"] for record in source_records}

    observed: set[tuple[object, object]] = set()
    group_tickets: set[str] = set()
    duplicate_group_tickets = 0
    blank_group_tickets = 0
    for row in group_rows:
        ticket_no = row.get("ticket_no")
        if not isinstance(ticket_no, str) or not ticket_no.strip():
            blank_group_tickets += 1
        elif ticket_no in group_tickets:
            duplicate_group_tickets += 1
        else:
            group_tickets.add(ticket_no)
        observed.add((row.get("source_name"), row.get("source_snapshot_id")))

    if blank_group_tickets:
        raise DedupSourceSnapshotMismatch(
            "dedup group provenance has invalid ticket rows: "
            f"blank_or_non_string={blank_group_tickets}"
        )
    if duplicate_group_tickets:
        raise DedupSourceSnapshotMismatch(
            "dedup group provenance has duplicate ticket rows: "
            f"duplicates={duplicate_group_tickets}"
        )

    required = {(DEDUP_SOURCE_NAME, expected)}
    if observed != required:
        raise DedupSourceSnapshotMismatch(
            "dedup group provenance does not match the asserted source snapshot: "
            f"expected_labels={len(required)}, observed_labels={len(observed)}"
        )
    if group_tickets != source_tickets:
        raise DedupSourceSnapshotMismatch(
            "dedup group ticket population does not match the asserted source snapshot: "
            f"missing_group_rows={len(source_tickets - group_tickets)}, "
            f"extra_group_rows={len(group_tickets - source_tickets)}"
        )
    return expected


def strip_placeholders(text: str) -> str:
    """Remove typed PII placeholder tokens (``[PHONE]``, ``[NAME]``, ...)
    and collapse the resulting whitespace.

    Called by :func:`shingles` before n-gramming — see module docstring
    point 2. Exposed separately because callers (and tests) may want to
    inspect the stripped text on its own.
    """
    stripped = _PLACEHOLDER_RE.sub(" ", text)
    return " ".join(stripped.split())


def shingles(text: str, k: int = DEFAULT_SHINGLE_SIZE) -> set[str]:
    """Character k-gram shingles of ``text``.

    Strips typed PII placeholders first (module docstring point 2), then
    Unicode-normalizes to NFC and lowercases before n-gramming.

    NFC (canonical composition) matters specifically for Odia script:
    Unicode allows the same visible character to be encoded two ways —
    e.g. the vowel sign U+0B4B, or the canonically equivalent sequence
    U+0B47 U+0B3E — and which one a given OCR pass or storage layer emits
    is not something this module controls or can assume is consistent
    across the corpus. Without normalizing, the composed and decomposed
    encodings of the *same filing* produce disjoint shingle sets (different
    codepoint sequences, different sliding windows) and never share an LSH
    band, so byte-identical-looking text fails to dedup against itself.
    NFC (over NFD, or the compatibility forms NFKC/NFKD) is the deliberate
    choice: NFC resolves exactly this canonical-equivalence case — the
    concern here — without NFKC/NFKD's further compatibility folding (e.g.
    full-width digits onto ASCII, ligatures onto letter sequences), which
    would fold text that is not actually the same character sequence and
    is out of scope for this fix. `.lower()` runs after normalization —
    it only folds cased (Latin-script) characters, so it is a no-op on
    Odia script and simply normalizes case variation in
    English/romanized-Odia text.

    Character n-grams, not word tokens (module docstring point 1): this
    also means shingles never bridge scripts on their own (module docstring
    point 4) — a codepoint from Odia script and a codepoint from Latin
    script never appear in the same shingle in a way that produces a
    collision, because the two scripts occupy disjoint Unicode ranges.

    Returns an empty set for text that reduces to fewer than ``k``
    characters after stripping (e.g. an all-placeholder page).
    """
    normalized = unicodedata.normalize("NFC", strip_placeholders(text)).lower()
    if len(normalized) < k:
        return set()
    return {normalized[i : i + k] for i in range(len(normalized) - k + 1)}


def _base_hash(value: str) -> int:
    """Stable (process- and run-independent) integer hash of a string.

    Python's builtin ``hash()`` is salted per-process for strings, which
    would make signatures non-reproducible across runs — exactly wrong for
    an index that gets rebuilt incrementally. blake2b is stdlib, fast, and
    deterministic.
    """
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _permutations(num_hashes: int) -> list[tuple[int, int]]:
    """``num_hashes`` deterministic ``(a, b)`` coefficient pairs for the
    MinHash permutation family ``h(x) = (a*x + b) mod _MERSENNE_PRIME``.

    Seeded from a fixed constant rather than system randomness, so calling
    this twice with the same ``num_hashes`` always yields the same
    permutations — required for signatures to be comparable across
    documents indexed in different batches or processes.
    """
    rng = random.Random(_PERMUTATION_SEED)
    return [
        (rng.randint(1, _MERSENNE_PRIME - 1), rng.randint(0, _MERSENNE_PRIME - 1))
        for _ in range(num_hashes)
    ]


def minhash_signature(
    shingle_set: Iterable[str], num_hashes: int = DEFAULT_NUM_HASHES
) -> tuple[int, ...] | None:
    """MinHash signature of a shingle set: ``num_hashes`` integers, each the
    minimum of a distinct hash permutation applied to every shingle.

    Two documents' signatures agree in a fraction of positions that is an
    unbiased estimator of the Jaccard similarity of their shingle sets —
    the standard MinHash property, checked directly in
    ``tests/test_dedup.py``.

    Returns ``None`` for an empty shingle set (e.g. an all-placeholder page
    or genuinely short text below :data:`DEFAULT_SHINGLE_SIZE` characters,
    see :func:`shingles`) — this is an **abstention, not a signature**. An
    earlier version of this function returned a constant sentinel tuple
    here so every such document would compare equal to every other one;
    that made blank pages and short-but-real filings like "road" or "pump"
    mutual duplicate candidates of each other and inflated duplicate
    prevalence on the first index build. There is nothing lexically
    meaningful to hash, so callers must not feed the result to
    :func:`lsh_bands` (which raises on ``None``) or treat it as a duplicate
    match; documents with no signature are low-signal/spam-review
    candidates for a different stage, not duplicate evidence.

    Raises ``ValueError`` for ``num_hashes <= 0``. Without this check a
    nonpositive value silently produced an *empty* tuple instead of either
    a real signature or the ``None`` abstention above — a signature that
    :func:`lsh_bands` would then accept (an empty tuple trivially satisfies
    ``0 % num_bands == 0``), hash zero-length slices for every band, and
    so give every document in the slice the same band values, collapsing
    the whole indexed slice into one duplicate group. That is a
    misconfiguration, not a legitimate abstention, so it fails loudly here
    instead of surfacing three functions downstream as a silent mass
    false-duplicate.
    """
    if num_hashes <= 0:
        raise ValueError(f"num_hashes must be positive, got {num_hashes}")
    base_hashes = [_base_hash(s) for s in shingle_set]
    if not base_hashes:
        return None
    return tuple(
        min((a * h + b) % _MERSENNE_PRIME for h in base_hashes)
        for a, b in _permutations(num_hashes)
    )


def lsh_bands(
    signature: tuple[int, ...] | None, num_bands: int = DEFAULT_NUM_BANDS
) -> tuple[int, ...]:
    """Split a MinHash ``signature`` into ``num_bands`` bands and return one
    hash per band.

    Two documents that share a band hash at the same band index are LSH
    *candidates*: worth an exact Jaccard/union-find comparison, not
    guaranteed duplicates. Banding trades exact nearest-neighbor search for
    a tunable false-positive/false-negative tradeoff — fewer, wider bands
    catch fewer near-duplicates but generate fewer false candidates; more,
    narrower bands do the opposite (see the arithmetic on
    ``DEFAULT_NUM_BANDS`` above). The blocking by district and time window
    that ROADMAP §5.2 calls for happens one level up, in the caller that
    groups documents before ever calling this function — this function only
    bands one signature.

    Raises ``ValueError`` if ``signature`` is ``None`` — the abstention
    :func:`minhash_signature` returns for an empty shingle set. There is no
    band hash that means "no signature" without accidentally making all
    such documents band-match each other, so this is a hard error: the
    caller must branch on ``None`` before banding, not push it through.
    Also raises ``ValueError`` if ``num_bands`` does not evenly divide the
    signature length, since an uneven last band would silently change the
    banding scheme's guarantees.
    """
    if signature is None:
        raise ValueError(
            "cannot band a None signature — minhash_signature() returned "
            "None for an empty shingle set; treat that as low-signal/spam, "
            "not a duplicate candidate, and do not call lsh_bands() on it"
        )
    if not signature:
        # An empty signature passes the divisibility check below (0 % n == 0),
        # every band then hashes the same empty tuple, and every empty-signature
        # record band-matches every other -- collapsing the indexed slice into
        # one duplicate group. The num_hashes <= 0 guard upstream protects the
        # producer; a signature read back from `dedup_signatures` or built by
        # hand never passes through minhash_signature() at all, which is the
        # path that matters once signatures are persisted for the backfill.
        raise ValueError(
            "cannot band an empty signature — every band would hash the same "
            "empty tuple, so unrelated records would all become candidates"
        )
    if num_bands <= 0 or len(signature) % num_bands != 0:
        raise ValueError(
            f"num_bands ({num_bands}) must evenly divide the signature "
            f"length ({len(signature)})"
        )
    rows_per_band = len(signature) // num_bands
    bands = []
    for i in range(num_bands):
        band = signature[i * rows_per_band : (i + 1) * rows_per_band]
        band_digest = hashlib.blake2b(repr(band).encode("utf-8"), digest_size=8)
        bands.append(int.from_bytes(band_digest.digest(), "big"))
    return tuple(bands)


def jaccard_similarity(shingles_a: Iterable[str], shingles_b: Iterable[str]) -> float:
    """Exact Jaccard similarity ``|A ∩ B| / |A ∪ B|`` of two shingle sets.

    This is the verification step LSH candidate generation calls for but
    does not perform itself (module docstring point 6): :func:`lsh_bands`
    reports a shared band as a *candidate*, and :func:`union_find_groups`
    unions whatever pairs it is handed with no verification of its own —
    call this on the real shingle sets (not the MinHash signatures, which
    only *estimate* Jaccard) for every candidate pair, and only pass pairs
    at or above your chosen duplicate threshold into
    :func:`union_find_groups`. See the end-to-end test in
    ``tests/test_dedup.py`` for the full candidate -> verify -> union
    pattern.

    Returns ``0.0`` for two empty shingle sets rather than the
    mathematically undefined ``0/0``: nothing to compare is not evidence of
    similarity, the same "refuse rather than claim a match" stance
    :func:`minhash_signature` and :func:`identity_key` take for a single
    empty/blank input (module docstring point 5) — this just extends it to
    the pairwise case in case a caller passes two abstained-from inputs
    through instead of already having skipped them.
    """
    set_a, set_b = set(shingles_a), set(shingles_b)
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def union_find_groups(
    pairs: Iterable[tuple[str, str]],
    items: Iterable[str] | None = None,
) -> dict[str, str]:
    """Union-find over **already-verified** candidate pairs (e.g. document
    ids whose shingle sets passed a :func:`jaccard_similarity` threshold
    after an LSH candidate search), returning ``{item: group_id}`` for
    every item seen.

    This function performs blind transitive closure: it does not compute
    or check any similarity itself, and trusts every pair it is given.
    Passing raw LSH candidate pairs (a shared band, not a verified match —
    see :func:`lsh_bands`) straight into this function turns every chance
    band collision into a confirmed duplicate group, and transitive merging
    then pulls further, unrelated documents into that false group. Verify
    with :func:`jaccard_similarity` first — this is module docstring
    point 6, and it is a real hazard, not a hypothetical one: an earlier
    version of ``tests/test_dedup.py`` fed shared-band buckets straight
    into this function without that check.

    Merges are **transitive**: if pairs contain ``("a", "b")`` and
    ``("b", "c")``, ``a``, ``b`` and ``c`` all land in one group even though
    ``a`` and ``c`` never appeared together in a pair — exactly the case LSH
    banding produces, since it only reports pairwise band collisions, not a
    full pairwise comparison. This is precisely why unverified input is
    dangerous here: one false-positive candidate pair does not just merge
    two documents, it can chain-merge everything transitively reachable
    through it.

    ``group_id`` is the lexicographically smallest item string in the
    group, so it is stable and does not depend on the order pairs arrive
    in. Pass ``items`` to also include documents that never candidate-paired
    with anything — they come back as singleton groups keyed to themselves,
    which is the "not a duplicate of anything found" case.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            return
        # Attach the larger root under the smaller so the group id is
        # always the lexicographically smallest member, regardless of the
        # order pairs were unioned in.
        if root_a < root_b:
            parent[root_b] = root_a
        else:
            parent[root_a] = root_b

    for a, b in pairs:
        union(a, b)
    if items is not None:
        for item in items:
            parent.setdefault(item, item)

    return {item: find(item) for item in parent}


def _canonical_phone_digits(value: str) -> str | None:
    """If ``value`` is shaped like an Indian phone number, return its
    canonical 10-digit ASCII form; otherwise ``None``.

    Collapses the common ways the same mobile number shows up across
    `petitioner_mobile` / OCR / form entry — "+91 98612 34567",
    "09861234567", "98612-34567", "9861234567", or the same number typed in
    Odia or Devanagari numerals ("୯୮୬୧୨୩୪୫୬୭") — to one ASCII string
    ("9861234567"). Odia/Devanagari digits are translated to ASCII *first*
    (via ``_INDIC_DIGITS_TO_ASCII``), so a citizen number entered in
    Odia-script numerals canonicalizes to the same key as the ASCII form —
    this is a primary input path for this corpus, not an edge case,
    exactly where resubmission linkage most needs to work. Only then are
    spaces/hyphens/parens dropped and a recognized leading country code
    ("91", for a resulting 12-digit string) or trunk prefix ("0", for a
    resulting 11-digit string) stripped down to the bare 10-digit
    subscriber number.

    The digit match after translation is deliberately ``[0-9]``, not
    ``\\d``: Python's ``\\d`` matches *any* Unicode decimal-digit
    character, not just ASCII — Bengali, Tamil, Gurmukhi, full-width digits
    and more all satisfy it. Without translating a script first, treating
    its digits as fair game for the length/prefix arithmetic below would
    silently misparse them (a lone untranslated Odia digit sitting next to
    ASCII digits, for instance, would still count toward "12 digits,
    strip 91" purely by ``\\d`` matching it, with no translation applied to
    make that arithmetic correct). Restricting to ``[0-9]`` after
    translating only the two scripts this corpus actually uses means any
    *other* digit script fails the match outright and falls through to
    :func:`identity_key`'s plain trim+lowercase path instead — it hashes
    consistently (so repeated values in that script still link to each
    other) but does not claim to link to the ASCII/Odia/Devanagari form of
    the same number. That is an accepted gap, not silently patched over:
    pinned in ``tests/test_dedup.py`` alongside the module's other
    cross-script limitations, not "fixed" by transliterating scripts this
    corpus does not use.

    Deliberately narrow rather than "keep the last 10 digits of anything
    10-15 digits long": that cruder rule would fold an unrelated 11-digit
    string starting with a stray leading digit (e.g. a typo'd
    "19861234567") onto the real "9861234567", which is exactly the kind
    of false resubmission match a dedup index must not manufacture.
    Anything that isn't a bare 10-digit number, or a 10-digit number behind
    a recognized 0/91 prefix, returns ``None`` — including email addresses,
    short non-phone identifiers, and phone numbers in an unhandled digit
    script — so :func:`identity_key` falls back to plain trim+lowercase for
    those.
    """
    ascii_digits = value.strip().translate(_INDIC_DIGITS_TO_ASCII)
    condensed = re.sub(r"[\s\-()]", "", ascii_digits)
    if not re.fullmatch(r"\+?[0-9]{10,13}", condensed):
        return None
    digits = condensed.lstrip("+")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def identity_key(value: str, salt: str) -> str | None:
    """Salted hash of a citizen identity value (mobile number, email) for
    resubmission linkage ("same citizen, same issue" — ROADMAP §5.2,
    duplicate relation 1).

    This is a **separate path** from the text path above (module docstring
    point 3): it never sees redacted text and its output never enters
    :func:`shingles`. Canonicalization happens here, before hashing —
    nothing derived from ``value`` or ``salt`` is ever written back into
    text that could reach the shingling path. Callers detect resubmission
    by comparing ``identity_key`` values directly (equal salted hash =>
    same underlying identity), and detect campaign/near-duplicate text via
    the MinHash/LSH functions — the two signals are combined by the
    caller, never by this module.

    ``salt`` is supplied by the caller (e.g. from deployment config/secrets,
    not hardcoded here) and must be kept outside version control and
    outside the redacted-text tables. Rotating the salt invalidates all
    existing identity keys, which is the intended way to revoke a
    compromised salt.

    Phone-shaped values are canonicalized via :func:`_canonical_phone_digits`
    before hashing, so "+91 98612 34567", "09861234567", "98612-34567",
    "9861234567" and the same number typed in Odia or Devanagari numerals
    all produce the *same* key — without this, the one job this function
    exists for (linking same-citizen resubmissions on `petitioner_mobile`)
    silently fails on real data, since those are all the same subscriber
    typed differently, and Odia-script/OCR-entered numbers are a primary
    input path here, not a corner case. A phone number in any other digit
    script is not canonicalized (see that function's docstring for why) and
    falls to the plain-text path below instead. Anything else (email
    addresses) is only stripped and lowercased, so "Citizen@Example.com"
    and " citizen@example.com " match but distinct addresses stay distinct.

    Returns ``None`` — an abstention, the same contract
    :func:`minhash_signature` uses for "nothing meaningful here" (module
    docstring point 5) — if ``value`` is blank or whitespace-only after
    normalization. `petitioner_mobile` / `petitioner_email` are nullable
    columns and blank strings are not normalized to ``None`` at ingestion,
    so hashing `""` would otherwise give every citizen with a missing
    contact field the *same* valid-looking salted hash, merging unrelated
    citizens into one "identity" purely because both left the field blank.
    Callers must check for ``None`` and skip resubmission linkage for that
    record rather than treat a blank contact field as an identity.

    Raises ``ValueError`` — not the ``None`` abstention above — if
    ``salt`` is blank or whitespace-only. A blank ``value`` is a property
    of one citizen's record: skipping that one record and hashing the rest
    normally is the correct, contained response. A blank ``salt`` is a
    property of the *deployment*: every key this function has ever
    produced or will produce in that run is affected identically, so
    "abstain and keep going" is the wrong response — it would silently
    keep emitting keys that all fail the same way. A mobile number is one
    of 10 billion (10^10) enumerable values; hashed without a real salt,
    the "salted hash" is offline-invertible over that space, which
    defeats the exact secret/rotation boundary this docstring documents
    two paragraphs up. That is a citizen-PII exposure, not an
    input-validation nicety, so it fails loudly and immediately rather
    than degrading per-record.
    """
    if not salt.strip():
        raise ValueError(
            "identity_key() requires a non-blank salt: a blank/"
            "whitespace-only salt would still produce stable-looking "
            "hashes, but over an enumerable ~10^10 mobile-number space "
            "that is effectively unsalted and offline-invertible, "
            "defeating the secret/rotation boundary this function exists "
            "to provide. Fix the deployment config; do not retry with an "
            "empty string."
        )
    canonical_phone = _canonical_phone_digits(value)
    normalized = canonical_phone if canonical_phone is not None else value.strip().lower()
    if not normalized:
        return None
    digest = hashlib.blake2b(
        f"{salt}\x00{normalized}".encode("utf-8"), digest_size=32
    )
    return digest.hexdigest()
