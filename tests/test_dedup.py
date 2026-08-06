"""MinHash/LSH duplicate index (janasunani.pipeline.dedup).

`dedup.py` is deliberately stdlib-only (hashlib, re, random, unicodedata) so
it — and this test file — run in CI with no pipeline extras installed.
Unlike every other pipeline test, this file does NOT `pytest.importorskip`:
it is the one real code-path CI coverage the whole intelligence layer
(Phase 15) has, per docs/ROADMAP.md §5.2/§5.6. Do not add a guard here.

Synthetic strings only — no real grievance text, no `data/` access.
"""

from itertools import combinations

import pytest

from janasunani.pipeline.dedup import (
    DEFAULT_NUM_HASHES,
    identity_key,
    jaccard_similarity,
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

# Digit-only translation tables built the same way _INDIC_DIGITS_TO_ASCII is
# built in dedup.py, but inverted (ASCII -> script) so these fixtures are
# generated, not hand-transcribed Unicode literals that would be easy to get
# subtly wrong and hard to review.
_ASCII_TO_ODIA_DIGITS = str.maketrans({str(i): chr(0x0B66 + i) for i in range(10)})
_ASCII_TO_DEVANAGARI_DIGITS = str.maketrans({str(i): chr(0x0966 + i) for i in range(10)})
# Bengali digits (U+09E6) — a script dedup.py deliberately does NOT
# canonicalize, to pin the accepted "unhandled script" fallback behavior.
_ASCII_TO_BENGALI_DIGITS = str.maketrans({str(i): chr(0x09E6 + i) for i in range(10)})

MOBILE_ASCII = "9861234567"
MOBILE_ODIA_DIGITS = MOBILE_ASCII.translate(_ASCII_TO_ODIA_DIGITS)
MOBILE_DEVANAGARI_DIGITS = MOBILE_ASCII.translate(_ASCII_TO_DEVANAGARI_DIGITS)
MOBILE_BENGALI_DIGITS = MOBILE_ASCII.translate(_ASCII_TO_BENGALI_DIGITS)

# Same Odia word, two canonically equivalent Unicode encodings: KA (U+0B15)
# followed by either the composed vowel sign O (U+0B4B), or the canonically
# equivalent decomposed sequence vowel sign E (U+0B47) + AA length mark
# (U+0B3E) — the exact pair Codex's review cited. Built from codepoints via
# chr(), not pasted glyphs, so the two forms are unambiguous on review and
# cannot be silently re-normalized by an editor/git. Real OCR/storage
# layers are not guaranteed to agree on which form they emit for the same
# filing.
_ODIA_KA = chr(0x0B15)
_ODIA_O_COMPOSED = chr(0x0B4B)
_ODIA_E_SIGN = chr(0x0B47)
_ODIA_AA_LENGTH_MARK = chr(0x0B3E)
ODIA_WORD_NFC = "respected sir " + _ODIA_KA + _ODIA_O_COMPOSED + " village pump broken"
ODIA_WORD_NFD = (
    "respected sir "
    + _ODIA_KA
    + _ODIA_E_SIGN
    + _ODIA_AA_LENGTH_MARK
    + " village pump broken"
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


def test_shingles_normalizes_canonically_equivalent_unicode_forms():
    # ODIA_WORD_NFC and ODIA_WORD_NFD are the *same visible filing text* in
    # two different, canonically equivalent Unicode encodings (composed
    # U+0B4B vs. decomposed U+0B47 U+0B3E). Without NFC normalization these
    # produce disjoint codepoint sequences and disjoint shingle sets, so a
    # filing would fail to dedup against a byte-different encoding of
    # itself depending on what the OCR/storage layer happened to emit.
    assert ODIA_WORD_NFC != ODIA_WORD_NFD  # confirm the fixtures really differ
    assert shingles(ODIA_WORD_NFC) == shingles(ODIA_WORD_NFD)


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


def test_minhash_signature_rejects_nonpositive_num_hashes():
    # num_hashes <= 0 used to silently return an empty tuple (neither a
    # real signature nor the None abstention) — lsh_bands() then accepted
    # it (0 % num_bands == 0), hashed empty slices, and gave every document
    # in a slice the same band values, collapsing the whole slice into one
    # duplicate group. This is a misconfiguration and must fail loudly.
    with pytest.raises(ValueError):
        minhash_signature(shingles(CAMPAIGN_A), num_hashes=0)
    with pytest.raises(ValueError):
        minhash_signature(shingles(CAMPAIGN_A), num_hashes=-1)
    # The same check applies even for an already-empty shingle set — an
    # invalid num_hashes is an error regardless of what else is wrong.
    with pytest.raises(ValueError):
        minhash_signature(set(), num_hashes=0)


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


# --- jaccard_similarity ---------------------------------------------------


def test_jaccard_similarity_identical_sets_is_one():
    s = shingles(CAMPAIGN_A)
    assert jaccard_similarity(s, s) == 1.0


def test_jaccard_similarity_disjoint_sets_is_zero():
    assert jaccard_similarity({"aaaaa"}, {"bbbbb"}) == 0.0


def test_jaccard_similarity_matches_manual_computation():
    a = shingles(CAMPAIGN_A)
    b = shingles(CAMPAIGN_C_REWORDED)
    expected = len(a & b) / len(a | b)
    assert jaccard_similarity(a, b) == expected


def test_jaccard_similarity_both_empty_is_zero_not_undefined():
    # 0/0 is mathematically undefined; refuse to claim similarity for two
    # abstained-from ("nothing here") inputs rather than raising or
    # guessing — the same "refuse rather than hash/claim a match" stance
    # the module takes for a single empty/blank input.
    assert jaccard_similarity(set(), set()) == 0.0


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
    # Full pipeline: shingle -> minhash -> band -> bucket by band ->
    # VERIFY with jaccard_similarity -> union. The verification step is not
    # optional here: union_find_groups() blindly trusts every pair it is
    # given (see its docstring), so LSH candidates — a shared band, not a
    # confirmed match — must be checked against the real shingle sets
    # before they are allowed to merge documents into a duplicate group.
    docs = {
        "sig_a": CAMPAIGN_A,
        "sig_b": CAMPAIGN_B,
        "sig_c": CAMPAIGN_C_REWORDED,
        "sig_u": UNRELATED_COMPLAINT,
    }
    doc_shingles = {doc_id: shingles(text) for doc_id, text in docs.items()}
    signatures = {
        doc_id: minhash_signature(s, num_hashes=128) for doc_id, s in doc_shingles.items()
    }
    buckets: dict[tuple[int, int], list[str]] = {}
    for doc_id, sig in signatures.items():
        for band_index, band_hash in enumerate(lsh_bands(sig, num_bands=16)):
            buckets.setdefault((band_index, band_hash), []).append(doc_id)

    duplicate_threshold = 0.5
    candidate_pairs = set()
    for bucket_docs in buckets.values():
        if len(bucket_docs) > 1:
            # Every unordered pair, not star edges from an arbitrary first
            # member (#101). Exact Jaccard verification is not transitive: in
            # a bucket of three the first can fall below the threshold against
            # both others while those two exceed it against each other, and if
            # that bucket is their only shared band the duplicate is lost.
            # This block is the pattern people copy, so it has to be the right
            # one -- janasunani/pipeline/dedup_index.py does the same.
            candidate_pairs.update(combinations(sorted(bucket_docs), 2))

    verified_pairs = [
        (doc_a, doc_b)
        for doc_a, doc_b in candidate_pairs
        if jaccard_similarity(doc_shingles[doc_a], doc_shingles[doc_b])
        >= duplicate_threshold
    ]

    groups = union_find_groups(verified_pairs, items=docs.keys())
    assert groups["sig_a"] == groups["sig_b"] == groups["sig_c"]
    assert groups["sig_u"] != groups["sig_a"]


def test_union_find_groups_trusts_unverified_pairs_this_is_why_verification_matters():
    # union_find_groups() does not know or check what a "pair" means — it
    # performs blind transitive closure. Feed it two clearly unrelated
    # documents directly (skipping jaccard_similarity() verification on
    # purpose) and it merges them anyway: this is the exact unsafe pattern
    # the module docstring (point 6) and union_find_groups()'s own
    # docstring warn against, pinned here so the warning stays true and
    # the safe end-to-end test above cannot silently regress to this.
    unverified_pairs = [("sig_a", "sig_u")]
    groups = union_find_groups(unverified_pairs)
    assert groups["sig_a"] == groups["sig_u"]  # merged despite being unrelated
    true_similarity = jaccard_similarity(
        shingles(CAMPAIGN_A), shingles(UNRELATED_COMPLAINT)
    )
    assert true_similarity < 0.3  # confirms they should NOT have merged


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


def test_identity_key_canonicalizes_odia_digit_phone_number():
    # Odia-script and OCR-entered contact numbers are a primary input path
    # for this corpus, not an edge case — the same subscriber typed in
    # Odia numerals must link to the ASCII form for resubmission detection
    # to work where it matters most.
    assert identity_key(MOBILE_ODIA_DIGITS, "s") == identity_key(MOBILE_ASCII, "s")


def test_identity_key_canonicalizes_devanagari_digit_phone_number():
    assert identity_key(MOBILE_DEVANAGARI_DIGITS, "s") == identity_key(
        MOBILE_ASCII, "s"
    )


def test_identity_key_canonicalizes_odia_digits_with_country_code():
    # The 91 country-code prefix stripping must also apply after Indic
    # digits are translated to ASCII, not just to already-ASCII input.
    odia_with_country_code = ("91" + MOBILE_ASCII).translate(_ASCII_TO_ODIA_DIGITS)
    assert identity_key(odia_with_country_code, "s") == identity_key(
        MOBILE_ASCII, "s"
    )


def test_identity_key_canonicalizes_mixed_ascii_and_odia_digits():
    # A realistic mixed-entry case: ASCII "+91" country code typed
    # normally, local subscriber number in Odia numerals.
    mixed = "+91 " + MOBILE_ODIA_DIGITS
    assert identity_key(mixed, "s") == identity_key(MOBILE_ASCII, "s")


def test_identity_key_unhandled_digit_script_does_not_link_but_stays_stable():
    # Bengali digits are a deliberately unhandled script (see
    # _canonical_phone_digits' docstring): dedup.py only transliterates
    # Odia and Devanagari, matching the scripts this corpus actually uses.
    # A Bengali-digit phone number must NOT silently claim to be the same
    # identity as its ASCII form (that would be papering over a real gap),
    # but it must still hash stably and distinctly from a different number
    # in the same script — falling to the literal-text path, not crashing
    # or colliding.
    bengali_key = identity_key(MOBILE_BENGALI_DIGITS, "s")
    assert bengali_key != identity_key(MOBILE_ASCII, "s")
    assert bengali_key == identity_key(MOBILE_BENGALI_DIGITS, "s")  # stable
    other_number_bengali = "9007654321".translate(_ASCII_TO_BENGALI_DIGITS)
    assert bengali_key != identity_key(other_number_bengali, "s")


def test_identity_key_abstains_on_blank_value():
    # petitioner_mobile / petitioner_email are nullable and blank strings
    # are not normalized to None at ingestion (janasunani/db/models.py).
    # Hashing "" would give every citizen with a missing contact field the
    # same valid-looking salted hash, merging unrelated citizens as one
    # "identity" purely because both left the field blank. Must abstain
    # (None) instead — the same contract minhash_signature() uses.
    assert identity_key("", "salt-1") is None
    assert identity_key("   ", "salt-1") is None
    assert identity_key("\t\n", "salt-1") is None


def test_identity_key_two_blank_records_do_not_look_like_the_same_citizen():
    # Both abstain; a caller comparing "identity_key(a) == identity_key(b)"
    # naively would otherwise treat two citizens who both left the contact
    # field blank as a resubmission match. Branching on None (per the
    # docstring contract) avoids ever reaching that comparison.
    assert identity_key("", "salt-1") is None
    assert identity_key("", "salt-2") is None  # different salt, still None


def test_identity_key_real_value_still_hashes_normally():
    # Sanity check alongside the abstention: a non-blank value is
    # unaffected by the blank-value guard.
    assert identity_key(MOBILE_ASCII, "s") is not None


def test_identity_key_rejects_blank_salt():
    # A blank salt is a deployment misconfiguration, not a per-record
    # abstention case: every key that run produces is affected identically,
    # and a "salted" hash over the ~10^10 enumerable mobile-number space
    # without a real salt is effectively unsalted and offline-invertible.
    # This must raise, not silently return None like the blank-value case —
    # abstaining would mean quietly continuing to emit compromised keys for
    # every subsequent record instead of stopping the misconfigured run.
    with pytest.raises(ValueError):
        identity_key(MOBILE_ASCII, "")
    with pytest.raises(ValueError):
        identity_key(MOBILE_ASCII, "   ")
    with pytest.raises(ValueError):
        identity_key(MOBILE_ASCII, "\t\n")


def test_identity_key_blank_salt_rejected_even_for_a_blank_value():
    # The salt check must not be shadowed by the value-abstention path —
    # a blank salt is invalid regardless of what value it would have been
    # applied to.
    with pytest.raises(ValueError):
        identity_key("", "")


def test_identity_key_real_salt_still_hashes_normally():
    # Sanity check alongside the guard: a non-blank salt is unaffected.
    assert identity_key(MOBILE_ASCII, "a-real-salt") is not None


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


class TestEmptySignatureIsRejected:
    """#100. An empty signature passes the divisibility check (0 % n == 0),
    every band hashes the same empty tuple, and every such record band-matches
    every other — collapsing the slice into one duplicate group."""

    def test_empty_tuple_is_rejected(self):
        with pytest.raises(ValueError, match="empty signature"):
            lsh_bands((), num_bands=4)

    def test_none_is_still_rejected_separately(self):
        with pytest.raises(ValueError, match="None signature"):
            lsh_bands(None, num_bands=4)

    def test_two_empty_signatures_cannot_become_candidates(self):
        """The failure this guards: without it both calls return identical band
        tuples and the records match on every band."""
        for signature in ((), ()):
            with pytest.raises(ValueError):
                lsh_bands(signature, num_bands=4)

    def test_a_real_signature_still_bands(self):
        signature = minhash_signature({"abc", "bcd", "cde"}, num_hashes=8)
        assert signature is not None
        assert len(lsh_bands(signature, num_bands=4)) == 4
