"""Redaction pass over complaints.grievance (real code path, SQLite OLTP).

Synthetic complaint text only. The redactor itself is stubbed in most tests so
they run without presidio; one test exercises the real analyzer and skips if
the `pii` extra is absent.
"""

import pytest
from sqlalchemy import create_engine, insert, select, text

from janasunani.db.models import Base, Complaint, GrievanceRedaction
from janasunani.pipeline.redact_grievance import main, redact_grievances

# Deliberately not real PII: these tests assert plumbing, not detection.
ROWS = [
    ("T1", "Khordha", 2024, "water supply broken since June"),
    ("T2", "Khordha", 2024, "no pension for three months"),
    ("T3", "Khordha", 2024, "street light not working"),
    ("T4", "Khordha", 2023, "different year, must not be touched"),
    ("T5", "Puri", 2024, "different district, must not be touched"),
    ("T6", "Khordha", 2024, None),
    ("T7", "Khordha", 2024, ""),
]


@pytest.fixture
def oltp(tmp_path):
    """A SQLite OLTP seeded with one district-year slice plus decoys."""
    path = tmp_path / "oltp.db"
    sync_url = f"sqlite:///{path}"
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for ticket, district, year, grievance in ROWS:
            conn.execute(
                insert(Complaint).values(
                    ticket_no=ticket,
                    district=district,
                    created_year=year,
                    grievance=grievance,
                )
            )
    engine.dispose()
    return f"sqlite+aiosqlite:///{path}", sync_url


def redactions(sync_url: str) -> dict[str, str]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(
            select(GrievanceRedaction.ticket_no, GrievanceRedaction.grievance_redacted)
        ).all()
    engine.dispose()
    return {r[0]: r[1] for r in rows}


def stub(text_in: str) -> str:
    return f"REDACTED::{text_in}"


@pytest.fixture(autouse=True)
def _stub_redactor(monkeypatch):
    """Stub redact_text so plumbing tests do not need presidio.

    Patched at its source module, which is where redact_grievances imports it
    from at call time.
    """
    import sys
    import types

    module = types.ModuleType("janasunani.pipeline.stages.pii_tagger")
    module.redact_text = stub
    real = sys.modules.get("janasunani.pipeline.stages.pii_tagger")
    sys.modules["janasunani.pipeline.stages.pii_tagger"] = module
    yield
    if real is not None:
        sys.modules["janasunani.pipeline.stages.pii_tagger"] = real
    else:
        del sys.modules["janasunani.pipeline.stages.pii_tagger"]


