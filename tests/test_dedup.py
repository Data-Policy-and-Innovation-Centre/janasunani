"""MinHash/LSH duplicate index (janasunani.pipeline.dedup).

`dedup.py` is deliberately stdlib-only (hashlib, re, random) so it — and
this test file — run in CI with no pipeline extras installed. Unlike every
other pipeline test, this file does NOT `pytest.importorskip`: it is the one
real code-path CI coverage the whole intelligence layer (Phase 15) has, per
docs/ROADMAP.md §5.2/§5.6. Do not add a guard here.

Synthetic strings only — no real grievance text, no `data/` access.
"""

import pytest

from janasunani.pipeline.dedup import (
    DEFAULT_NUM_HASHES,
    identity_key,
    lsh_bands,
    minhash_signature,
    shingles,
    strip_placeholders,
    union_find_groups,
)

# --- synthetic fixtures --------------------------------------------------

# A "campaign": near-identical grievance text as it would look after
# pii_tagger has redacted the signatory's name and phone. Two signatories,
# same underlying complaint.
CAMPAIGN_A = (
    "[NAME] [PHONE] respected sir the hand pump in our village has been "
    "broken for three months kindly send someone to repair it urgently"
)
CAMPAIGN_B = (
    "[NAME] [PHONE] respected sir the hand pump in our village has been "
    "broken for three months kindly send someone to repair it urgently"
)
# Same complaint, lightly reworded (a second signatory retyping it, not a
# copy-paste) — near-duplicate, not byte-identical.
CAMPAIGN_C_REWORDED = (
    "[NAME] [PHONE] respected sir the hand pump in our village has stayed "
    "broken for three months kindly send someone to repair it very urgently"
)

UNRELATED_COMPLAINT = (
    "[NAME] [PHONE] the municipal garbage truck has not visited our street "
    "in over a month and waste is piling up near the market causing a "
    "health hazard for children playing nearby"
)

# Real Odia-script Unicode text and an unrelated romanized-Odia string
# (Latin script). Not a translation of each other on purpose — the point of
# the cross-script test is codepoint disjointness, not semantic equivalence.
ODIA_SCRIPT_TEXT = (
    "ଆମ ଗାଁରେ ପାଣି ପମ୍ପ ତିନି ମାସ ହେଲା ଖରାପ ଅଛି। ଦୟାକରି ମରାମତି କରନ୍ତୁ।"
)
ROMANIZED_TEXT = (
    "aame ganre pani pump tini masa hela kharap achi doyakari maramati karantu"
)


# --- strip_placeholders ---------------------------------------------------


def test_strip_placeholders_removes_typed_pii_tokens():
    text = "[NAME] called about a leak, contact [PHONE] or [EMAIL]."
    stripped = strip_placeholders(text)
    assert "[NAME]" not in stripped
    assert "[PHONE]" not in stripped
    assert "[EMAIL]" not in stripped
    assert "called about a leak, contact" in stripped


def test_strip_placeholders_collapses_whitespace():
    assert strip_placeholders("a  [NAME]   b") == "a b"


def test_strip_placeholders_leaves_ordinary_bracket_free_text_alone():
    # Only the exact typed-token shape ([A-Z]+) is stripped.
    text = "the pump (broken) needs repair"
    assert strip_placeholders(text) == text


# --- shingles --------------------------------------------------------------


def test_shingles_are_character_ngrams_not_words():
    # Overlapping character windows, not whole-word tokens — the
    # word-tokenization behavior this module explicitly rejects (ROADMAP
    # §5.2). Word tokenization on "hello world" would yield exactly the two
    # tokens {"hello", "world"}; character 5-grams instead slide across the
    # word boundary, producing windows like "llo w" / "lo wo" / "o wor" that
    # straddle both words and have no word-token analogue.
    result = shingles("hello world", k=5)
    assert len(result) == len("hello world") - 5 + 1
    assert {"llo w", "lo wo", "o wor"} <= result
    assert all(len(s) == 5 for s in result)


def test_shingles_strips_placeholders_before_ngramming():
    with_placeholder = shingles("call [PHONE] now", k=4)
    without_placeholder = shingles("call now", k=4)
    assert with_placeholder == without_placeholder
    assert not any("PHONE" in s.upper() and "[" in s for s in with_placeholder)


