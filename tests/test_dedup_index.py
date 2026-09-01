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
from janasunani.pipeline.dedup import (
    DEDUP_SOURCE_NAME,
    DedupSourceSnapshotMismatch,
    assert_group_source_snapshot,
    identity_key,
    minhash_signature,
    shingles,
    source_snapshot_id,
)
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


class TestGroupSourceSnapshotProvenance:
    """#137. Groups stay in OLTP for the short redaction -> index chain, but
    #72-style lake analytics must be able to prove their source slice matches
    before treating a group id as a denominator."""

    @staticmethod
    def _source_records():
        return [
            {
                "ticket_no": ticket_no,
                "district": district,
                "created_year": year,
                "created_on": created_on,
                "petitioner_mobile": mobile,
                "petitioner_email": None,
                "grievance_redacted": redacted,
            }
            for ticket_no, district, year, created_on, mobile, redacted in _SMALL_ROWS
            if district == "Khordha" and year == 2024 and redacted is not None
        ]

    @staticmethod
    def _group_provenance_rows(groups):
        return [
            {
                "ticket_no": row.ticket_no,
                "source_name": row.source_name,
                "source_snapshot_id": row.source_snapshot_id,
                "grouping_scope_snapshot_id": row.grouping_scope_snapshot_id,
            }
            for row in groups.values()
        ]

    def test_reordered_complete_group_set_validates(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)

        source_records = self._source_records()
        expected = source_snapshot_id(source_records)
        groups = _group_rows(sync_url)
        assert {row.source_name for row in groups.values()} == {DEDUP_SOURCE_NAME}
        assert {row.source_snapshot_id for row in groups.values()} == {expected}

        # This is the direct downstream guard: source-row ordering is irrelevant,
        # while a different/incomplete lake source would fail before aggregation.
        assert (
            assert_group_source_snapshot(
                reversed(self._group_provenance_rows(groups)),
                reversed(source_records),
            )
            == expected
        )

    def test_missing_group_row_fails_with_counts_only(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        group_rows = self._group_provenance_rows(_group_rows(sync_url))

        with pytest.raises(
            DedupSourceSnapshotMismatch,
            match="missing_group_rows=1, extra_group_rows=0",
        ):
            assert_group_source_snapshot(group_rows[:-1], self._source_records())

    def test_extra_group_row_fails_with_counts_only(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        group_rows = self._group_provenance_rows(_group_rows(sync_url))
        group_rows.append(
            {
                "ticket_no": "synthetic-extra-ticket",
                "source_name": group_rows[0]["source_name"],
                "source_snapshot_id": group_rows[0]["source_snapshot_id"],
                "grouping_scope_snapshot_id": group_rows[0]["grouping_scope_snapshot_id"],
            }
        )

        with pytest.raises(
            DedupSourceSnapshotMismatch,
            match="missing_group_rows=0, extra_group_rows=1",
        ):
            assert_group_source_snapshot(group_rows, self._source_records())

    def test_duplicate_group_row_fails_with_count_only(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        group_rows = self._group_provenance_rows(_group_rows(sync_url))
        group_rows.append(dict(group_rows[0]))

        with pytest.raises(DedupSourceSnapshotMismatch, match="duplicates=1"):
            assert_group_source_snapshot(group_rows, self._source_records())

    def test_blank_group_ticket_fails_with_count_only(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        group_rows = self._group_provenance_rows(_group_rows(sync_url))
        group_rows[0]["ticket_no"] = " "

        with pytest.raises(
            DedupSourceSnapshotMismatch,
            match="blank_or_non_string=1",
        ):
            assert_group_source_snapshot(group_rows, self._source_records())

    def test_changed_oltp_source_fails_until_refresh_then_recertifies(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        before = _group_rows(sync_url)["T1"].source_snapshot_id

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :text "
                    "WHERE ticket_no = 'T1'"
                ),
                {"text": "water supply now broken for eight months"},
            )
        engine.dispose()

        # Candidate generation would use the old signature while Jaccard
        # verification would read the new redacted text. Do not write a newly
        # certified group from those mixed inputs.
        with pytest.raises(ValueError, match="old candidates with current redacted text"):
            build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        assert groups["T1"].source_snapshot_id == before
        changed_source = self._source_records()
        changed_source[0]["grievance_redacted"] = "water supply now broken for eight months"
        with pytest.raises(DedupSourceSnapshotMismatch, match="does not match"):
            assert_group_source_snapshot(
                self._group_provenance_rows(groups),
                changed_source,
            )

        refreshed = build_dedup_index(
            "Khordha",
            2024,
            oltp_url=async_url,
            salt=_SALT,
            refresh_stale=True,
        )
        groups = _group_rows(sync_url)
        assert refreshed["source_mismatches_at_start"] == 1
        assert refreshed["total"] == 3
        assert refreshed["already_indexed"] == 2
        assert refreshed["processed"] == 1
        assert refreshed["already_indexed"] + refreshed["processed"] == 3
        assert groups["T1"].source_snapshot_id != before
        assert (
            assert_group_source_snapshot(
                self._group_provenance_rows(groups),
                changed_source,
            )
            == groups["T1"].source_snapshot_id
        )

    def test_legacy_signature_without_a_source_digest_is_rebuilt(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE dedup_signatures SET source_record_digest = NULL"))
        engine.dispose()

        counts = build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert counts["processed"] == counts["total"]
        assert all(row.source_record_digest for row in _signature_rows(sync_url).values())

    def test_source_membership_change_fails_closed_even_with_refresh(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        before = _group_rows(sync_url)["T1"].source_snapshot_id
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE complaints SET created_year = 2023 WHERE ticket_no = 'T1'")
            )
        engine.dispose()

        with pytest.raises(ValueError, match="moved outside this district-year"):
            build_dedup_index(
                "Khordha",
                2024,
                oltp_url=async_url,
                salt=_SALT,
                refresh_stale=True,
            )
        assert _group_rows(sync_url)["T1"].source_snapshot_id == before

    def test_missing_current_source_row_fails_closed(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM grievance_redactions WHERE ticket_no = 'T1'"))
        engine.dispose()

        with pytest.raises(ValueError, match="no longer have a current complaint/redaction"):
            build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)

    def test_source_refresh_rejects_limit_instead_of_exceeding_it(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :text "
                    "WHERE ticket_no = 'T1'"
                ),
                {"text": "water supply now broken for eight months"},
            )
        engine.dispose()

        with pytest.raises(ValueError, match="--limit cannot be combined"):
            build_dedup_index(
                "Khordha",
                2024,
                oltp_url=async_url,
                salt=_SALT,
                refresh_stale=True,
                limit=1,
            )


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

    def test_signature_version_format_does_not_include_grouping_policy(self, oltp):
        from janasunani.pipeline.dedup_index import _index_version

        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        version = _signature_rows(sync_url)["T1"].index_version

        assert version == _index_version(30, 0.5, _SALT)
        assert "grouping_algorithm=" not in version


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
        # --- campaign block (all within one 30-day window: Jan 2024) ---
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
        # complaints.grievance (a different window: Sept 2024) ---
        ("T8", "Sambalpur", 2024, datetime(2024, 9, 5), None, None, "same raw text for t8 and t9", CAMPAIGN_A),
        ("T9", "Sambalpur", 2024, datetime(2024, 9, 6), None, None, "same raw text for t8 and t9", UNRELATED_A),
        ("T10", "Sambalpur", 2024, datetime(2024, 9, 10), None, None, "unique raw text for t10", CAMPAIGN_A),
        ("T11", "Sambalpur", 2024, datetime(2024, 9, 12), None, None, "totally different raw text for t11", CAMPAIGN_A),
        # --- abstention: too short/empty to shingle ---
        ("T12", "Sambalpur", 2024, datetime(2024, 1, 6), None, None, "raw t12", ""),
        # --- cross-script (a different window: Mar 2024) ---
        ("T13", "Sambalpur", 2024, datetime(2024, 3, 1), None, None, "raw t13", ODIA_TEXT),
        ("T14", "Sambalpur", 2024, datetime(2024, 3, 2), None, None, "raw t14", ROMANIZED_TEXT),
        ("T15", "Sambalpur", 2024, datetime(2024, 3, 3), None, None, "raw t15", ODIA_TEXT),
        # --- blocking: identical text to T1's campaign, different window (Jul 2024) ---
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
        from janasunani.pipeline.dedup_index import DEDUP_WINDOW_EPOCH

        async_url, sync_url = dup_oltp
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        row = _signature_rows(sync_url)["T1"]
        # The window index counts from the fixed epoch, not from Jan 1 of the
        # slice year, so it is not 0 for the first complaint of a year. That
        # is the point: the bucket is a property of the record, so the same
        # complaint keeps it under any run scope.
        expected_window = (datetime(2024, 1, 5).date() - DEDUP_WINDOW_EPOCH).days // 30
        assert row.block_key == f"Sambalpur:latin:{expected_window}"


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


class TestBucketVerificationPolicy:
    """#158 keeps #101's exhaustive small-bucket contract, but makes the
    deliberate large-campaign recall trade bounded and visible."""

    def test_small_bucket_scores_every_unordered_pair(self, monkeypatch):
        import janasunani.pipeline.dedup_index as di

        compared = []
        monkeypatch.setattr(di, "shingles", lambda text: {text})
        monkeypatch.setattr(
            di,
            "jaccard_similarity",
            lambda left, right: compared.append(tuple(sorted((*left, *right)))) or 0.0,
        )

        matches, comparisons, used_large_policy = di._verify_bucket(
            ["a", "b", "c", "d"],
            {ticket: ticket for ticket in "abcd"},
            {},
            {},
            threshold=0.5,
            cap=5,
        )

        assert matches == 0
        assert comparisons == 6
        assert used_large_policy is False
        assert len(compared) == 6

    def test_large_bucket_has_a_linear_deterministic_comparison_bound(self, monkeypatch):
        import janasunani.pipeline.dedup_index as di

        monkeypatch.setattr(di, "shingles", lambda text: {text})
        calls = []
        monkeypatch.setattr(
            di,
            "jaccard_similarity",
            lambda left, right: calls.append((left, right)) or 0.0,
        )
        members = [f"T{i:04d}" for i in range(1_000)]
        anchor_count = 7

        matches, comparisons, used_large_policy = di._verify_bucket(
            members,
            {ticket: ticket for ticket in members},
            {},
            {},
            threshold=0.5,
            cap=200,
            anchor_count=anchor_count,
        )

        assert matches == 0
        assert used_large_policy is True
        expected = (
            anchor_count * (anchor_count - 1) // 2
            + anchor_count * (len(members) - anchor_count)
        )
        assert comparisons == expected
        assert len(calls) == comparisons
        assert comparisons < len(members) * (len(members) - 1) // 100

    def test_large_bucket_scores_and_unions_anchor_pairs(self, monkeypatch):
        import janasunani.pipeline.dedup_index as di

        monkeypatch.setattr(di, "shingles", lambda text: {text})
        compared = []

        def similarity(left, right):
            pair = frozenset((*left, *right))
            compared.append(pair)
            return 1.0 if pair == frozenset({"a", "b"}) else 0.0

        monkeypatch.setattr(di, "jaccard_similarity", similarity)
        parent = {}
        matches, comparisons, used_large_policy = di._verify_bucket(
            ["a", "b", "c", "d"],
            {ticket: ticket for ticket in "abcd"},
            {},
            parent,
            threshold=0.5,
            cap=4,
            anchor_count=2,
        )

        assert used_large_policy is True
        assert comparisons == 5  # C(2, 2) + 2 * (4 - 2)
        assert compared.count(frozenset({"a", "b"})) == 1
        assert matches == 1
        assert di._find(parent, "a") == di._find(parent, "b")

    def test_overlapping_bands_in_a_block_fetch_each_candidate_text_once(
        self, dup_oltp, monkeypatch
    ):
        """#158: a ticket sitting in several overlapping band buckets is
        fetched once, not once per bucket.

        #336 rescoped that guarantee from the run to the block. One fetch for
        every candidate ticket in the scope is what did not fit corpus-wide,
        so the promise is now "once per block, plus once per identity batch".
        On this fixture that is three block fetches -- the January window, the
        September window, and the Odia-script window all produce buckets --
        and one identity batch.
        """
        import janasunani.pipeline.dedup_index as di

        original = di._load_redacted_text
        fetches = []

        async def tracked_load(conn, ticket_nos):
            fetches.append(tuple(ticket_nos))
            return await original(conn, ticket_nos)

        monkeypatch.setattr(di, "_load_redacted_text", tracked_load)
        async_url, _ = dup_oltp
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)

        assert len(fetches) == 4
        # The #158 property itself: T1, T2 and T3 share the January block and
        # collide on more than one band. One fetch has to cover all three.
        assert any({"T1", "T2", "T3"} <= set(fetch) for fetch in fetches)
        for fetch in fetches:
            assert fetch == tuple(sorted(set(fetch)))
        assert counts["comparison_pairs"] >= counts["verified_pairs"]
        assert counts["large_buckets"] == 0


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


class TestGroupVersionProvenance:
    def test_group_version_records_effective_bounded_policy(self, dup_oltp):
        from janasunani.pipeline.dedup_index import GROUPING_ALGORITHM, _index_version

        async_url, sync_url = dup_oltp
        build_dedup_index(
            "Sambalpur",
            2024,
            oltp_url=async_url,
            salt=_SALT,
            representative_cap=7,
            anchor_count=3,
        )

        expected = _index_version(
            30,
            0.5,
            _SALT,
            grouping_algorithm=GROUPING_ALGORITHM,
            representative_cap=7,
            anchor_count=3,
        )
        assert {row.index_version for row in _group_rows(sync_url).values()} == {expected}
        assert expected.endswith(
            "grouping_algorithm=fixed-anchor-v1 representative_cap=7 anchor_count=3"
        )

    def test_policy_change_restamps_groups_but_not_signatures(self, dup_oltp):
        async_url, sync_url = dup_oltp
        build_dedup_index(
            "Sambalpur",
            2024,
            oltp_url=async_url,
            salt=_SALT,
            representative_cap=7,
            anchor_count=3,
        )
        signature_before = _signature_rows(sync_url)["T1"].index_version
        group_before = _group_rows(sync_url)["T1"].index_version

        build_dedup_index(
            "Sambalpur",
            2024,
            oltp_url=async_url,
            salt=_SALT,
            representative_cap=8,
            anchor_count=4,
        )

        assert _signature_rows(sync_url)["T1"].index_version == signature_before
        assert _group_rows(sync_url)["T1"].index_version != group_before


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
        assert counts["already_indexed"] == 0
        assert counts["processed"] + counts["already_indexed"] == counts["total"]

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
        assert "_band_buckets" in source
        assert "_identity_buckets" in source
        # Assignment, not any mention: a comment referring to the old helpers
        # is fine, a variable holding every pair is the thing being guarded.
        assert not re.search(r"^\s*candidate_pairs\s*=", source, re.M)
        # #336: production must not call the whole-scope helper. It builds
        # every bucket in the scope at once, which is the thing being avoided.
        assert not re.search(r"_candidate_buckets\s*\(", source)

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
        assert [sorted(members) for _block_key, members in buckets] == [["T1", "T2"]]

    def test_band_buckets_carry_their_block_key_identity_buckets_do_not(self):
        """#158: `_group_duplicates` caches redacted text per block, reusing
        it across every band bucket in that block -- it needs the block key
        on each band bucket to know which cache to use. Identity buckets are
        unblocked by construction (module docstring), so there is no single
        block to key a cache on; they come back with `None` instead."""
        import janasunani.pipeline.dedup_index as di

        row_a = SimpleNamespace(
            ticket_no="a",
            block_key="Khordha:latin:0",
            signature=[1, 1, 100, 101],
            identity_key_mobile=None,
            identity_key_email=None,
        )
        row_b = SimpleNamespace(
            ticket_no="b",
            block_key="Khordha:latin:0",
            signature=[1, 1, 200, 201],
            identity_key_mobile="shared-identity",
            identity_key_email=None,
        )
        row_c = SimpleNamespace(
            ticket_no="c",
            block_key="Khordha:latin:5",
            signature=[9, 9, 9, 9],
            identity_key_mobile="shared-identity",
            identity_key_email=None,
        )
        buckets = di._candidate_buckets([row_a, row_b, row_c], 2)

        band = [(k, sorted(m)) for k, m in buckets if k is not None]
        identity = [(k, sorted(m)) for k, m in buckets if k is None]
        assert band == [("Khordha:latin:0", ["a", "b"])]
        assert identity == [(None, ["b", "c"])]

    def test_groups_still_form_over_the_real_fixture(self, dup_oltp):
        """Streaming must not change the answer, only the memory profile."""
        async_url, _ = dup_oltp
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        assert counts["groups"] > 0
        assert counts["slice_signatures"] > 0


class TestCLIReporting:
    def test_main_logs_comparison_and_large_bucket_counts(self, oltp, monkeypatch):
        """Exercise argparse -> build -> final Loguru message, not just the
        build helper's returned dictionary."""
        from loguru import logger as loguru_logger

        import janasunani.pipeline.dedup_index as di

        async_url, _ = oltp
        monkeypatch.setattr(
            "sys.argv",
            [
                "janasunani-dedup-index",
                "--district",
                "Khordha",
                "--year",
                "2024",
                "--oltp-url",
                async_url,
                "--salt",
                _SALT,
            ],
        )
        messages = []
        sink = loguru_logger.add(
            lambda message: messages.append(str(message)), level="INFO", format="{message}"
        )
        try:
            di.main()
        finally:
            loguru_logger.remove(sink)

        final = next(message for message in messages if message.startswith("done:"))
        assert "comparison_pairs=0" in final
        assert "large_buckets=0" in final

    def test_main_reports_one_refresh_as_three_of_three_not_four(self, oltp, monkeypatch):
        from loguru import logger as loguru_logger

        import janasunani.pipeline.dedup_index as di

        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :text "
                    "WHERE ticket_no = 'T1'"
                ),
                {"text": "water supply now broken for eight months"},
            )
        engine.dispose()

        monkeypatch.setattr(
            "sys.argv",
            [
                "janasunani-dedup-index",
                "--district",
                "Khordha",
                "--year",
                "2024",
                "--oltp-url",
                async_url,
                "--salt",
                _SALT,
                "--refresh-stale",
            ],
        )
        messages = []
        sink = loguru_logger.add(
            lambda message: messages.append(str(message)), level="INFO", format="{message}"
        )
        try:
            di.main()
        finally:
            loguru_logger.remove(sink)

        final = next(message for message in messages if message.startswith("done:"))
        assert final.startswith("done: 1 processed this run, 3 of 3 indexed")