class TestSliceScoping:
    """--district and --year have no defaults because redacting the wrong slice
    means processing citizen text nobody authorised."""

    def test_only_the_named_slice_is_processed(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert set(redactions(sync_url)) == {"T1", "T2", "T3"}

    def test_other_year_is_untouched(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert "T4" not in redactions(sync_url)

    def test_other_district_is_untouched(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert "T5" not in redactions(sync_url)

    def test_null_and_empty_grievances_are_skipped(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        written = redactions(sync_url)
        assert "T6" not in written and "T7" not in written


class TestResumability:
    """The pending predicate is the resume mechanism. If it regresses to an
    offset, an interrupted run silently skips unprocessed rows."""

    def test_counts_report_the_slice(self, oltp):
        async_url, _ = oltp
        counts = redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert counts == {"total": 3, "already_redacted": 0, "processed": 3, "stale_at_start": 0}

    def test_second_run_is_a_no_op(self, oltp):
        async_url, _ = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        second = redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert second == {"total": 3, "already_redacted": 3, "processed": 0, "stale_at_start": 0}

    def test_an_interrupted_run_resumes_where_it_stopped(self, oltp):
        async_url, sync_url = oltp
        first = redact_grievances("Khordha", 2024, oltp_url=async_url, limit=2)
        assert first["processed"] == 2

        second = redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert second["already_redacted"] == 2
        assert second["processed"] == 1
        assert set(redactions(sync_url)) == {"T1", "T2", "T3"}

    def test_rerunning_does_not_duplicate_rows(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM grievance_redactions")).scalar()
        engine.dispose()
        assert total == 3


class TestWhatIsWritten:
    def test_redacted_text_is_the_redactor_output(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert redactions(sync_url)["T1"] == stub("water supply broken since June")

    def test_the_original_is_never_overwritten(self, oltp):
        """A redaction is derived from one analyzer version. Losing the input
        would make a re-run under a better analyzer impossible."""
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            original = conn.execute(
                select(Complaint.grievance).where(Complaint.ticket_no == "T1")
            ).scalar()
        engine.dispose()
        assert original == "water supply broken since June"

    def test_analyzer_version_is_stamped(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            stamp = conn.execute(
                select(GrievanceRedaction.analyzer_version).where(
                    GrievanceRedaction.ticket_no == "T1"
                )
            ).scalar()
        engine.dispose()
        assert stamp and "presidio-analyzer=" in stamp

    def test_stamp_carries_our_own_code_version(self, oltp):
        """#55, #56 and #91 all changed what gets redacted with every package
        version unchanged. A package-only stamp would mark materially different
        redactions as identical and make targeted reprocessing impossible."""
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            stamp = conn.execute(
                select(GrievanceRedaction.analyzer_version).where(
                    GrievanceRedaction.ticket_no == "T1"
                )
            ).scalar()
        engine.dispose()
        assert "janasunani=" in stamp

    def test_redacted_at_is_naive_utc(self, oltp):
        """asyncpg refuses to bind a tz-aware value into TIMESTAMP WITHOUT TIME
        ZONE while SQLite accepts it, so an aware value passes every test here
        and fails on the first batch against the deployed Postgres."""
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            stamped = conn.execute(
                select(GrievanceRedaction.redacted_at).where(
                    GrievanceRedaction.ticket_no == "T1"
                )
            ).scalar()
        engine.dispose()
        assert stamped is not None
        assert stamped.tzinfo is None


class TestSchemaGuards:
    def test_grievance_redacted_is_in_no_index(self):
        """Postgres btree entries cap at ~2.7KB and grievance prose exceeds it.
        An index on a text column of this kind broke the first cloud migration."""
        indexed = {
            col.name
            for index in GrievanceRedaction.__table__.indexes
            for col in index.columns
        }
        assert "grievance_redacted" not in indexed

    def test_primary_key_is_the_ticket(self):
        assert [c.name for c in GrievanceRedaction.__table__.primary_key] == ["ticket_no"]

    def test_table_reaches_the_lake(self):
        """LAKE_TABLES is a hardcoded tuple; a new table reaches Parquet only by
        being named in it."""
        from janasunani.olap.materialize import LAKE_TABLES

        assert "grievance_redactions" in LAKE_TABLES

    def test_original_grievance_column_is_not_denylisted(self):
        """Dropping complaints.grievance from the lake is Phase 18, not this
        pass. Redaction is the control here, not column suppression."""
        from janasunani.olap.materialize import LAKE_COLUMN_DENYLIST

        assert "grievance" not in LAKE_COLUMN_DENYLIST.get("complaints", frozenset())


class TestRealAnalyzer:
    def test_real_redactor_removes_a_mobile_number(self, oltp, monkeypatch):
        """One test on the real code path. Asserts only on PII that both the
        pre- and post-#91 analyzer redact, so this does not go red when the
        recognizers change."""
        pytest.importorskip("presidio_analyzer")
        import sys

        sys.modules.pop("janasunani.pipeline.stages.pii_tagger", None)

        async_url, sync_url = oltp
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE complaints SET grievance = :g WHERE ticket_no = 'T1'"),
                {"g": "please call me on 9876543210 about the water supply"},
            )
        engine.dispose()

        redact_grievances("Khordha", 2024, oltp_url=async_url)
        out = redactions(sync_url)["T1"]
        assert "9876543210" not in out
        assert "water supply" in out


class TestRefreshStale:
    """#130. Without this the analyzer_version stamp is write-only: a completed
    slice is excluded forever, so a better analyzer can never reach the rows it
    was built to fix. #139 and #120 are exactly that case."""

    def _restamp(self, sync_url: str, version: str) -> None:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE grievance_redactions SET analyzer_version = :v"),
                {"v": version},
            )
        engine.dispose()

    def test_stale_rows_are_counted_even_when_not_reprocessed(self, oltp):
        """Reported by every run, so the need is visible rather than something
        you have to think to check."""
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        self._restamp(sync_url, "presidio-analyzer=0.0.1 an-older-build")

        counts = redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert counts["stale_at_start"] == 3
        assert counts["processed"] == 0

    def test_stale_rows_are_not_reprocessed_by_default(self, oltp):
        """A backfill over citizen text must not change scope because an
        unrelated commit landed."""
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        self._restamp(sync_url, "presidio-analyzer=0.0.1 an-older-build")

        counts = redact_grievances("Khordha", 2024, oltp_url=async_url)
        assert counts["processed"] == 0

    def test_refresh_stale_reprocesses_them(self, oltp):
        async_url, sync_url = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        self._restamp(sync_url, "presidio-analyzer=0.0.1 an-older-build")

        counts = redact_grievances("Khordha", 2024, oltp_url=async_url, refresh_stale=True)
        assert counts["processed"] == 3

        engine = create_engine(sync_url)
        with engine.begin() as conn:
            stamps = conn.execute(
                select(GrievanceRedaction.analyzer_version).distinct()
            ).scalars().all()
        engine.dispose()
        assert len(stamps) == 1 and "0.0.1" not in stamps[0]

    def test_refresh_stale_leaves_current_rows_alone(self, oltp):
        """Rows already on the current analyzer are not rewritten, so a refresh
        run costs only what actually changed."""
        async_url, _ = oltp
        redact_grievances("Khordha", 2024, oltp_url=async_url)
        counts = redact_grievances("Khordha", 2024, oltp_url=async_url, refresh_stale=True)
        assert counts["processed"] == 0
        assert counts["stale_at_start"] == 0


def test_refresh_progress_does_not_exceed_the_slice(oltp, caplog):
    """In a refresh run every row is already redacted, so adding `already` to
    `processed` reported more rows than the slice contains."""
    from loguru import logger as _logger

    async_url, sync_url = oltp
    redact_grievances("Khordha", 2024, oltp_url=async_url)
    # Force a version mismatch, or there is nothing stale to reprocess and the
    # run emits no progress lines at all.
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE grievance_redactions SET analyzer_version = :v"),
            {"v": "presidio-analyzer=0.0.1 an-older-build"},
        )
    engine.dispose()

    records: list[str] = []
    sink = _logger.add(lambda m: records.append(str(m)), level="INFO", format="{message}")
    try:
        redact_grievances("Khordha", 2024, oltp_url=async_url, refresh_stale=True)
    finally:
        _logger.remove(sink)

    progress = [r for r in records if "redacted" in r and " of " in r]
    assert progress, "expected at least one progress line"
    for line in progress:
        done = int(line.split("redacted ")[1].split(" of ")[0])
        total = int(line.split(" of ")[1].split(" ")[0])
        assert done <= total, line


def test_refresh_progress_counts_current_rows_in_a_mixed_slice(oltp, capsys):
    """A slice that is part current and part stale must not finish at the stale
    count alone: 48,544 current plus 7,000 stale ending at '7000 of 55544'
    reads as a run that gave up (Codex on #141)."""
    from loguru import logger as _logger

    async_url, sync_url = oltp
    redact_grievances("Khordha", 2024, oltp_url=async_url)

    # Make exactly one of the three rows stale.
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE grievance_redactions SET analyzer_version = :v "
                "WHERE ticket_no = 'T1'"
            ),
            {"v": "presidio-analyzer=0.0.1 an-older-build"},
        )
    engine.dispose()

    records: list[str] = []
    sink = _logger.add(lambda m: records.append(str(m)), level="INFO", format="{message}")
    try:
        counts = redact_grievances(
            "Khordha", 2024, oltp_url=async_url, refresh_stale=True
        )
    finally:
        _logger.remove(sink)

    assert counts["stale_at_start"] == 1
    assert counts["processed"] == 1

    progress = [r for r in records if "redacted" in r and " of " in r]
    assert progress
    final = progress[-1]
    done = int(final.split("redacted ")[1].split(" of ")[0])
    total = int(final.split(" of ")[1].split(" ")[0])
    # Two rows were already current, one was made current: all three.
    assert done == total == 3, final


def test_done_line_does_not_exceed_the_slice(oltp, monkeypatch):
    """The per-batch progress line was fixed in #141/#147 and this one was not,
    so the refresh ended on '111088 of 55544'. It is the line that gets pasted
    into a run record, which is worse than the one that scrolls past."""
    import sys

    from loguru import logger as _logger

    async_url, sync_url = oltp
    redact_grievances("Khordha", 2024, oltp_url=async_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE grievance_redactions SET analyzer_version = :v"),
            {"v": "presidio-analyzer=0.0.1 an-older-build"},
        )
    engine.dispose()

    records: list[str] = []
    sink = _logger.add(lambda m: records.append(str(m)), level="INFO", format="{message}")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "redact_grievance.py",
            "--district",
            "Khordha",
            "--year",
            "2024",
            "--oltp-url",
            async_url,
            "--refresh-stale",
        ],
    )
    try:
        main()
    finally:
        _logger.remove(sink)

    done = [r for r in records if r.startswith("done:")]
    assert done, "expected a done line"
    counted = int(done[-1].split("this run, ")[1].split(" of ")[0])
    total = int(done[-1].split(" of ")[1].split(" ")[0])
    assert counted == total == 3, done[-1]
