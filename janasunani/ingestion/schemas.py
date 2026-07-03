"""Pydantic schemas = the single raw-source -> ORM column map.

Each ``Field`` alias is the **raw source column name** (from the ``mysqldump`` /
Janasunani API); the field name is the clean snake_case ORM column. The migration
loaders and the API ingestion both validate raw rows through these schemas and
then ``model_dump(by_alias=False)`` to get ORM-ready dicts, so the messy source
names live in exactly one place.

Schemas are deliberately lenient for cold-start dump loading: every field except
``ticket_no`` is optional, and the office / govt-ticket validators normalise
when they can but never raise (a single unrecognised value should not drop an
otherwise-valid historical record).
"""

from datetime import datetime
from difflib import get_close_matches
from typing import Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import OFFICE


def _strip_nul(v):
    """Drop NUL bytes from strings. PostgreSQL text columns cannot contain
    ``0x00`` (asyncpg raises ``CharacterNotInRepertoireError``); MySQL and
    SQLite pass it through silently, so real dump rows do carry them."""
    if isinstance(v, str) and "\x00" in v:
        return v.replace("\x00", "")
    return v


def _coerce_datetime(v):
    """Best-effort parse of a datetime from the source (already a ``datetime``
    via DB reflection, or an ISO / Janasunani-formatted string). Returns ``None``
    rather than raising on anything unparseable."""
    if v is None or isinstance(v, datetime):
        return v
    for parser in (
        lambda s: datetime.fromisoformat(s),
        lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S"),
        lambda s: datetime.strptime(s, "%d-%b-%Y %H:%M %p"),
    ):
        try:
            return parser(v)
        except (ValueError, TypeError):
            continue
    return None