class TestPinnedThresholdAndBands:
    """Guard the pinned LSH/band params — do not change to 0.8/20 without
    updating every test that depends on the candidate probability curve."""

    def test_threshold_and_bands_are_pinned(self):
        from janasunani.pipeline.dedup import DEFAULT_NUM_BANDS
        from janasunani.pipeline.dedup_index import DEFAULT_DUPLICATE_THRESHOLD

        # 0.5/16 is the curve that gives a lightly-reworded duplicate
        # (Jaccard ~0.78) ~90% candidate probability while unrelated
        # boilerplate at 0.3 stays <0.1% — see dedup.py's band comment.
        # 0.8/20 would need 20 bands over 128 hashes (128 % 20 != 0) and a
        # stricter 0.8 Jaccard check that would miss those same reworded
        # fixtures (see tests/test_dedup.py CAMPAIGN_C_REWORDED).
        assert DEFAULT_DUPLICATE_THRESHOLD == 0.5
        assert DEFAULT_NUM_BANDS == 16

    def test_slice_shorthand_parses_sambalpur_2024(self):
        from janasunani.pipeline.dedup_index import _parse_slice

        assert _parse_slice("Sambalpur/2024") == ("Sambalpur", 2024)
        assert _parse_slice("  Sambalpur / 2024 ") == ("Sambalpur", 2024)
        with pytest.raises(ValueError, match="District/YYYY"):
            _parse_slice("Sambalpur-2024")
        with pytest.raises(ValueError, match="District/YYYY"):
            _parse_slice("Sambalpur/")
        with pytest.raises(ValueError, match="integer"):
            _parse_slice("Sambalpur/year")


