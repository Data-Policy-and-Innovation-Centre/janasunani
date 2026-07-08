"""Tests for the ORM table definitions."""

from sqlalchemy import create_engine, inspect

from janasunani.db.models import ActionHistory, Base, Complaint, LiveGrievance


def test_metadata_creates_expected_tables():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "complaints",
        "action_history",
        "districts",
        "api_request_tracking",
        "action_history_api_request_tracking",
        "live_grievances",
    } <= tables


def test_complaint_models_full_dump_plus_ingestion_columns():
    cols = set(Complaint.__table__.columns.keys())
    # 56 dump columns + id PK + 4 ingestion-populated document columns
    assert len(cols) == 61
    # the join key the older dev ORM lacked
    assert "tracking_id" in cols
    # ingestion-only columns are present (written later, not in the dump)
    assert {
        "local_document_path",
        "document_downloaded",
        "document_download_date",
        "document_download_error",
    } <= cols
    # representative id/name pairs from the expansion
    assert {"district", "district_id", "office", "office_id"} <= cols


def test_action_history_has_ticket_and_tracking_keys():
    cols = set(ActionHistory.__table__.columns.keys())
    assert {"ticket_no", "tracking_id", "action_taken_by", "action_status"} <= cols


def test_live_grievance_preserves_result_payload():
    cols = set(LiveGrievance.__table__.columns.keys())
    assert {
        "id",
        "ticket_no",
        "status",
        "submitted_on",
        "district",
        "source",
        "category",
        "dept",
        "routing_method",
        "routing_confidence",
        "result_json",
    } <= cols
