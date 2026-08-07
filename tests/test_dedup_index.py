"""Dedup index backfill runner (real code path, SQLite OLTP).

`janasunani.pipeline.dedup_index` walks a redacted district-year slice,
computes MinHash/LSH signatures and salted identity keys, blocks candidate
generation by district/script/time-window, verifies every LSH *and*
identity-key candidate pair with exact Jaccard (a matching identity means
the same citizen, not the same issue -- it is not duplicate evidence on its
own), unions what passes with `dedup.py`'s `union_find_groups`, and persists
`dedup_signatures`/`dedup_groups`.

Synthetic complaint text only — no real grievance text, no `data/` access.
Salted with a fixed test-only salt (`_SALT`), never a real deployment
secret.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, select, text

from janasunani.db.models import (
    Base,
    Complaint,
    DedupGroup,
    DedupSignature,
    GrievanceRedaction,
)
from janasunani.pipeline.dedup import identity_key, minhash_signature, shingles
from janasunani.pipeline.dedup_index import (
    DEFAULT_NUM_HASHES,
    _script_of,
    _text_candidate_pairs,
    build_dedup_index,
)

_SALT = "unit-test-salt-do-not-use-in-prod"

# --- text fixtures (synthetic, mirrors tests/test_dedup.py's style) -------

CAMPAIGN_A = (
    "respected sir the hand pump in our village has been broken for three "
    "months kindly send someone to repair it urgently"
)
CAMPAIGN_C_REWORDED = (
    "respected sir the hand pump in our village has stayed broken for "
    "three months kindly send someone to repair it very urgently"
)
UNRELATED_A = (
    "the municipal garbage truck has not visited our street in over a "
    "month and waste is piling up near the market causing a health "
    "hazard for children playing nearby"
)
UNRELATED_B = (
    "the drainage near the old market has collapsed and rain water is "
    "flooding three houses every monsoon season"
)
UNRELATED_C = (
    "streetlights on the school road have been dark for two weeks and "
    "children walk home after tuition in complete darkness"
)

# Real Odia-script text and an unrelated romanized-Odia string (same
# fixtures test_dedup.py uses for the cross-script pin) -- disjoint Unicode
# ranges, not translations of each other.
ODIA_TEXT = "ଆମ ଗାଁରେ ପାଣି ପମ୍ପ ତିନି ମାସ ହେଲା ଖରାପ ଅଛି। ଦୟାକରି ମରାମତି କରନ୍ତୁ।"
ROMANIZED_TEXT = "aame ganre pani pump tini masa hela kharap achi doyakari maramati karantu"


def _make_oltp(tmp_path, complaints, redactions):
    """A SQLite OLTP seeded with `complaints` rows and `redactions` rows
    (a subset of ticket_nos -- rows with no redaction simulate a complaint
    that has not been through janasunani-redact-grievance yet)."""
    path = tmp_path / "oltp.db"
    sync_url = f"sqlite:///{path}"
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(Complaint), complaints)
        if redactions:
            conn.execute(insert(GrievanceRedaction), redactions)
    engine.dispose()
    return f"sqlite+aiosqlite:///{path}", sync_url


def _signature_rows(sync_url):
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(select(DedupSignature)).all()
    engine.dispose()
    return {r.ticket_no: r for r in rows}


def _group_rows(sync_url):
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(select(DedupGroup)).all()
    engine.dispose()
    return {r.ticket_no: r for r in rows}


# --- small fixture: slice scoping / resumability / salt / timestamps ------

# Complaints in the slice, mirroring test_redact_grievance.py's ROWS shape.
# Each gets a Complaint row; `redacted` is None for T6, which simulates a
# complaint that redact_grievance has not processed yet.
_SMALL_ROWS = [
    # ticket, district, year, created_on,           mobile,        redacted
    ("T1", "Khordha", 2024, datetime(2024, 1, 5), "9861234567", "water supply broken since June"),
    ("T2", "Khordha", 2024, datetime(2024, 1, 6), None, "no pension for three months"),
    ("T3", "Khordha", 2024, datetime(2024, 1, 7), None, "street light not working"),
    ("T4", "Khordha", 2023, datetime(2023, 1, 5), None, "different year, must not be touched"),
    ("T5", "Puri", 2024, datetime(2024, 1, 5), None, "different district, must not be touched"),
    ("T6", "Khordha", 2024, datetime(2024, 1, 8), None, None),  # never redacted
]


@pytest.fixture
def oltp(tmp_path):
    complaints = [
        {
            "ticket_no": t,
            "district": d,
            "created_year": y,
            "created_on": c,
            "petitioner_mobile": m,
            "grievance": f"raw grievance for {t}",
        }
        for t, d, y, c, m, _ in _SMALL_ROWS
    ]
    redactions = [
        {"ticket_no": t, "grievance_redacted": r}
        for t, _, _, _, _, r in _SMALL_ROWS
        if r is not None
    ]
    return _make_oltp(tmp_path, complaints, redactions)


class TestSliceScoping:
    """--district and --year have no defaults; the pending query must only
    ever touch the named slice."""

    def test_only_the_named_slice_is_indexed(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3"}

    def test_other_year_is_untouched(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert "T4" not in _signature_rows(sync_url)

    def test_other_district_is_untouched(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert "T5" not in _signature_rows(sync_url)

    def test_unredacted_complaint_is_never_indexed(self, oltp):
        """T6 has a `complaints` row but no `grievance_redactions` row --
        the pending query inner-joins to grievance_redactions, so a
        complaint that redact_grievance has not processed yet must never
        appear here, even though it is in the slice."""
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert "T6" not in _signature_rows(sync_url)


class TestResumability:
    def test_counts_report_the_slice(self, oltp):
        async_url, _ = oltp
        counts = build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert counts["total"] == 3
        assert counts["already_indexed"] == 0
        assert counts["processed"] == 3

    def test_second_run_is_a_no_op_for_signatures(self, oltp):
        async_url, _ = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        second = build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert second["already_indexed"] == 3
        assert second["processed"] == 0

    def test_an_interrupted_run_resumes_where_it_stopped(self, oltp):
        async_url, sync_url = oltp
        first = build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT, limit=2)
        assert first["processed"] == 2

        second = build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert second["already_indexed"] == 2
        assert second["processed"] == 1
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3"}

    def test_rerunning_does_not_duplicate_signature_rows(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert len(_signature_rows(sync_url)) == 3


class TestNaiveUTCTimestamps:
    """asyncpg refuses a tz-aware value into TIMESTAMP WITHOUT TIME ZONE
    while SQLite accepts one -- an aware value here passes every local test
    and fails on the first batch against the deployed Postgres (#129)."""

    def test_indexed_at_is_naive(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        assert row.indexed_at is not None
        assert row.indexed_at.tzinfo is None

    def test_grouped_at_is_naive(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _group_rows(sync_url)["T1"]
        assert row.grouped_at is not None
        assert row.grouped_at.tzinfo is None


class TestSaltRequirement:
    def test_missing_salt_raises_before_any_db_connection(self, oltp, monkeypatch):
        async_url, _ = oltp
        import janasunani.pipeline.dedup_index as dedup_index_module

        monkeypatch.setattr(dedup_index_module.settings, "DEDUP_SALT", None)

        def _boom(*args, **kwargs):
            raise AssertionError("create_async_engine must not be called without a salt")

        monkeypatch.setattr(dedup_index_module, "create_async_engine", _boom)

        with pytest.raises(ValueError, match="salt"):
            build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=None)

    def test_blank_salt_is_also_rejected(self, oltp):
        async_url, _ = oltp
        with pytest.raises(ValueError, match="salt"):
            build_dedup_index("Khordha", 2024, oltp_url=async_url, salt="   ")

    def test_salt_from_settings_is_used_when_not_passed_explicitly(self, oltp, monkeypatch):
        async_url, sync_url = oltp
        import janasunani.pipeline.dedup_index as dedup_index_module

        monkeypatch.setattr(dedup_index_module.settings, "DEDUP_SALT", "from-settings")
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=None)

        expected = identity_key("9861234567", "from-settings")
        assert _signature_rows(sync_url)["T1"].identity_key_mobile == expected


class TestThresholdValidation:
    """The backfill persists duplicate_group_id, so a bad threshold
    corrupts the slice instead of failing fast (review finding, #135):
    -1 would union every candidate, >1/NaN would reject every real one."""

    def _assert_rejected_before_any_db_connection(self, oltp, monkeypatch, threshold):
        async_url, _ = oltp
        import janasunani.pipeline.dedup_index as dedup_index_module

        def _boom(*args, **kwargs):
            raise AssertionError("create_async_engine must not be called with a bad threshold")

        monkeypatch.setattr(dedup_index_module, "create_async_engine", _boom)

        with pytest.raises(ValueError, match="threshold"):
            build_dedup_index(
                "Khordha", 2024, oltp_url=async_url, salt=_SALT, threshold=threshold
            )

    def test_negative_threshold_is_rejected(self, oltp, monkeypatch):
        self._assert_rejected_before_any_db_connection(oltp, monkeypatch, -1.0)

    def test_threshold_above_one_is_rejected(self, oltp, monkeypatch):
        self._assert_rejected_before_any_db_connection(oltp, monkeypatch, 1.5)

    def test_nan_threshold_is_rejected(self, oltp, monkeypatch):
        self._assert_rejected_before_any_db_connection(oltp, monkeypatch, float("nan"))

    def test_boundary_values_zero_and_one_are_accepted(self, oltp):
        async_url, _ = oltp
        # Must not raise.
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT, threshold=0.0)
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT, threshold=1.0)