class TestHeldOutRecall:
    """Held-out recall vs officer-confirmed duplicates (#72 baseline).

    The 34k officer baseline (``duplicate_officer_confirmed``) is two
    families: ``case already taken up`` + ``duplicate copy``. The dedup
    capability claim is the increment beyond them. This test measures both
    on synthetic officer labels that mimic that baseline, so the harness
    is runnable in CI without the 55k slice.
    """

    def _build_synthetic_slice(self, tmp_path):
        # Two officer-confirmed duplicate groups (campaign text) plus
        # two singletons that are unrelated. The dedup index should
        # recover the officer pairs (recall) and also surface any
        # additional dedup pairs as incremental.
        rows = [
            # officer group 1: three near-identical campaign filings (window 0)
            ("O1", "Sambalpur", 2024, datetime(2024, 1, 5), None, None, "raw o1", CAMPAIGN_A),
            ("O2", "Sambalpur", 2024, datetime(2024, 1, 6), None, None, "raw o2", CAMPAIGN_A),
            ("O3", "Sambalpur", 2024, datetime(2024, 1, 7), None, None, "raw o3", CAMPAIGN_C_REWORDED),
            # officer group 2: two more campaign filings (window 0)
            ("O4", "Sambalpur", 2024, datetime(2024, 1, 8), None, None, "raw o4", CAMPAIGN_A),
            ("O5", "Sambalpur", 2024, datetime(2024, 1, 9), None, None, "raw o5", CAMPAIGN_A),
            # singletons — not officer duplicates, but one will be a dedup
            # incremental (same text as group 1, not labelled by officers)
            ("I1", "Sambalpur", 2024, datetime(2024, 1, 10), None, None, "raw i1", CAMPAIGN_A),
            ("S1", "Sambalpur", 2024, datetime(2024, 1, 11), None, None, "raw s1", UNRELATED_A),
            ("S2", "Sambalpur", 2024, datetime(2024, 1, 12), None, None, "raw s2", UNRELATED_B),
        ]
        return _dup_oltp(tmp_path, rows)

    def test_recall_and_incremental_are_reported(self, tmp_path):
        from janasunani.pipeline.dedup_index import evaluate_held_out_recall

        async_url, sync_url = self._build_synthetic_slice(tmp_path)
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups = {r.ticket_no: r.duplicate_group_id for r in _group_rows(sync_url).values()}
        # Officer labels: two groups as defined above.
        officer_groups = [{"O1", "O2", "O3"}, {"O4", "O5"}]
        officer_pairs = {frozenset(p) for g in officer_groups for p in __import__("itertools").combinations(sorted(g), 2)}
        report = evaluate_held_out_recall(officer_pairs, groups)
        # All officer pairs share campaign text in same window/script, so
        # MinHash+block+verification should find them.
        assert report["officer_pairs"] == 4  # C(3,2)=3 + C(2,2)=1
        assert report["recall"] == 1.0
        assert report["true_positives"] == 4
        # I1 is same text as group 1, same window, but not in officer set —
        # it joins group 1, creating incremental pairs (I1 with each of O1..O3).
        assert report["incremental_pairs"] >= 3
        assert report["incremental_groups"] >= 1
        # Sanity: dedup pairs = officer pairs + incremental - any overlap
        assert report["dedup_pairs"] == report["true_positives"] + report["incremental_pairs"]

    def test_empty_officer_set_has_recall_one(self):
        from janasunani.pipeline.dedup_index import evaluate_held_out_recall

        report = evaluate_held_out_recall(set(), {"A": "A", "B": "B"})
        assert report["recall"] == 1.0
        assert report["officer_pairs"] == 0

    def test_mixed_snapshot_guard_fails_before_recall(self, oltp):
        # Even a correct recall harness must assert provenance before
        # treating a dedup group as a denominator (#137).
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        groups = _group_rows(sync_url)
        source_records = [
            {
                "ticket_no": ticket_no,
                "district": district,
                "created_year": year,
                "created_on": created_on,
                "petitioner_mobile": mobile,
                "petitioner_email": None,
                "grievance_redacted": redacted,
            }
            for ticket_no, district, year, created_on, mobile, redacted in _SMALL_ROWS
            if district == "Khordha" and year == 2024 and redacted is not None
        ]
        # Baseline passes.
        assert assert_group_source_snapshot(
            [
                {
                "ticket_no": r.ticket_no,
                "source_name": r.source_name,
                "source_snapshot_id": r.source_snapshot_id,
                "grouping_scope_snapshot_id": r.grouping_scope_snapshot_id,
            }
                for r in groups.values()
            ],
            source_records,
        )
        # Tamper one group's snapshot — downstream join must fail loudly,
        # not silently mix.
        tampered = [
            {
                "ticket_no": r.ticket_no,
                "source_name": r.source_name,
                "source_snapshot_id": "sha256:deadbeef",
                "grouping_scope_snapshot_id": r.grouping_scope_snapshot_id,
            }
            for r in groups.values()
        ]
        with pytest.raises(DedupSourceSnapshotMismatch):
            assert_group_source_snapshot(tampered, source_records)

    def test_cli_slice_shorthand_builds_same_index(self, tmp_path):
        # --slice Sambalpur/2024 must be equivalent to --district/--year.
        rows = [
            ("T1", "Sambalpur", 2024, datetime(2024, 1, 5), None, None, "raw t1", CAMPAIGN_A),
            ("T2", "Sambalpur", 2024, datetime(2024, 1, 6), None, None, "raw t2", CAMPAIGN_A),
        ]
        async_url, sync_url = _dup_oltp(tmp_path, rows)
        import janasunani.pipeline.dedup_index as di

        # Build via explicit district/year first.
        build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt=_SALT)
        groups_via_args = {r.ticket_no: r.duplicate_group_id for r in _group_rows(sync_url).values()}
        # Clear and rebuild via --slice CLI path.
        from sqlalchemy import create_engine as _ce

        eng = _ce(sync_url)
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM dedup_groups"))
            conn.execute(text("DELETE FROM dedup_signatures"))
        eng.dispose()
        import sys

        old_argv = sys.argv
        sys.argv = ["janasunani-dedup-index", "--slice", "Sambalpur/2024", "--oltp-url", async_url, "--salt", _SALT]
        try:
            di.main()
        finally:
            sys.argv = old_argv
        groups_via_slice = {r.ticket_no: r.duplicate_group_id for r in _group_rows(sync_url).values()}
        assert groups_via_args == groups_via_slice


