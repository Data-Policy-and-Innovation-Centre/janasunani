"""Tests for the source→ORM mapping in janasunani.ingestion.schemas.

These guard the single place the messy raw dump column names are translated, plus
the lenient validators that keep cold-start rows from being dropped.
"""

from datetime import datetime

import pytest

from janasunani.db.models import Complaint as ComplaintModel
from janasunani.ingestion.schemas import (
    ActionHistory as ActionHistorySchema,
)
from janasunani.ingestion.schemas import (
    Complaint as ComplaintSchema,
)
from janasunani.ingestion.schemas import (
    validate,
    validate_action_history,
)


def _raw_complaint(**overrides):
    """A raw row keyed by the dump's source column names (aliases)."""
    row = {
        "ticketNumber": "T1",
        "trackingId": "TR1",
        "grievanceSubject": "Need caste certificate",
        "Document": "https://x/doc.pdf",
        "officeNAme": "Collector",
        "intDistId": 11,
        "districtName": "Cuttack",
        "govtTicket": "Yes",
        "CreatedOn": "2021-03-14 10:30:00",
        "genderName": "Male",
        "StatusName": "Disposed",
        "category": "Certificates",
    }
    row.update(overrides)
    return row


def test_alias_maps_source_names_to_orm_fields():
    c = ComplaintSchema(**_raw_complaint()).model_dump(by_alias=False)
    assert c["ticket_no"] == "T1"
    assert c["tracking_id"] == "TR1"
    assert c["grievance"] == "Need caste certificate"
    assert c["document_url"] == "https://x/doc.pdf"
    assert c["office"] == "Collector"
    assert c["district_id"] == 11
    assert c["district"] == "Cuttack"
    assert c["petitioner_gender"] == "Male"
    assert c["status"] == "Disposed"


def test_validated_dict_only_has_real_orm_columns():
    """Drift guard: every model_dump key must be a real Complaint column, so the
    schema and the ORM can never silently diverge."""
    c = ComplaintSchema(**_raw_complaint()).model_dump(by_alias=False)
    orm_columns = set(ComplaintModel.__table__.columns.keys())
    assert set(c).issubset(orm_columns), set(c) - orm_columns
    # And the schema models the full 56-column dump set.
    assert len(ComplaintSchema.model_fields) == 56


@pytest.mark.parametrize(
    "raw,expected",
    [("Yes", True), ("yes", True), ("No", False), ("no", False), ("", None), (None, None)],
)
def test_govt_ticket_coercion(raw, expected):
    c = ComplaintSchema(**_raw_complaint(govtTicket=raw))
    assert c.govt_ticket is expected


def test_datetime_coercion_handles_strings_and_garbage():
    c = ComplaintSchema(**_raw_complaint(CreatedOn="2021-03-14 10:30:00", lastUpdatedOn="not-a-date"))
    assert c.created_on == datetime(2021, 3, 14, 10, 30, 0)
    assert c.last_updated_on is None  # unparseable -> None, not an error


def test_nul_bytes_stripped_from_all_string_fields():
    # PostgreSQL text can't hold 0x00; MySQL/SQLite pass it through, and real
    # dump rows carry it (broke the first cloud migration at ~600k rows).
    c = ComplaintSchema(
        **_raw_complaint(grievanceSubject="need\x00 help", petitionerName="\x00A B")
    )
    assert c.grievance == "need help"
    assert c.petitioner_name == "A B"

    out = validate_action_history(
        [{"trackingId": "TR1", "action_taken_remark": "ok\x00"}], ticket_no="T1"
    )
    assert out[0]["action_taken_remark"] == "ok"


def test_office_validator_is_lenient():
    # Unknown office (outside the 7-value API map) must pass through, not raise.
    c = ComplaintSchema(**_raw_complaint(officeNAme="Tahasildar, Some Block"))
    assert c.office == "Tahasildar, Some Block"


def test_missing_ticket_number_is_dropped_by_validate():
    bad = _raw_complaint()
    del bad["ticketNumber"]
    # ticket_no is the one required field; validate() logs and skips the row.
    out = validate([bad, _raw_complaint()], ComplaintSchema)
    assert len(out) == 1
    assert out[0]["ticket_no"] == "T1"


def test_action_history_stamps_resolved_ticket():
    raw = {
        "trackingId": "TR1",
        "action_taken_by": "Officer A",
        "action_status": "Forwarded",
        "action_taken_remark": "remark",
        "complaint_status_with_authority": "pending",
        "action_taken_date": "2021-03-15 09:00:00",
    }
    out = validate_action_history([raw], ticket_no="T1")
    assert len(out) == 1
    rec = out[0]
    assert rec["ticket_no"] == "T1"
    assert rec["tracking_id"] == "TR1"
    assert rec["action_taken_date"] == datetime(2021, 3, 15, 9, 0, 0)


def test_action_history_schema_keys_are_real_orm_columns():
    from janasunani.db.models import ActionHistory as ActionHistoryModel

    rec = ActionHistorySchema(trackingId="TR1", action_taken_by="A").model_dump(by_alias=False)
    assert set(rec).issubset(set(ActionHistoryModel.__table__.columns.keys()))
