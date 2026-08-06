"""MinHash/LSH duplicate index over character n-grams (Phase 14, W6).

Deterministic, CPU-only, no model — the algorithmic core of "component (b)"
in docs/ROADMAP.md §5.2. Lives at the light `janasunani.pipeline` level
(like `ocr_quality.py` and `ticket.py`), not inside `stages/`, because it is
deliberately **stdlib-only**: `hashlib`, `re`, `random`, `collections.abc`.
No numpy, no presidio, no torch, no datasketch. That keeps this module (and
`tests/test_dedup.py`) importable and runnable in CI, which installs no
heavy extras — every other pipeline test guards with
`pytest.importorskip` and skips there. This is the one pipeline module that
doesn't, and is therefore the only real CI coverage over the whole
intelligence layer that Phase 15 converges on. Keep it that way: do not add
a dependency here to make an implementation nicer.

Wiring this in as the 7th pipeline stage (`spam_duplicate`, after
`pii_tagger`) is deliberately out of scope for this module — see the
tracking issue. This file only provides the primitives:
`shingles`, `minhash_signature`, `lsh_bands`, `union_find_groups`,
`strip_placeholders`, `identity_key`.

Four things that shape every function below:

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
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Iterable

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

    Strips typed PII placeholders first (module docstring point 2) and
    lowercases before n-gramming — ``.lower()`` only folds cased
    (Latin-script) characters, so this is a no-op on Odia script and simply
    normalizes case variation in English/romanized-Odia text.

    Character n-grams, not word tokens (module docstring point 1): this
    also means shingles never bridge scripts on their own (module docstring
    point 4) — a codepoint from Odia script and a codepoint from Latin
    script never appear in the same shingle in a way that produces a
    collision, because the two scripts occupy disjoint Unicode ranges.

    Returns an empty set for text that reduces to fewer than ``k``
    characters after stripping (e.g. an all-placeholder page).
    """
    normalized = strip_placeholders(text).lower()
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
    """
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


def union_find_groups(
    pairs: Iterable[tuple[str, str]],
    items: Iterable[str] | None = None,
) -> dict[str, str]:
    """Union-find over candidate pairs (e.g. document ids that shared an LSH
    band), returning ``{item: group_id}`` for every item seen.

    Merges are **transitive**: if pairs contain ``("a", "b")`` and
    ``("b", "c")``, ``a``, ``b`` and ``c`` all land in one group even though
    ``a`` and ``c`` never appeared together in a pair — exactly the case LSH
    banding produces, since it only reports pairwise band collisions, not a
    full pairwise comparison.

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


def identity_key(value: str, salt: str) -> str:
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
    """
    canonical_phone = _canonical_phone_digits(value)
    normalized = canonical_phone if canonical_phone is not None else value.strip().lower()
    digest = hashlib.blake2b(
        f"{salt}\x00{normalized}".encode("utf-8"), digest_size=32
    )
    return digest.hexdigest()