# --- #317: corpus-wide scope ----------------------------------------------

# A citizen refiling the same complaint in a later year, from the same
# mobile. Both halves are needed: identity alone is "same citizen, not same
# issue" and never unions on its own, so the text has to clear the Jaccard
# threshold too.
_REFILED_2023 = (
    "the community water pump near the panchayat office has been broken "
    "for months and no one has come to repair it despite several visits"
)
_REFILED_2024 = (
    "the community water pump near the panchayat office has been broken "
    "for months and nobody has come to repair it despite several visits"
)

_CROSS_SCOPE_ROWS = [
    # ticket, district,   year, created_on,           mobile,        redacted
    ("X1", "Khordha", 2023, datetime(2023, 3, 4), "9861111111", _REFILED_2023),
    ("X2", "Khordha", 2024, datetime(2024, 7, 9), "9861111111", _REFILED_2024),
    ("X3", "Khordha", 2024, datetime(2024, 7, 9), None, UNRELATED_A),
]


@pytest.fixture
def cross_scope_oltp(tmp_path):
    complaints = [
        {
            "ticket_no": t,
            "district": d,
            "created_year": y,
            "created_on": c,
            "petitioner_mobile": m,
            "grievance": f"raw grievance for {t}",
        }
        for t, d, y, c, m, _ in _CROSS_SCOPE_ROWS
    ]
    redactions = [
        {"ticket_no": t, "grievance_redacted": r} for t, _, _, _, _, r in _CROSS_SCOPE_ROWS
    ]
    return _make_oltp(tmp_path, complaints, redactions)