def test_shingles_short_text_returns_empty_set():
    assert shingles("hi", k=5) == set()
    assert shingles("[PHONE]", k=5) == set()  # all-placeholder page


def test_shingles_case_insensitive_for_latin_script():
    assert shingles("Hello World", k=5) == shingles("hello world", k=5)


# --- minhash_signature -------------------------------------------------


def test_minhash_signature_deterministic_across_calls():
    s = shingles(CAMPAIGN_A)
    sig1 = minhash_signature(s)
    sig2 = minhash_signature(s)
    assert sig1 == sig2
    assert len(sig1) == DEFAULT_NUM_HASHES


def test_minhash_signature_identical_text_matches_exactly():
    sig_a = minhash_signature(shingles(CAMPAIGN_A))
    sig_b = minhash_signature(shingles(CAMPAIGN_B))
    assert sig_a == sig_b


def test_minhash_signature_empty_shingle_set_abstains():
    # An earlier version returned a constant sentinel signature here, which
    # made every blank/all-placeholder/too-short document a mutual
    # candidate of every other one and inflated duplicate prevalence. It
    # must abstain (None) instead — low-signal/spam territory, not
    # duplicate evidence.
    assert minhash_signature(set()) is None
    assert minhash_signature(shingles("hi")) is None  # too short to shingle
    real_sig = minhash_signature(shingles(CAMPAIGN_A))
    assert real_sig is not None


def test_minhash_signature_two_empty_documents_do_not_look_like_duplicates():
    # Both abstain; None == None would make them look like the same
    # signature if a caller compared naively, so this guards that a caller
    # branching on "is None" (per the docstring contract) never even gets
    # to a comparison — there is nothing to band or compare.
    doc_a = minhash_signature(shingles("[PHONE]"))
    doc_b = minhash_signature(shingles("[NAME]"))
    assert doc_a is None
    assert doc_b is None


def test_minhash_signature_approximates_jaccard_similarity():
    # Two texts sharing most but not all of a sentence: compute true
    # Jaccard directly from the shingle sets, then check the MinHash
    # signature agreement rate (the standard unbiased estimator) lands
    # close to it.
    shingles_a = shingles(CAMPAIGN_A)
    shingles_b = shingles(CAMPAIGN_C_REWORDED)
    true_jaccard = len(shingles_a & shingles_b) / len(shingles_a | shingles_b)
    assert 0.5 < true_jaccard < 0.95  # reworded, not identical, not disjoint

    num_hashes = 256
    sig_a = minhash_signature(shingles_a, num_hashes=num_hashes)
    sig_b = minhash_signature(shingles_b, num_hashes=num_hashes)
    agreement = sum(1 for x, y in zip(sig_a, sig_b) if x == y) / num_hashes
    assert abs(agreement - true_jaccard) < 0.1


def test_minhash_signature_dissimilar_texts_have_low_agreement():
    sig_a = minhash_signature(shingles(CAMPAIGN_A), num_hashes=256)
    sig_b = minhash_signature(shingles(UNRELATED_COMPLAINT), num_hashes=256)
    agreement = sum(1 for x, y in zip(sig_a, sig_b) if x == y) / 256
    true_jaccard = len(shingles(CAMPAIGN_A) & shingles(UNRELATED_COMPLAINT)) / len(
        shingles(CAMPAIGN_A) | shingles(UNRELATED_COMPLAINT)
    )
    assert true_jaccard < 0.3
    assert agreement < 0.3


# --- lsh_bands ---------------------------------------------------------


def test_lsh_bands_identical_signatures_share_every_band():
    sig_a = minhash_signature(shingles(CAMPAIGN_A))
    sig_b = minhash_signature(shingles(CAMPAIGN_B))
    assert lsh_bands(sig_a) == lsh_bands(sig_b)