class District(BaseModel):
    """A district name + unique id (from the Janasunani districts API)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dist_name: str = Field(..., alias="distName")
    dist_id: int = Field(..., alias="distId")


class Complaint(BaseModel):
    """The full complaint record: all 56 source columns of
    ``t_janasunani_etl_pre_data`` mapped to snake_case ORM fields."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    _sanitize_strings = field_validator("*", mode="before")(_strip_nul)

    # Identity / linkage
    ticket_no: str = Field(..., alias="ticketNumber")
    tracking_id: Optional[str] = Field(default=None, alias="trackingId")
    created_year: Optional[int] = Field(default=None, alias="createdYear")

    # Petitioner
    petitioner_name: Optional[str] = Field(default=None, alias="petitionerName")
    petitioner_mobile: Optional[str] = Field(default=None, alias="petitionerMobile")
    petitioner_email: Optional[str] = Field(default=None, alias="petitionerEmail")
    petitioner_gender: Optional[str] = Field(default=None, alias="genderName")
    gender_id: Optional[int] = Field(default=None, alias="gender")

    # Grievance body / document
    grievance: Optional[str] = Field(default=None, alias="grievanceSubject")
    document_url: Optional[str] = Field(default=None, alias="Document")

    # Office / receiver
    office: Optional[str] = Field(default=None, alias="officeNAme")
    office_id: Optional[int] = Field(default=None, alias="intOfficeId")
    received_by: Optional[str] = Field(default=None, alias="RecievedByOfficerName")
    received_by_id: Optional[int] = Field(default=None, alias="RecievedBy")

    # Geography
    district: Optional[str] = Field(default=None, alias="districtName")
    district_id: Optional[int] = Field(default=None, alias="intDistId")
    block: Optional[str] = Field(default=None, alias="blockName")
    block_id: Optional[int] = Field(default=None, alias="intBlockId")
    state: Optional[str] = Field(default=None, alias="stateName")
    state_id: Optional[int] = Field(default=None, alias="intStateId")
    address: Optional[str] = Field(default=None, alias="Address")

    # Mode / disability
    mode: Optional[str] = Field(default=None, alias="modeName")
    mode_id: Optional[int] = Field(default=None, alias="Mode")
    disability: Optional[str] = Field(default=None, alias="disbilityName")
    disability_type: Optional[str] = Field(default=None, alias="disabilityType")

    # Status
    status: Optional[str] = Field(default=None, alias="StatusName")
    complaint_status_id: Optional[int] = Field(default=None, alias="intCompliantStatusId")
    govt_ticket: Optional[bool] = Field(default=None, alias="govtTicket")
    transfer_status: Optional[str] = Field(default=None, alias="transferStatus")
    urgent: Optional[str] = Field(default=None, alias="mostUrgent")
    benefitted: Optional[str] = Field(default=None, alias="benefitted")

    # Categorisation
    category: Optional[str] = Field(default=None, alias="category")
    category_id: Optional[int] = Field(default=None, alias="CategoryId")
    subcategory: Optional[str] = Field(default=None, alias="Subcategory")
    subcategory_id: Optional[int] = Field(default=None, alias="SubCategoryId")
    dept: Optional[str] = Field(default=None, alias="deptName")
    dept_id: Optional[int] = Field(default=None, alias="DepartmentId")

    # Tagging / assignment / routing
    tagged_to: Optional[str] = Field(default=None, alias="taggedTo")
    tagged_by: Optional[str] = Field(default=None, alias="taggedByName")
    tagged_by_id: Optional[str] = Field(default=None, alias="taggedBy")
    tagged_date: Optional[datetime] = Field(default=None, alias="taggedDate")
    pending_with: Optional[str] = Field(default=None, alias="pendingwithName")
    pending_with_id: Optional[int] = Field(default=None, alias="pendingWith")
    review_authority: Optional[str] = Field(default=None, alias="reviewAuthorityName")
    review_authority_id: Optional[int] = Field(default=None, alias="reviewAuthority")
    all_esc_user: Optional[str] = Field(default=None, alias="vchAllEscUser")
    self_assign: Optional[str] = Field(default=None, alias="isSelfAssign")

    # Lifecycle timestamps / actors
    created_on: Optional[datetime] = Field(default=None, alias="CreatedOn")
    assigned_on: Optional[datetime] = Field(default=None, alias="assignedOn")
    escalation_date: Optional[datetime] = Field(default=None, alias="escalationDate")
    resolved_on: Optional[datetime] = Field(default=None, alias="ResolvedOn")
    resolved_by: Optional[str] = Field(default=None, alias="resolvedBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    last_updated_on: Optional[datetime] = Field(default=None, alias="lastUpdatedOn")
    reopened_by: Optional[str] = Field(default=None, alias="reopenedBy")
    account: Optional[str] = Field(default=None, alias="vchAccount")

    @field_validator("office", mode="before")
    def normalise_office(cls, v):
        """Map to a canonical office name when there's a close match; otherwise
        pass the raw value through unchanged (the dump's office names are a much
        wider domain than the 7-value API ``OFFICE`` map)."""
        if v is None or v in OFFICE.values():
            return v
        closest = get_close_matches(str(v), list(OFFICE.values()), n=1)
        return closest[0] if closest else v

    @field_validator("govt_ticket", mode="before")
    def normalise_govt_ticket(cls, v):
        if isinstance(v, bool) or v is None:
            return v
        s = str(v).strip().lower()
        if s in ("yes", "y", "1", "true"):
            return True
        if s in ("no", "n", "0", "false"):
            return False
        return None

    @field_validator(
        "created_on",
        "tagged_date",
        "assigned_on",
        "escalation_date",
        "resolved_on",
        "last_updated_on",
        mode="before",
    )
    def parse_datetimes(cls, v):
        return _coerce_datetime(v)


class ActionHistory(BaseModel):
    """An action taken on a complaint (from ``t_janasunani_etl_history_pre_data``).

    Source rows key on ``trackingId``; ``ticket_no`` is resolved upstream (via the
    tracking map) and set before/at validation."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    _sanitize_strings = field_validator("*", mode="before")(_strip_nul)

    ticket_no: Optional[str] = Field(default=None, alias="ticketNumber")
    tracking_id: Optional[str] = Field(default=None, alias="trackingId")
    action_taken_by: Optional[str] = None
    action_taken_date: Optional[datetime] = None
    action_taken_remark: Optional[str] = None
    action_status: Optional[str] = None
    complaint_status_with_authority: Optional[str] = None

    @field_validator("action_taken_date", mode="before")
    def parse_datetime(cls, v):
        return _coerce_datetime(v)


def validate(
    items: list[dict], model: type[BaseModel], dict_mode: bool = True
) -> list[dict] | list[BaseModel]:
    """Validate raw source rows against ``model``; log and skip failures.

    Returns dicts keyed by ORM field name (``by_alias=False``) when
    ``dict_mode`` is True, else the validated model instances."""
    logger.info(f"Attempting to validate {len(items)} {model.__name__} records")
    validated = []
    errors = []
    for idx, item in enumerate(items):
        try:
            validated.append(model(**item))
        except Exception as e:  # noqa: BLE001 - collect and report per-row
            errors.append((idx, item, str(e)))
    if errors:
        error_msgs = "\n".join(f"Index {idx}: {err}" for idx, _itm, err in errors)
        logger.error(
            f"Validation failed for {len(errors)} records. Errors:\n{error_msgs}"
        )
    logger.info(f"Validated {len(validated)} {model.__name__} records")
    if dict_mode:
        return [m.model_dump(by_alias=False) for m in validated]
    return validated


def validate_action_history(
    items: list[dict], ticket_no: str, dict_mode: bool = True
) -> list[dict] | list[ActionHistory]:
    """Validate action-history rows, stamping each with the resolved ``ticket_no``."""
    for item in items:
        item["ticketNumber"] = ticket_no
    return validate(items, ActionHistory, dict_mode=dict_mode)