class TestCorpusWideScope:
    """`district`/`year` are independently optional; None means unbounded."""

    def test_corpus_wide_indexes_every_district_and_year(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)
        # T6 stays out: it has no redaction row, which is orthogonal to scope.
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3", "T4", "T5"}

    def test_district_without_year_spans_years(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", None, oltp_url=async_url, salt=_SALT)
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3", "T4"}

    def test_year_without_district_spans_districts(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index(None, 2024, oltp_url=async_url, salt=_SALT)
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3", "T5"}


class TestCrossSliceIdentityDuplicates:
    """The reason corpus-wide exists, and the thing a per-slice loop cannot do.

    Text candidates are blocked by `district:script:window_index`, so a
    per-slice run finds every text duplicate a corpus run would. Identity
    candidates are deliberately unblocked -- but `build_dedup_index` scopes
    its rows before bucketing, so only a run whose scope contains *both*
    sides of a same-citizen pair can ever see it.
    """

    def test_per_year_runs_never_group_the_refiling(self, cross_scope_oltp):
        async_url, sync_url = cross_scope_oltp
        build_dedup_index("Khordha", 2023, oltp_url=async_url, salt=_SALT)
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)

        groups = _group_rows(sync_url)
        assert set(groups) == {"X1", "X2", "X3"}
        # Each slice saw one half of the pair, so nothing could union.
        assert groups["X1"].duplicate_group_id != groups["X2"].duplicate_group_id

    def test_corpus_wide_groups_the_refiling(self, cross_scope_oltp):
        async_url, sync_url = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        groups = _group_rows(sync_url)
        assert groups["X1"].duplicate_group_id == groups["X2"].duplicate_group_id
        # An unrelated filing in the same district-year stays separate: the
        # identity path is not unioning everything it touches.
        assert groups["X3"].duplicate_group_id != groups["X1"].duplicate_group_id


class TestWindowEpochIsAbsolute:
    """A record's time window must be a property of the record, not of the
    run that indexed it.

    The epoch used to be `date(year, 1, 1)`, so the same complaint got a
    different `window_index` -- and therefore a different `block_key` --
    depending on the scope it was indexed under. Two signatures built under
    different origins would then never bucket together, silently.
    """

    def test_block_key_does_not_depend_on_run_scope(self, tmp_path):
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

        # Two independent databases with identical contents, so the only
        # difference between the runs is the scope they were invoked with.
        scoped_dir = tmp_path / "scoped"
        corpus_dir = tmp_path / "corpus"
        scoped_dir.mkdir()
        corpus_dir.mkdir()

        scoped_async, scoped_sync = _make_oltp(scoped_dir, complaints, redactions)
        corpus_async, corpus_sync = _make_oltp(corpus_dir, complaints, redactions)

        build_dedup_index("Khordha", 2024, oltp_url=scoped_async, salt=_SALT)
        build_dedup_index(None, None, oltp_url=corpus_async, salt=_SALT)

        assert (
            _signature_rows(scoped_sync)["T1"].block_key
            == _signature_rows(corpus_sync)["T1"].block_key
        )

    def test_epoch_is_part_of_the_index_version(self):
        """Without this, changing the epoch rewrites every block key while
        leaving the version stamp claiming the rows are current, so
        --refresh-stale would not rebuild them (#136's failure mode)."""
        from janasunani.pipeline.dedup_index import DEDUP_WINDOW_EPOCH, _index_version

        version = _index_version(30, 0.5, _SALT)
        assert f"epoch={DEDUP_WINDOW_EPOCH.isoformat()}" in version

    def test_window_index_counts_from_the_absolute_epoch(self):
        from janasunani.pipeline.dedup_index import DEDUP_WINDOW_EPOCH, _window_index

        created = datetime(2024, 1, 5)
        expected = (created.date() - DEDUP_WINDOW_EPOCH).days // 30
        assert _window_index(created, DEDUP_WINDOW_EPOCH, 30) == expected
        # Same record, a scope that knows nothing about 2024: same bucket.
        assert _window_index(created, DEDUP_WINDOW_EPOCH, 30) != 0


class TestScopeIsNeverImplied:
    """A bare invocation is far more often a forgotten --slice than a
    deliberate 1.37M-row rebuild, and argv cannot tell them apart. Corpus
    scope has to be asked for."""

    def test_no_scope_at_all_is_rejected(self, monkeypatch):
        from janasunani.pipeline import dedup_index

        monkeypatch.setattr("sys.argv", ["janasunani-dedup-index"])
        with pytest.raises(SystemExit) as exc:
            dedup_index.main()
        assert exc.value.code == 2

    def test_all_conflicts_with_an_explicit_scope(self, monkeypatch):
        from janasunani.pipeline import dedup_index

        monkeypatch.setattr(
            "sys.argv",
            ["janasunani-dedup-index", "--all", "--slice", "Khordha/2024"],
        )
        with pytest.raises(SystemExit) as exc:
            dedup_index.main()
        assert exc.value.code == 2

    def test_all_runs_corpus_wide(self, oltp, monkeypatch):
        from janasunani.pipeline import dedup_index

        async_url, sync_url = oltp
        monkeypatch.setattr(
            "sys.argv",
            [
                "janasunani-dedup-index",
                "--all",
                "--oltp-url",
                async_url,
                "--salt",
                _SALT,
            ],
        )
        dedup_index.main()
        assert set(_signature_rows(sync_url)) == {"T1", "T2", "T3", "T4", "T5"}


class TestGroupRowsDescribeTheRecordNotTheRun:
    """`dedup_groups.district`/`created_year` come from the signature row.

    They used to be stamped from the run's own scope, which was
    indistinguishable from correct while every run was a single
    district-year. A corpus-wide run makes it a NOT NULL violation, which is
    loud. A district-wide run makes it a wrong year on every row, which is
    not -- so this is pinned rather than left to the constraint.
    """

    def test_district_wide_run_stamps_each_row_with_its_own_year(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", None, oltp_url=async_url, salt=_SALT)

        groups = _group_rows(sync_url)
        assert groups["T1"].created_year == 2024
        # T4 is the 2023 row. Stamping the run scope would have made this
        # 2024 as well, or None, depending on what the run was invoked with.
        assert groups["T4"].created_year == 2023
        assert {g.district for g in groups.values()} == {"Khordha"}

    def test_corpus_wide_run_stamps_each_row_with_its_own_district(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        groups = _group_rows(sync_url)
        assert groups["T1"].district == "Khordha"
        assert groups["T5"].district == "Puri"
        assert groups["T4"].created_year == 2023


class TestCodexFollowups317:
    """The four findings on #325, each pinned by the failure it caused."""

    def test_refresh_stale_does_not_call_every_mismatch_moved(self, cross_scope_oltp):
        """P1. The moved-source predicate had a second copy inside
        _index_signatures. Fixing only the one in
        _raise_if_source_is_not_current left --all --refresh-stale comparing
        every row against None and raising "moved outside this district-year
        source slice" for a corpus run, where nothing can move."""
        async_url, sync_url = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        # Change a source record so its stored digest no longer matches.
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :t "
                    "WHERE ticket_no = 'X3'"
                ),
                {"t": UNRELATED_C},
            )
        engine.dispose()

        # Must rebuild, not raise a membership error.
        counts = build_dedup_index(
            None, None, oltp_url=async_url, salt=_SALT, refresh_stale=True
        )
        assert counts["processed"] >= 1

    def test_group_rows_carry_their_own_slice_snapshot(self, oltp):
        """P1. analytics/findings/workload.py reads one district-year from the
        lake, recomputes source_snapshot_id over it, and asserts every group
        row carries that value. Stamping one corpus-wide digest on every row
        would fail that assertion for every slice."""
        async_url, sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        groups = _group_rows(sync_url)
        khordha_2024 = {groups[t].source_snapshot_id for t in ("T1", "T2", "T3")}
        assert len(khordha_2024) == 1

        # Different district-years must not share a digest, or the stamp is
        # not describing the slice at all.
        assert groups["T4"].source_snapshot_id not in khordha_2024  # Khordha/2023
        assert groups["T5"].source_snapshot_id not in khordha_2024  # Puri/2024

    def test_scoped_snapshot_matches_what_a_scoped_run_would_stamp(self, oltp, tmp_path):
        """The per-slice digest from a corpus run must equal the digest a
        district-year run produces, or consumers still cannot verify."""
        scoped_dir = tmp_path / "scoped"
        scoped_dir.mkdir()
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
        scoped_async, scoped_sync = _make_oltp(scoped_dir, complaints, redactions)

        corpus_async, corpus_sync = oltp
        build_dedup_index("Khordha", 2024, oltp_url=scoped_async, salt=_SALT)
        build_dedup_index(None, None, oltp_url=corpus_async, salt=_SALT)

        assert (
            _group_rows(scoped_sync)["T1"].source_snapshot_id
            == _group_rows(corpus_sync)["T1"].source_snapshot_id
        )

    def test_scoped_rerun_refuses_to_split_a_cross_scope_group(self, cross_scope_oltp):
        """P1. X1/X2 are one group spanning 2023 and 2024. Regrouping 2024
        alone would recompute X2 as a singleton and upsert only it, leaving
        X1 stamped with a group id and size that no longer hold."""
        async_url, _ = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        # Matched on the guard's own wording: _source_membership_changed_error
        # also says "outside", and this must not pass on that instead.
        with pytest.raises(ValueError, match="would split them"):
            build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)

    def test_scoped_rerun_is_allowed_when_no_group_straddles(self, oltp):
        """The guard must not block the ordinary case, or every slice run
        breaks the moment a corpus index exists."""
        async_url, sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert set(_group_rows(sync_url)) >= {"T1", "T2", "T3"}

    def test_complaints_missing_district_or_year_are_not_indexed(self, tmp_path):
        """P2. Complaint.district/created_year are nullable; DedupSignature
        declares both NOT NULL. A scoped run never saw such a row because
        `district = 'X'` is NULL for it. Removing the predicate for --all
        removed that accidental filter and the backfill died on an integrity
        error partway through."""
        complaints = [
            {
                "ticket_no": "N1",
                "district": "Khordha",
                "created_year": 2024,
                "created_on": datetime(2024, 1, 5),
                "grievance": "raw n1",
            },
            {
                "ticket_no": "N2",
                "district": None,
                "created_year": 2024,
                "created_on": datetime(2024, 1, 6),
                "grievance": "raw n2",
            },
            {
                "ticket_no": "N3",
                "district": "Khordha",
                "created_year": None,
                "created_on": datetime(2024, 1, 7),
                "grievance": "raw n3",
            },
        ]
        redactions = [
            {"ticket_no": "N1", "grievance_redacted": UNRELATED_A},
            {"ticket_no": "N2", "grievance_redacted": UNRELATED_B},
            {"ticket_no": "N3", "grievance_redacted": UNRELATED_C},
        ]
        async_url, sync_url = _make_oltp(tmp_path, complaints, redactions)

        counts = build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        assert set(_signature_rows(sync_url)) == {"N1"}
        # They are excluded from the denominator too, so the run does not
        # report itself as perpetually incomplete.
        assert counts["total"] == 1