class TestSignatureContents:
    def test_signature_is_a_full_length_minhash(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        assert row.signature is not None
        assert len(row.signature) == DEFAULT_NUM_HASHES

    def test_signature_round_trips_through_json_exactly(self, oltp):
        # The MinHash integers can run to 19 digits (< 2**61 - 1); a JSON
        # round trip through SQLite/Postgres must not lose precision or
        # silently coerce them through a float.
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        expected = minhash_signature(
            shingles("water supply broken since June"), num_hashes=DEFAULT_NUM_HASHES
        )
        assert tuple(row.signature) == expected

    def test_num_shingles_matches_the_redacted_text(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        assert row.num_shingles == len(shingles("water supply broken since June"))

    def test_index_version_is_stamped(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        assert row.index_version and "num_hashes=" in row.index_version


# --- large fixture: duplicate detection, blocking, cross-script, identity -


def _dup_oltp(tmp_path, rows):
    """`rows`: (ticket, district, year, created_on, mobile, email, raw_grievance,
    redacted_text_or_None)."""
    complaints = [
        {
            "ticket_no": t,
            "district": d,
            "created_year": y,
            "created_on": c,
            "petitioner_mobile": m,
            "petitioner_email": e,
            "grievance": raw,
        }
        for t, d, y, c, m, e, raw, _ in rows
    ]
    redactions = [
        {"ticket_no": t, "grievance_redacted": r}
        for t, _, _, _, _, _, _, r in rows
        if r is not None
    ]
    return _make_oltp(tmp_path, complaints, redactions)


@pytest.fixture
def dup_oltp(tmp_path):
    rows = [
        # --- campaign block (window 0: Jan 2024) ---
        ("T1", "Sambalpur", 2024, datetime(2024, 1, 5), "9861234567", None, "raw t1", CAMPAIGN_A),
        ("T2", "Sambalpur", 2024, datetime(2024, 1, 10), None, None, "raw t2", CAMPAIGN_A),
        ("T3", "Sambalpur", 2024, datetime(2024, 1, 12), None, None, "raw t3", CAMPAIGN_C_REWORDED),
        ("T4", "Sambalpur", 2024, datetime(2024, 1, 8), None, None, "raw t4", UNRELATED_A),
        # --- same citizen (mobile), different issue: must NOT group ---
        ("T5", "Sambalpur", 2024, datetime(2024, 6, 20), "9861234567", None, "raw t5", UNRELATED_B),
        # --- same citizen (mobile) AND matching text, different window:
        # must group -- identity widens the search, text still decides ---
        ("T5R", "Sambalpur", 2024, datetime(2024, 4, 15), "9861234567", None, "raw t5r", CAMPAIGN_A),
        # --- decoys ---
        ("T6", "Puri", 2024, datetime(2024, 1, 5), None, None, "raw t6", CAMPAIGN_A),
        ("T7", "Sambalpur", 2024, datetime(2024, 1, 5), None, None, "raw t7 with 9999999999", None),
        # --- hard constraint: index built from grievance_redacted, never
        # complaints.grievance (window 8: Sept 2024) ---
        ("T8", "Sambalpur", 2024, datetime(2024, 9, 5), None, None, "same raw text for t8 and t9", CAMPAIGN_A),
        ("T9", "Sambalpur", 2024, datetime(2024, 9, 6), None, None, "same raw text for t8 and t9", UNRELATED_A),
        ("T10", "Sambalpur", 2024, datetime(2024, 9, 10), None, None, "unique raw text for t10", CAMPAIGN_A),
        ("T11", "Sambalpur", 2024, datetime(2024, 9, 12), None, None, "totally different raw text for t11", CAMPAIGN_A),
        # --- abstention: too short/empty to shingle ---
        ("T12", "Sambalpur", 2024, datetime(2024, 1, 6), None, None, "raw t12", ""),
        # --- cross-script (window 2: Mar 2024) ---
        ("T13", "Sambalpur", 2024, datetime(2024, 3, 1), None, None, "raw t13", ODIA_TEXT),
        ("T14", "Sambalpur", 2024, datetime(2024, 3, 2), None, None, "raw t14", ROMANIZED_TEXT),
        ("T15", "Sambalpur", 2024, datetime(2024, 3, 3), None, None, "raw t15", ODIA_TEXT),
        # --- blocking: identical text to T1's campaign, but window 6 (Jul 2024) ---
        ("T18", "Sambalpur", 2024, datetime(2024, 7, 1), None, None, "raw t18", CAMPAIGN_A),
        # --- same citizen (email), different issue: must NOT group ---
        ("T19", "Sambalpur", 2024, datetime(2024, 1, 6), None, "citizen@example.com", "raw t19", UNRELATED_B),
        ("T20", "Sambalpur", 2024, datetime(2024, 8, 1), None, "Citizen@Example.com ", "raw t20", UNRELATED_C),
        # --- same citizen (email, case/whitespace-normalized) AND matching
        # text, different window: must group ---
        ("T20R", "Sambalpur", 2024, datetime(2024, 10, 1), None, "citizen@example.com", "raw t20r", UNRELATED_B),
    ]
    return _dup_oltp(tmp_path, rows)


class TestBuildsFromRedactedTextOnly:
    """Hard constraint (#71): the index is built from
    grievance_redactions.grievance_redacted, never complaints.grievance.
    Both directions are pinned so this fails if someone points the query at
    the raw column instead."""

    def test_identical_raw_text_with_different_redactions_does_not_group(self, dup_oltp):
        # T8 and T9 share the exact same raw complaints.grievance, but their
        # grievance_redacted text is unrelated. If the indexer read
        # complaints.grievance, these would trivially match (Jaccard 1.0).
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T8"].duplicate_group_id != groups["T9"].duplicate_group_id

    def test_different_raw_text_with_matching_redactions_does_group(self, dup_oltp):
        # T10 and T11 have completely different raw complaints.grievance,
        # but identical grievance_redacted text. Only reading the redacted
        # column groups them.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T10"].duplicate_group_id == groups["T11"].duplicate_group_id

    def test_unredacted_complaint_is_skipped_even_with_pii_in_the_raw_text(self, dup_oltp):
        # T7 has no grievance_redactions row at all (redact_grievance has
        # not run on it yet). It must never be indexed from the raw column.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        assert "T7" not in _signature_rows(sync_url)
        assert "T7" not in _group_rows(sync_url)


class TestCampaignAndTextGrouping:
    def test_near_duplicate_campaign_text_is_grouped(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T1"].duplicate_group_id == groups["T2"].duplicate_group_id
        assert groups["T1"].duplicate_group_id == groups["T3"].duplicate_group_id

    def test_unrelated_text_in_the_same_window_is_not_grouped(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T4"].duplicate_group_id != groups["T1"].duplicate_group_id

    def test_singleton_group_size_is_one(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        row = _group_rows(sync_url)["T4"]
        assert row.duplicate_group_id == "T4"
        assert row.group_size == 1

    def test_campaign_group_size_matches_membership(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        gid = groups["T1"].duplicate_group_id
        members = [t for t, r in groups.items() if r.duplicate_group_id == gid]
        assert groups["T1"].group_size == len(members)


class TestBlockingByTimeWindow:
    def test_identical_text_in_a_different_window_is_not_found(self, dup_oltp):
        # T18 has byte-identical grievance_redacted text to T1's campaign,
        # but falls in a different (district, script, window) block.
        # Blocking means this pair is never even generated as an LSH
        # candidate -- pinned here as documented scope, not a bug.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T18"].duplicate_group_id != groups["T1"].duplicate_group_id

    def test_block_key_encodes_district_script_and_window(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        assert row.block_key == "Sambalpur:latin:0"


class TestBucketVerifiesAllPairsNotJustStarEdges:
    """Every unordered pair within an LSH bucket must be emitted as a
    candidate, not just first-to-other edges (review finding, #135): if the
    first member happens to be an accidental band collision that fails
    Jaccard verification against the other two, star edges would leave two
    genuine near-duplicates elsewhere in the bucket uncompared with each
    other. Exercised directly against `_text_candidate_pairs` (a pure
    function of its `rows` argument) with a hand-built 3-member bucket,
    rather than through real text -- getting three real signatures to
    collide in a specific band deterministically would be fragile."""

    def test_three_member_bucket_yields_all_three_pairs(self):
        # a, b, c all share the same value in band 0 (so all three land in
        # one bucket together) but differ in band 1. A star topology
        # (first-to-other only) would emit (a, b) and (a, c) but never
        # (b, c).
        row_a = SimpleNamespace(ticket_no="a", block_key="X", signature=[1, 1, 100, 101])
        row_b = SimpleNamespace(ticket_no="b", block_key="X", signature=[1, 1, 200, 201])
        row_c = SimpleNamespace(ticket_no="c", block_key="X", signature=[1, 1, 300, 301])

        pairs = _text_candidate_pairs([row_a, row_b, row_c], num_bands=2)

        normalized = {frozenset(p) for p in pairs}
        assert frozenset({"a", "b"}) in normalized
        assert frozenset({"a", "c"}) in normalized
        assert frozenset({"b", "c"}) in normalized
        assert len(normalized) == 3


class TestCrossScriptNonSupport:
    def test_same_script_near_duplicate_is_grouped(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T13"].duplicate_group_id == groups["T15"].duplicate_group_id

    def test_cross_script_is_never_grouped(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T13"].duplicate_group_id != groups["T14"].duplicate_group_id

    def test_script_is_detected_and_stored_per_ticket(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        sigs = _signature_rows(sync_url)
        assert sigs["T13"].script == "odia"
        assert sigs["T15"].script == "odia"
        assert sigs["T14"].script == "latin"

    def test_script_of_helper_matches_module_docstring_claim(self):
        assert _script_of(ODIA_TEXT) == "odia"
        assert _script_of(ROMANIZED_TEXT) == "latin"


class TestIdentityKeyLinking:
    """Identity equality is a candidate source, not duplicate evidence on its
    own (review finding, #135): a repeat filer with two unrelated grievances
    must not be collapsed into one duplicate group, since duplicate-adjusted
    workload is a number people act on."""

    def test_same_mobile_different_issue_does_not_group(self, dup_oltp):
        # T1 and T5 share a mobile number, but T5's redacted text
        # (UNRELATED_B) has nothing to do with T1's campaign text -- same
        # citizen, different issue. Identity alone must not merge them.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T1"].duplicate_group_id != groups["T5"].duplicate_group_id

    def test_same_email_different_issue_does_not_group(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T19"].duplicate_group_id != groups["T20"].duplicate_group_id

    def test_same_mobile_and_matching_text_across_windows_does_group(self, dup_oltp):
        # T1 and T5R share a mobile number AND near-identical text, but sit
        # in different time windows -- pure text-LSH blocking would never
        # generate this pair as a candidate (different block_key). Only the
        # identity-key path (unblocked by window) surfaces it as a
        # candidate, and it clears jaccard_similarity like any other
        # candidate, so it unions.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T1"].duplicate_group_id == groups["T5R"].duplicate_group_id

    def test_same_email_and_matching_text_across_windows_does_group(self, dup_oltp):
        # T19 and T20R: same normalized email, identical redacted text
        # (UNRELATED_B for both), different windows.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T19"].duplicate_group_id == groups["T20R"].duplicate_group_id

    def test_identity_keys_are_salted_and_not_derived_from_redacted_text(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        sigs = _signature_rows(sync_url)
        # T1 and T5 have the same mobile but completely different redacted
        # text -- the stored identity key must agree with a direct call to
        # identity_key() on the raw mobile, independent of the text path
        # (and independent of whether the pair ends up in the same
        # duplicate group).
        expected = identity_key("9861234567", _SALT)
        assert sigs["T1"].identity_key_mobile == expected
        assert sigs["T5"].identity_key_mobile == expected
        # a different salt would change it -- proves it is not a
        # placeholder/text-derived value
        assert sigs["T1"].identity_key_mobile != identity_key("9861234567", "a-different-salt")

    def test_same_citizen_different_issue_stays_queryable_via_identity_key(self, dup_oltp):
        # The relationship is not discarded just because it does not meet
        # the duplicate bar: T1 and T5 fail to group, but both still carry
        # the same identity_key_mobile, so "same citizen, different issue"
        # remains directly queryable (e.g. for a future spike-decomposition
        # consumer) by joining on that column.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        sigs = _signature_rows(sync_url)
        groups = _group_rows(sync_url)
        assert sigs["T1"].identity_key_mobile == sigs["T5"].identity_key_mobile
        assert groups["T1"].duplicate_group_id != groups["T5"].duplicate_group_id


class TestAbstention:
    def test_too_short_to_shingle_still_gets_a_signature_row(self, dup_oltp):
        # T12's redacted text is "" -- minhash_signature() abstains (None),
        # but the row must still be written so the pending predicate
        # advances past it rather than retrying forever.
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T12"]
        assert row.num_shingles == 0
        assert row.signature is None

    def test_abstained_ticket_is_its_own_singleton_group(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        row = _group_rows(sync_url)["T12"]
        assert row.duplicate_group_id == "T12"
        assert row.group_size == 1


class TestReconciliation:
    """#71 wants row counts reconciled against the slice definition."""

    def test_every_signature_gets_exactly_one_group_row(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        assert set(_signature_rows(sync_url)) == set(_group_rows(sync_url))

    def test_reported_counts_are_internally_consistent(self, dup_oltp):
        async_url, sync_url = dup_oltp
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        assert counts["total"] == counts["processed"] + counts["already_indexed"]
        assert counts["slice_signatures"] == counts["total"]
        assert counts["slice_signatures"] == len(_signature_rows(sync_url))
        assert counts["groups"] == len({r.duplicate_group_id for r in _group_rows(sync_url).values()})

    def test_puri_decoy_never_enters_the_sambalpur_slice(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        assert "T6" not in _signature_rows(sync_url)


class TestSchemaGuards:
    def test_dedup_tables_do_not_reach_the_lake(self):
        """Redaction lowers exposure; it does not declassify what is
        derived from citizen prose (ROADMAP §3.2). These stay dpic-infra,
        deliberately not in LAKE_TABLES -- see materialize.py's comment."""
        from janasunani.olap.materialize import LAKE_TABLES

        assert "dedup_signatures" not in LAKE_TABLES
        assert "dedup_groups" not in LAKE_TABLES

    def test_signature_primary_key_is_the_ticket(self):
        assert [c.name for c in DedupSignature.__table__.primary_key] == ["ticket_no"]

    def test_group_primary_key_is_the_ticket(self):
        assert [c.name for c in DedupGroup.__table__.primary_key] == ["ticket_no"]

    def test_alembic_has_exactly_one_head(self):
        """Two heads is a broken migration chain."""
        from pathlib import Path

        from alembic.script import ScriptDirectory

        script_location = (
            Path(__file__).resolve().parents[1] / "janasunani" / "db" / "alembic"
        )
        script_dir = ScriptDirectory(str(script_location))
        assert len(script_dir.get_heads()) == 1


class TestRefreshStaleSignatures:
    """#136. Without this the index_version stamp is write-only. It matters most
    after a salt rotation: you rotate *because* the old salt is suspect, and a
    rotation that leaves every stored identity hash in place has not rotated
    anything for the rows that matter."""

    def _restamp(self, sync_url: str, version: str) -> None:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE dedup_signatures SET index_version = :v"), {"v": version}
            )
        engine.dispose()

    def test_the_stamp_records_which_salt_was_used(self, dup_oltp):
        """Not the salt itself: a column holding the secret beside the hashes it
        protects defeats the point."""
        from janasunani.pipeline.dedup_index import _index_version

        one = _index_version(30, 0.5, "salt-one")
        two = _index_version(30, 0.5, "salt-two")
        assert one != two
        assert "salt-one" not in one and "salt-two" not in two

    def test_same_salt_gives_the_same_stamp(self):
        from janasunani.pipeline.dedup_index import _index_version

        assert _index_version(30, 0.5, "s") == _index_version(30, 0.5, "s")

    def test_stale_signatures_are_counted_but_not_rebuilt_by_default(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        self._restamp(sync_url, "an-older-parameter-set")

        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        assert counts["stale_at_start"] > 0
        assert counts["processed"] == 0

    def test_refresh_stale_rebuilds_them(self, dup_oltp):
        async_url, sync_url = dup_oltp
        first = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        self._restamp(sync_url, "an-older-parameter-set")

        counts = build_dedup_index(
            "Sambalpur", 2024, oltp_url=async_url, salt="s", refresh_stale=True
        )
        assert counts["processed"] == first["processed"]

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            versions = (
                conn.execute(select(DedupSignature.index_version).distinct())
                .scalars()
                .all()
            )
        engine.dispose()
        assert versions == [_index_version_for_test()]

    def test_a_salt_rotation_makes_every_row_stale(self, dup_oltp):
        """The case the issue is about: rotate the salt, and every stored
        identity hash is from the compromised one."""
        async_url, _ = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="old-salt")
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="new-salt")
        assert counts["stale_at_start"] == counts["already_indexed"]


def _index_version_for_test() -> str:
    from janasunani.pipeline.dedup_index import _index_version

    return _index_version(30, 0.5, "s")


class TestGroupingStreamsBucketsInsteadOfMaterialisingPairs:
    """The Sambalpur run OOM-killed twice at 7.4 GB on an 8 GB box, after all
    55,544 signatures were written. The allocation was the candidate-pair set,
    not the text: a bucket is quadratic in its membership and this slice has a
    9,405-row block, so one campaign bucket alone is tens of millions of pairs.
    Grievance subjects are a couple of hundred characters -- every text in the
    slice together is only megabytes."""

    def test_grouping_no_longer_builds_a_global_pair_set(self):
        """The regression guard. If a future change collects every candidate
        pair before verifying, this is what should stop it."""
        import inspect
        import re

        import janasunani.pipeline.dedup_index as di

        source = inspect.getsource(di._group_duplicates)
        assert "_candidate_buckets" in source
        # Assignment, not any mention: a comment referring to the old helpers
        # is fine, a variable holding every pair is the thing being guarded.
        assert not re.search(r"^\s*candidate_pairs\s*=", source, re.M)

    def test_singleton_buckets_are_dropped(self):
        """No pairs in them, and they would each cost a database round trip."""
        import janasunani.pipeline.dedup_index as di

        rows = [
            SimpleNamespace(
                ticket_no=t,
                signature=None,
                block_key="b",
                identity_key_mobile=k,
                identity_key_email=None,
            )
            for t, k in (("T1", "shared"), ("T2", "shared"), ("T3", "alone"))
        ]
        buckets = di._candidate_buckets(rows, 4)
        assert [sorted(b) for b in buckets] == [["T1", "T2"]]

    def test_groups_still_form_over_the_real_fixture(self, dup_oltp):
        """Streaming must not change the answer, only the memory profile."""
        async_url, _ = dup_oltp
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        assert counts["groups"] > 0
        assert counts["slice_signatures"] > 0