def test_lsh_bands_near_duplicate_shares_at_least_one_band_with_default_bands():
    # CAMPAIGN_A vs CAMPAIGN_C_REWORDED have true Jaccard ~0.78 (two words
    # changed out of ~25) — a lightly reworded resubmission/campaign filing,
    # not a byte-identical one. This must work with *default* banding
    # (no num_bands override): DEFAULT_NUM_BANDS is chosen so this pair's
    # candidate probability is ~90% (see the arithmetic on
    # DEFAULT_NUM_BANDS in dedup.py) — a caller should not have to
    # remember to widen the bands for the primitive to do its one job.
    sig_a = minhash_signature(shingles(CAMPAIGN_A))
    sig_c = minhash_signature(shingles(CAMPAIGN_C_REWORDED))
    bands_a = set(lsh_bands(sig_a))
    bands_c = set(lsh_bands(sig_c))
    assert bands_a & bands_c, "near-duplicate campaign text produced no candidate band"


def test_lsh_bands_unrelated_text_shares_no_band_with_default_bands():
    sig_a = minhash_signature(shingles(CAMPAIGN_A))
    sig_u = minhash_signature(shingles(UNRELATED_COMPLAINT))
    bands_a = set(lsh_bands(sig_a))
    bands_u = set(lsh_bands(sig_u))
    assert not (bands_a & bands_u)


def test_lsh_bands_rejects_num_bands_not_dividing_signature_length():
    sig = minhash_signature(shingles(CAMPAIGN_A), num_hashes=100)
    with pytest.raises(ValueError):
        lsh_bands(sig, num_bands=7)


def test_lsh_bands_rejects_abstained_none_signature():
    # minhash_signature() abstains (returns None) on an empty shingle set;
    # lsh_bands() must refuse it outright rather than banding a sentinel,
    # so an all-placeholder/blank document can never band-match a real one.
    with pytest.raises(ValueError):
        lsh_bands(None)


# --- union_find_groups ---------------------------------------------------


def test_union_find_groups_transitive_merge():
    # a-b and b-c candidate pairs (as LSH would produce via shared bands)
    # must merge all three into one group even though a-c never co-occurred.
    pairs = [("doc_a", "doc_b"), ("doc_b", "doc_c")]
    groups = union_find_groups(pairs)
    assert groups["doc_a"] == groups["doc_b"] == groups["doc_c"]


def test_union_find_groups_keeps_disjoint_groups_separate():
    pairs = [("doc_a", "doc_b"), ("doc_x", "doc_y")]
    groups = union_find_groups(pairs)
    assert groups["doc_a"] == groups["doc_b"]
    assert groups["doc_x"] == groups["doc_y"]
    assert groups["doc_a"] != groups["doc_x"]


def test_union_find_groups_singletons_from_items_param():
    pairs = [("doc_a", "doc_b")]
    groups = union_find_groups(pairs, items=["doc_a", "doc_b", "doc_solo"])
    assert groups["doc_solo"] == "doc_solo"
    assert groups["doc_a"] == groups["doc_b"]


def test_union_find_groups_group_id_independent_of_pair_order():
    forward = union_find_groups([("doc_a", "doc_b"), ("doc_b", "doc_c")])
    backward = union_find_groups([("doc_c", "doc_b"), ("doc_b", "doc_a")])
    assert forward["doc_a"] == forward["doc_b"] == forward["doc_c"]
    assert backward["doc_a"] == backward["doc_b"] == backward["doc_c"]
    assert forward["doc_a"] == backward["doc_a"]  # same stable representative


def test_union_find_groups_end_to_end_campaign_vs_unrelated():
    # Full pipeline: shingle -> minhash -> band -> bucket by band -> union.
    docs = {
        "sig_a": CAMPAIGN_A,
        "sig_b": CAMPAIGN_B,
        "sig_c": CAMPAIGN_C_REWORDED,
        "sig_u": UNRELATED_COMPLAINT,
    }
    signatures = {
        doc_id: minhash_signature(shingles(text), num_hashes=128)
        for doc_id, text in docs.items()
    }
    buckets: dict[tuple[int, int], list[str]] = {}
    for doc_id, sig in signatures.items():
        for band_index, band_hash in enumerate(lsh_bands(sig, num_bands=16)):
            buckets.setdefault((band_index, band_hash), []).append(doc_id)

    pairs = []
    for bucket_docs in buckets.values():
        if len(bucket_docs) > 1:
            first = bucket_docs[0]
            pairs.extend((first, other) for other in bucket_docs[1:])

    groups = union_find_groups(pairs, items=docs.keys())
    assert groups["sig_a"] == groups["sig_b"] == groups["sig_c"]
    assert groups["sig_u"] != groups["sig_a"]