class TestGroupingScopeProvenance:
    """#317 follow-up. The per-slice source digest cannot certify a corpus
    grouping, because a ticket outside a row's district-year can bridge two
    otherwise separate groups inside it. Change that outside ticket, regroup,
    and the slice's distinct-problem count moves while its source_snapshot_id
    does not. The grouping-scope digest is what makes the two runs tell
    apart."""

    def test_regrouping_the_same_records_is_deterministic(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        first = _group_rows(sync_url)["T1"].grouping_scope_snapshot_id

        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        assert _group_rows(sync_url)["T1"].grouping_scope_snapshot_id == first

    def test_a_different_threshold_changes_the_scope_but_not_the_slice_digest(
        self, oltp
    ):
        """The scope digest covers the parameters that produced an assignment,
        not only which records were read. Regrouping the identical records at
        a different --threshold yields different duplicate groups, so two
        artifacts from the two runs must not compare equal.

        The slice digest must *not* move: a consumer recomputes it from lake
        records and knows nothing about grouping parameters, so folding them
        in there would make it unverifiable."""
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        before = _group_rows(sync_url)["T1"]

        build_dedup_index(
            "Khordha", 2024, oltp_url=async_url, salt=_SALT, threshold=0.9
        )
        after = _group_rows(sync_url)["T1"]

        assert after.grouping_scope_snapshot_id != before.grouping_scope_snapshot_id
        assert after.source_snapshot_id == before.source_snapshot_id

    def test_a_different_window_changes_the_scope(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)
        before = _group_rows(sync_url)["T1"].grouping_scope_snapshot_id

        build_dedup_index(
            "Khordha", 2024, oltp_url=async_url, salt=_SALT, window_days=7,
            refresh_stale=True,
        )
        assert _group_rows(sync_url)["T1"].grouping_scope_snapshot_id != before

    def test_corpus_run_scope_digest_is_wider_than_the_slice_digest(self, oltp):
        async_url, sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        rows = _group_rows(sync_url)
        # One scope for the whole run...
        assert len({r.grouping_scope_snapshot_id for r in rows.values()}) == 1
        # ...and it is not any individual slice's digest.
        assert rows["T1"].grouping_scope_snapshot_id != rows["T1"].source_snapshot_id

    def test_changing_an_out_of_slice_record_changes_the_scope_digest(
        self, cross_scope_oltp
    ):
        """The failure Codex described, pinned directly. X1 (2023) is outside
        Khordha/2024 but participates in its grouping. Editing X1 must change
        what Khordha/2024's rows advertise, or two different group
        assignments are publishable as the same snapshot."""
        async_url, sync_url = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)
        before = _group_rows(sync_url)["X2"]

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :t "
                    "WHERE ticket_no = 'X1'"
                ),
                {"t": UNRELATED_C},
            )
        engine.dispose()
        build_dedup_index(
            None, None, oltp_url=async_url, salt=_SALT, refresh_stale=True
        )
        after = _group_rows(sync_url)["X2"]

        # X2's own slice is untouched, so the old stamp cannot distinguish them.
        assert after.source_snapshot_id == before.source_snapshot_id
        # The scope digest can.
        assert after.grouping_scope_snapshot_id != before.grouping_scope_snapshot_id

    def test_mixed_grouping_scopes_are_refused(self):
        """assert_group_source_snapshot must reject rows assembled from two
        group assignments even when their slice digests agree."""
        records = [
            {
                "ticket_no": ticket,
                "district": "Khordha",
                "created_year": 2024,
                "created_on": datetime(2024, 1, 5),
                "petitioner_mobile": None,
                "petitioner_email": None,
                "grievance_redacted": text_,
            }
            for ticket, text_ in (("A1", UNRELATED_A), ("A2", UNRELATED_B))
        ]
        digest = source_snapshot_id(records)

        def rows_with(scope_a, scope_b):
            return [
                {
                    "ticket_no": "A1",
                    "source_name": DEDUP_SOURCE_NAME,
                    "source_snapshot_id": digest,
                    "grouping_scope_snapshot_id": scope_a,
                },
                {
                    "ticket_no": "A2",
                    "source_name": DEDUP_SOURCE_NAME,
                    "source_snapshot_id": digest,
                    "grouping_scope_snapshot_id": scope_b,
                },
            ]

        # Uniform scope is fine, and the ticket population is complete in both
        # cases so only the scope differs between them.
        assert assert_group_source_snapshot(rows_with("s1", "s1"), records) == digest

        with pytest.raises(DedupSourceSnapshotMismatch, match="mix grouping scopes"):
            assert_group_source_snapshot(rows_with("s1", "s2"), records)

    def test_absent_grouping_scope_is_refused_not_treated_as_agreement(self):
        """A set of scopes that is exactly {None} passes a 'no two different
        values' test. Rows written before the column existed all carry NULL,
        so without this the legacy case publishes a blank scope that compares
        equal to every other blank one -- reopening the hole the field closes."""
        records = [
            {
                "ticket_no": "L1",
                "district": "Khordha",
                "created_year": 2024,
                "created_on": datetime(2024, 1, 5),
                "petitioner_mobile": None,
                "petitioner_email": None,
                "grievance_redacted": UNRELATED_A,
            }
        ]
        digest = source_snapshot_id(records)
        legacy = [
            {
                "ticket_no": "L1",
                "source_name": DEDUP_SOURCE_NAME,
                "source_snapshot_id": digest,
                "grouping_scope_snapshot_id": None,
            }
        ]
        with pytest.raises(DedupSourceSnapshotMismatch, match="lack grouping-scope"):
            assert_group_source_snapshot(legacy, records)

    def test_stale_signatures_produce_a_distinct_scope_from_a_refreshed_run(
        self, oltp
    ):
        """Without --refresh-stale the runner deliberately leaves stale
        signatures in place, so a run can group old-parameter rows together
        with newly indexed ones. Identity linkage breaks across that boundary
        (#136) and the assignments are wrong; a later full refresh produces
        different, correct ones from identical source records under the
        identical *requested* version. Hashing only the requested version made
        those two runs indistinguishable."""
        async_url, sync_url = oltp
        build_dedup_index("Khordha", 2024, oltp_url=async_url, salt=_SALT)

        # A different salt makes every stored signature stale. Without
        # --refresh-stale they survive, so this run groups over rows whose
        # stored version is not the one it asked for.
        stale_run = build_dedup_index(
            "Khordha", 2024, oltp_url=async_url, salt="a-rotated-salt"
        )
        assert stale_run["processed"] == 0  # nothing rebuilt: they are stale, not missing
        mixed = _group_rows(sync_url)["T1"].grouping_scope_snapshot_id

        refreshed = build_dedup_index(
            "Khordha",
            2024,
            oltp_url=async_url,
            salt="a-rotated-salt",
            refresh_stale=True,
        )
        assert refreshed["processed"] > 0
        assert _group_rows(sync_url)["T1"].grouping_scope_snapshot_id != mixed


class TestCorpusScaleMemory:
    """Codex P1 (two findings) on #325: `--all` is the point of the PR, and
    at corpus scale both the refresh and the grouping load would have failed
    before doing any work.

    These pin the shape of the fix rather than the byte count, which no unit
    test can assert honestly. What is checkable is that the text never
    accumulates and the signature never survives banding.
    """

    def test_the_mismatch_scan_carries_identity_not_text(self, cross_scope_oltp):
        """The documented post-redaction run is `--all --refresh-stale`, where
        every signature mismatches at once. Carrying each row's grievance text
        through this scan would hold the whole redacted corpus in memory
        before rebuilding a single signature.
        """
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        from janasunani.pipeline import dedup_index as di

        async_url, sync_url = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE grievance_redactions SET grievance_redacted = :t "
                    "WHERE ticket_no = 'X3'"
                ),
                {"t": UNRELATED_C},
            )
        engine.dispose()

        async def _scan():
            aengine = create_async_engine(async_url)
            try:
                async with aengine.begin() as conn:
                    return await di._source_digest_mismatches(conn, None, None)
            finally:
                await aengine.dispose()

        mismatches, missing = asyncio.run(_scan())

        assert missing == []
        assert [m[0] for m in mismatches] == ["X3"]
        # (ticket_no, district, created_year) and nothing else. The assertion
        # that matters is the width: a text column here is the whole defect.
        for row in mismatches:
            assert len(row) == 3
            assert not any(
                isinstance(value, str) and len(value) > 64 for value in row
            )

    def test_the_source_refresh_runs_in_batches(self, cross_scope_oltp, monkeypatch):
        """One pass would build every replacement row beside the whole fetched
        corpus and submit them through a single multi-row upsert."""
        from janasunani.pipeline import dedup_index as di

        async_url, sync_url = cross_scope_oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            # Every row mismatches, which is the post-redaction shape.
            conn.execute(
                text("UPDATE grievance_redactions SET grievance_redacted = :t"),
                {"t": UNRELATED_C},
            )
            total = conn.execute(
                text("SELECT count(*) FROM dedup_signatures")
            ).scalar_one()
        engine.dispose()
        assert total > 1, "fixture must have more than one signature to batch"

        monkeypatch.setattr(di, "BATCH_SIZE", 1)
        seen: list[int] = []
        original = di._load_source_rows_for_tickets

        async def _spy(conn, ticket_nos):
            seen.append(len(ticket_nos))
            return await original(conn, ticket_nos)

        monkeypatch.setattr(di, "_load_source_rows_for_tickets", _spy)

        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT, refresh_stale=True)

        # One fetch per batch, each bounded by BATCH_SIZE — never one call
        # holding every mismatched ticket's text at once.
        assert len(seen) == total
        assert set(seen) == {1}

    def test_loaded_signatures_keep_bands_and_drop_the_signature(self, oltp):
        """1.37M rows x a 128-element JSON array is roughly 4.6 GiB of Python
        ints before anything else. The array is only ever used to compute
        bands — verification re-reads the text — so it must not survive the
        load.
        """
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        from janasunani.pipeline import dedup_index as di

        async_url, _sync_url = oltp
        build_dedup_index(None, None, oltp_url=async_url, salt=_SALT)

        async def _load():
            aengine = create_async_engine(async_url)
            try:
                async with aengine.begin() as conn:
                    return await di._load_slice_signatures(conn, None, None, 16)
            finally:
                await aengine.dispose()

        rows = asyncio.run(_load())

        assert rows, "fixture must produce signatures"
        for row in rows:
            assert not hasattr(row, "signature")
            assert row.bands is None or len(row.bands) == 16
        # At least one real signature, or the assertion above is vacuous.
        assert any(row.bands is not None for row in rows)

    def test_bucketing_refuses_bands_built_for_a_different_band_count(self):
        """Banding at load time and bucketing at another count would produce
        buckets that are not LSH bands of anything."""
        from janasunani.pipeline import dedup_index as di

        row = di._BandedSignature(
            ticket_no="T1",
            district="Khordha",
            created_year=2024,
            block_key="Khordha:latin:0",
            identity_key_mobile=None,
            identity_key_email=None,
            bands=(1, 2, 3, 4),
        )
        with pytest.raises(ValueError, match="carries 4 band"):
            di._candidate_buckets([row], 16)


class TestBandBucketsAreBuiltOneBandAtATime:
    """Codex P1 on #325, after the raw signatures were already dropped.

    Dropping the 128-element arrays fixed the rows; the bucket map was the
    larger half and survived it. Building all sixteen bands before filtering
    singletons costs 16N dictionary entries at peak -- ~21.9M for the corpus
    -- and nearly all of them are singletons thrown away immediately: a
    signature colliding with nothing still occupies sixteen.
    """

    @staticmethod
    def _rows(n, num_bands, seed=11):
        import random

        from janasunani.pipeline.dedup_index import _BandedSignature

        rng = random.Random(seed)
        return [
            _BandedSignature(
                ticket_no=f"CMO2024{i:07d}",
                district=f"D{i % 7}",
                created_year=2024,
                block_key=f"D{i % 7}:latin:{i % 5}",
                identity_key_mobile=(f"m{i // 3}" if i % 3 == 0 else None),
                identity_key_email=None,
                # 2% of bands collide, so real buckets exist to be found.
                bands=tuple(
                    rng.getrandbits(63) if rng.random() > 0.02 else rng.randrange(40)
                    for _ in range(num_bands)
                ),
            )
            for i in range(n)
        ]

    @staticmethod
    def _all_at_once(rows, num_bands):
        """The previous implementation, kept here as the thing to match."""
        from collections import defaultdict

        band = defaultdict(list)
        for r in rows:
            for bi, bh in enumerate(r.bands):
                band[(r.block_key, bi, bh)].append(r.ticket_no)
        ident = defaultdict(list)
        for r in rows:
            for kind, k in (
                ("mobile", r.identity_key_mobile),
                ("email", r.identity_key_email),
            ):
                if k is not None:
                    ident[(kind, k)].append(r.ticket_no)
        return [
            (b, m) for (b, _i, _h), m in band.items() if len(set(m)) > 1
        ] + [(None, m) for m in ident.values() if len(set(m)) > 1]

    @staticmethod
    def _norm(buckets):
        return sorted((b, tuple(sorted(m))) for b, m in buckets)

    def test_the_buckets_are_exactly_what_the_all_at_once_build_produced(self):
        """Banding is independent per band index, so a bucket can never span
        two of them -- which is why the split is safe. Asserted rather than
        argued, because a wrong bucket here silently changes group membership.
        """
        from janasunani.pipeline import dedup_index as di

        rows = self._rows(3000, 8)
        got = di._candidate_buckets(rows, 8)
        assert self._norm(got) == self._norm(self._all_at_once(rows, 8))
        assert got, "fixture must produce real (non-singleton) buckets"

    def test_peak_memory_is_a_fraction_of_the_all_at_once_build(self):
        import tracemalloc

        from janasunani.pipeline import dedup_index as di

        rows = self._rows(20_000, 16)

        def peak(fn):
            tracemalloc.start()
            # Materialise, so a generator is measured on the same footing as
            # the list the old build returned.
            held = list(fn(rows, 16))
            _, pk = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert held
            return pk

        before = peak(self._all_at_once)
        after = peak(di._candidate_buckets)
        # Measured ~17x on a corpus-shaped fixture. Asserted well below that
        # so the test pins the property, not the machine it ran on.
        assert after * 4 < before, (
            f"peak {after / 2**20:.1f} MiB vs {before / 2**20:.1f} MiB — "
            "band buckets are being retained across bands again"
        )