# --- identity_key ---------------------------------------------------------


def test_identity_key_same_value_and_salt_is_stable():
    assert identity_key("9861234567", "salt-1") == identity_key(
        "9861234567", "salt-1"
    )


def test_identity_key_different_salt_gives_different_key():
    key_1 = identity_key("9861234567", "salt-1")
    key_2 = identity_key("9861234567", "salt-2")
    assert key_1 != key_2


def test_identity_key_different_value_gives_different_key():
    key_a = identity_key("9861234567", "salt-1")
    key_b = identity_key("9007654321", "salt-1")
    assert key_a != key_b


def test_identity_key_normalizes_whitespace_and_case():
    assert identity_key(" Citizen@Example.com ", "s") == identity_key(
        "citizen@example.com", "s"
    )


def test_identity_key_canonicalizes_equivalent_phone_number_formats():
    # Same subscriber, four ways petitioner_mobile/OCR/form entry could
    # spell it — resubmission linkage is the one job identity_key exists
    # for, and it fails at that job if these don't collapse to one key.
    variants = [
        "+91 98612 34567",
        "09861234567",
        "98612-34567",
        "9861234567",
    ]
    keys = {identity_key(v, "salt-1") for v in variants}
    assert len(keys) == 1


def test_identity_key_distinct_phone_numbers_still_differ():
    assert identity_key("9861234567", "s") != identity_key("9861234568", "s")
    # Canonicalization must not fold distinct subscribers together just
    # because one is a substring of another after digit-stripping.
    assert identity_key("9861234567", "s") != identity_key("19861234567", "s")


def test_identity_key_non_phone_numeric_id_is_not_treated_as_a_phone_number():
    # A 6-digit PIN code (or any short numeric identifier) is shorter than
    # the 10-digit floor _canonical_phone_digits requires, so it must not
    # be silently reshaped — it falls back to plain trim+lowercase, and
    # distinct short codes must still produce distinct keys.
    assert identity_key("751001", "s") != identity_key("751002", "s")


def test_identity_key_never_appears_in_or_derives_shingle_text():
    # Guards the separation this module's docstring insists on: an identity
    # key is not text and must never be fed into the shingling path.
    mobile = "9861234567"
    key = identity_key(mobile, "salt-1")
    grievance_text = "[NAME] [PHONE] the drain near our house is blocked"
    doc_shingles = shingles(grievance_text)
    assert mobile not in grievance_text  # pii_tagger already redacted it
    assert not any(key in s or mobile in s for s in doc_shingles)


# --- cross-script non-support pin (ROADMAP §5.2 August contract) ---------


def test_cross_script_shingles_do_not_overlap():
    # Odia-script and romanized-Odia text occupy disjoint Unicode ranges,
    # so character n-grams never bridge them — pinned here, not papered
    # over. Real cross-script matching is Phase 17 (transliteration), not
    # this module.
    odia_shingles = shingles(ODIA_SCRIPT_TEXT, k=3)
    romanized_shingles = shingles(ROMANIZED_TEXT, k=3)
    assert odia_shingles  # sanity: the Odia text actually produced shingles
    assert romanized_shingles
    assert odia_shingles & romanized_shingles == set()


def test_cross_script_minhash_signatures_do_not_match():
    sig_odia = minhash_signature(shingles(ODIA_SCRIPT_TEXT, k=3), num_hashes=256)
    sig_romanized = minhash_signature(
        shingles(ROMANIZED_TEXT, k=3), num_hashes=256
    )
    agreement = sum(1 for x, y in zip(sig_odia, sig_romanized) if x == y) / 256
    assert agreement < 0.05  # effectively zero recall across scripts


def test_cross_script_lsh_bands_never_collide():
    sig_odia = minhash_signature(shingles(ODIA_SCRIPT_TEXT, k=3), num_hashes=128)
    sig_romanized = minhash_signature(
        shingles(ROMANIZED_TEXT, k=3), num_hashes=128
    )
    bands_odia = set(lsh_bands(sig_odia, num_bands=8))
    bands_romanized = set(lsh_bands(sig_romanized, num_bands=8))
    assert not (bands_odia & bands_romanized)