class TestGroupingIsScopedPerBlock:
    """#336. Corpus-wide grouping needed ~59 GB because every structure in the
    pass was sized by the scope: buckets for the whole run held twice, then
    candidate text and a shingle memo for every candidate ticket at once.

    Measured on Sambalpur/2024 (55,544 of 1,361,842 signatures) the grouping
    pass peaked at 3,169 MB against a 788 MB baseline, which extrapolates to
    ~59 GB on a 15.7 GB box. `block_key` is district:script:window, so a block
    does not grow with the corpus -- 3,227 blocks, mean 422 rows, largest
    8,691 -- and scoping the caches to a block makes the peak flat in corpus
    size.
    """

    @staticmethod
    def _rows(n, num_bands, blocks=4, seed=17):
        import random

        from janasunani.pipeline.dedup_index import _BandedSignature

        rng = random.Random(seed)
        return [
            _BandedSignature(
                ticket_no=f"CMO2024{i:07d}",
                district="Sambalpur",
                created_year=2024,
                block_key=f"Sambalpur:latin:{i % blocks}",
                identity_key_mobile=(f"m{i // 3}" if i % 3 == 0 else None),
                identity_key_email=None,
                bands=tuple(
                    rng.getrandbits(63) if rng.random() > 0.05 else rng.randrange(30)
                    for _ in range(num_bands)
                ),
            )
            for i in range(n)
        ]

    def test_band_buckets_is_a_generator(self):
        """A list is what made the corpus run hold every bucket twice: once
        in the helper's return value and once in the caller's own index."""
        import inspect

        from janasunani.pipeline import dedup_index as di

        rows = self._rows(200, 8)
        assert inspect.isgenerator(di._band_buckets(rows, 8))
        assert inspect.isgenerator(di._identity_buckets(rows))

    def test_verifying_block_by_block_finds_exactly_the_same_band_buckets(self):
        """The correctness core of the change. `_band_buckets` keys on
        ``(block_key, band_hash)``, so a band bucket can never span two
        blocks -- which is why a block can be finished before the next one
        starts. Asserted rather than argued: a bucket that went missing here
        would silently drop duplicates."""
        from collections import defaultdict

        from janasunani.pipeline import dedup_index as di

        rows = self._rows(2000, 8, blocks=5)

        whole_scope = sorted(
            (block_key, tuple(sorted(set(members))))
            for block_key, members in di._band_buckets(rows, 8)
        )

        rows_by_block = defaultdict(list)
        for row in rows:
            rows_by_block[row.block_key].append(row)
        per_block = sorted(
            (block_key, tuple(sorted(set(members))))
            for block_rows in rows_by_block.values()
            for block_key, members in di._band_buckets(block_rows, 8)
        )

        assert per_block == whole_scope
        assert whole_scope, "fixture must produce real (non-singleton) buckets"

    def test_candidate_text_is_never_fetched_for_the_whole_scope_at_once(
        self, dup_oltp, monkeypatch
    ):
        """The memory property, over the real fixture and the real code path.

        One fetch for every candidate ticket in the scope is exactly what did
        not fit. The fixture spans several windows and both scripts, so a
        block-scoped fetch is necessarily smaller than the scope."""
        from janasunani.pipeline import dedup_index as di

        async_url, _ = dup_oltp
        calls: list[list[str]] = []
        real = di._load_redacted_text

        async def recording(conn, ticket_nos):
            calls.append(list(ticket_nos))
            return await real(conn, ticket_nos)

        monkeypatch.setattr(di, "_load_redacted_text", recording)
        counts = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")

        assert counts["groups"] > 0
        assert len(calls) > 1, "text was still fetched in a single pass"
        every_candidate = {t for call in calls for t in call}
        assert max(len(call) for call in calls) < len(every_candidate), (
            "one fetch covered every candidate ticket in the scope -- the "
            "caches are scope-sized again"
        )

    def test_identity_buckets_are_verified_in_batches(self, dup_oltp, monkeypatch):
        """Identity buckets carry no block key, so batching is the only thing
        bounding them. With a one-ticket budget, each identity bucket must
        cost its own fetch."""
        from janasunani.pipeline import dedup_index as di

        async_url, _ = dup_oltp
        calls: list[list[str]] = []
        real = di._load_redacted_text

        async def recording(conn, ticket_nos):
            calls.append(list(ticket_nos))
            return await real(conn, ticket_nos)

        monkeypatch.setattr(di, "_load_redacted_text", recording)
        monkeypatch.setattr(di, "IDENTITY_BATCH_TICKETS", 1)
        batched = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")
        batched_calls = len(calls)

        calls.clear()
        monkeypatch.setattr(di, "IDENTITY_BATCH_TICKETS", 10_000_000)
        unbatched = build_dedup_index("Sambalpur", 2024, oltp_url=async_url, salt="s")

        assert batched_calls > len(calls), "batch size did not change the fetches"
        # Batching is a memory knob, never an answer knob.
        assert batched["groups"] == unbatched["groups"]
        assert batched["verified_pairs"] == unbatched["verified_pairs"]
        assert batched["comparison_pairs"] == unbatched["comparison_pairs"]


class TestLargeBucketShingleRetention:
    """#336. Identity buckets are wildly uneven: measured over the corpus,
    13,428 non-singleton buckets cover 1,355,855 of 1,361,842 signatures and
    the largest holds 201,965 of them. Nothing can split one bucket, since
    every member is compared against the anchors -- so the bucket has to be
    survivable, which means not keeping a shingle set per member.
    """

    @staticmethod
    def _bucket(n):
        members = [f"T{i:04d}" for i in range(n)]
        text = {t: f"grievance about the road near ward {t}" for t in members}
        return members, text

    def test_large_bucket_memoises_only_its_anchors(self):
        from janasunani.pipeline import dedup_index as di

        members, text = self._bucket(300)
        shingle_cache: dict[str, set[str]] = {}
        _matches, _checked, used_large_policy = di._verify_bucket(
            members, text, shingle_cache, {}, 0.9, 200, 8
        )

        assert used_large_policy is True
        assert set(shingle_cache) == set(members[:8]), (
            "non-anchors are read once and must not be memoised"
        )

    def test_small_bucket_still_memoises_every_member(self):
        """#158's reuse is on the exhaustive path and must survive #336."""
        from janasunani.pipeline import dedup_index as di

        members, text = self._bucket(10)
        shingle_cache: dict[str, set[str]] = {}
        _matches, _checked, used_large_policy = di._verify_bucket(
            members, text, shingle_cache, {}, 0.9, 200, 8
        )

        assert used_large_policy is False
        assert set(shingle_cache) == set(members)

    def test_a_cached_non_anchor_is_reused_rather_than_recomputed(self):
        """Not adding to the cache is not the same as ignoring it. A member
        another bucket already paid for is still read from the memo."""
        from janasunani.pipeline import dedup_index as di

        members, text = self._bucket(300)
        seeded = members[100]
        shingle_cache = {seeded: shingles(text[seeded])}
        before = shingle_cache[seeded]

        di._verify_bucket(members, text, shingle_cache, {}, 0.9, 200, 8)

        assert shingle_cache[seeded] is before

    def test_the_answer_does_not_depend_on_what_was_cached(self):
        """The cache is a memo, never an input to the decision."""
        from janasunani.pipeline import dedup_index as di

        members, text = self._bucket(300)

        cold = di._verify_bucket(members, text, {}, {}, 0.9, 200, 8)
        warm_cache = {t: shingles(text[t]) for t in members}
        warm = di._verify_bucket(members, text, warm_cache, {}, 0.9, 200, 8)

        assert cold == warm
