"""SQLAlchemy ORM models for the grievance complaint store (``grievance.db``).

The ``Complaint`` and ``ActionHistory`` tables model the **full column set** of
the source ``mysqldump`` (database ``sociomatics_ticket``, tables
``t_janasunani_etl_pre_data`` and ``t_janasunani_etl_history_pre_data``),
adapted from the source's mixed-case / Hungarian-prefixed / occasionally
mis-spelled column names to a clean snake_case convention.

The raw-source → ORM field mapping lives in one place — the Pydantic ``Field``
aliases in :mod:`janasunani.ingestion.schemas` — so the migration loaders and the
API ingestion all translate through the same map. Paired ``(id, name)`` source
columns are modelled as both a numeric ``*_id`` column and the human-readable
name column (e.g. ``intDistId`` -> ``district_id`` and ``districtName`` ->
``district``).

Columns are intentionally nullable (except ``ticket_no``): the cold-start dump
holds historical data of varying completeness, and dropping rows on a missing
non-key field would lose records.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class District(Base):
    """Administrative district (name + unique numeric id)."""

    __tablename__ = "districts"

    id = Column(Integer, primary_key=True)
    dist_name = Column(String, nullable=False)
    dist_id = Column(Integer, nullable=False, unique=True)

    __table_args__ = (UniqueConstraint("dist_id", name="dist_id_uniq"),)


class Complaint(Base):
    """A grievance complaint record.

    Models all 56 columns of ``t_janasunani_etl_pre_data`` plus four
    ingestion-populated document columns (``local_document_path``,
    ``document_downloaded``, ``document_download_date``,
    ``document_download_error``) that are **not** in the dump — they are written
    later by the document ingestion pipeline.
    """

    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True)

    # Identity / linkage
    ticket_no = Column(String, unique=True, nullable=False)  # ticketNumber
    tracking_id = Column(String, nullable=True)              # trackingId (join key to action history)
    created_year = Column(Integer, nullable=True)            # createdYear

    # Petitioner
    petitioner_name = Column(String, nullable=True)          # petitionerName
    petitioner_mobile = Column(String, nullable=True)        # petitionerMobile
    petitioner_email = Column(String, nullable=True)         # petitionerEmail
    petitioner_gender = Column(String, nullable=True)        # genderName
    gender_id = Column(Integer, nullable=True)               # gender

    # Grievance body / document
    grievance = Column(String, nullable=True)                # grievanceSubject
    document_url = Column(String, nullable=True)             # Document

    # Office / receiver
    office = Column(String, nullable=True)                   # officeNAme
    office_id = Column(Integer, nullable=True)               # intOfficeId
    received_by = Column(String, nullable=True)              # RecievedByOfficerName
    received_by_id = Column(Integer, nullable=True)          # RecievedBy

    # Geography
    district = Column(String, nullable=True)                 # districtName
    district_id = Column(Integer, nullable=True)             # intDistId
    block = Column(String, nullable=True)                    # blockName
    block_id = Column(Integer, nullable=True)                # intBlockId
    state = Column(String, nullable=True)                    # stateName
    state_id = Column(Integer, nullable=True)                # intStateId
    address = Column(String, nullable=True)                  # Address

    # Mode / disability
    mode = Column(String, nullable=True)                     # modeName
    mode_id = Column(Integer, nullable=True)                 # Mode
    disability = Column(String, nullable=True)               # disbilityName (sic)
    disability_type = Column(String, nullable=True)          # disabilityType

    # Status
    status = Column(String, nullable=True)                   # StatusName
    complaint_status_id = Column(Integer, nullable=True)     # intCompliantStatusId (sic)
    govt_ticket = Column(Boolean, nullable=True)             # govtTicket (Yes/No -> bool)
    transfer_status = Column(String, nullable=True)          # transferStatus
    urgent = Column(String, nullable=True)                   # mostUrgent
    benefitted = Column(String, nullable=True)               # benefitted

    # Categorisation
    category = Column(String, nullable=True)                 # category
    category_id = Column(Integer, nullable=True)             # CategoryId
    subcategory = Column(String, nullable=True)              # Subcategory
    subcategory_id = Column(Integer, nullable=True)          # SubCategoryId
    dept = Column(String, nullable=True)                     # deptName
    dept_id = Column(Integer, nullable=True)                 # DepartmentId

    # Tagging / assignment / routing
    tagged_to = Column(String, nullable=True)                # taggedTo
    tagged_by = Column(String, nullable=True)                # taggedByName
    tagged_by_id = Column(String, nullable=True)             # taggedBy
    tagged_date = Column(DateTime, nullable=True)            # taggedDate
    pending_with = Column(String, nullable=True)             # pendingwithName
    pending_with_id = Column(Integer, nullable=True)         # pendingWith
    review_authority = Column(String, nullable=True)         # reviewAuthorityName
    review_authority_id = Column(Integer, nullable=True)     # reviewAuthority
    all_esc_user = Column(String, nullable=True)             # vchAllEscUser
    self_assign = Column(String, nullable=True)              # isSelfAssign

    # Lifecycle timestamps / actors
    created_on = Column(DateTime, nullable=True)             # CreatedOn
    assigned_on = Column(DateTime, nullable=True)            # assignedOn
    escalation_date = Column(DateTime, nullable=True)        # escalationDate (treated as overdue date)
    resolved_on = Column(DateTime, nullable=True)            # ResolvedOn
    resolved_by = Column(String, nullable=True)              # resolvedBy
    updated_by = Column(String, nullable=True)               # updatedBy
    last_updated_on = Column(DateTime, nullable=True)        # lastUpdatedOn
    reopened_by = Column(String, nullable=True)              # reopenedBy
    account = Column(String, nullable=True)                  # vchAccount (CMO/Naveen Pattnaik social handle)

    # --- Not in the dump: populated by the document ingestion pipeline ---
    local_document_path = Column(String, nullable=True)
    document_downloaded = Column(Boolean, default=False)
    document_download_date = Column(DateTime, nullable=True)
    document_download_error = Column(String, nullable=True)

    __table_args__ = (UniqueConstraint("ticket_no", name="ticket_no_uniq"),)


class ActionHistory(Base):
    """An action taken on a complaint.

    Models ``t_janasunani_etl_history_pre_data``. The source rows key on
    ``trackingId``; the migration resolves it to the owning complaint's
    ``ticket_no`` (the FK) via the tracking map, and also stores the raw
    ``tracking_id`` for traceability.
    """

    __tablename__ = "action_history"

    id = Column(Integer, primary_key=True)
    ticket_no = Column(String, ForeignKey("complaints.ticket_no"))
    tracking_id = Column(String, nullable=True)              # trackingId (raw source key)
    complaint = relationship("Complaint", backref="action_history")
    action_taken_date = Column(DateTime, nullable=True)      # action_taken_date
    action_taken_by = Column(String, nullable=True)          # action_taken_by
    action_status = Column(String, nullable=True)            # action_status
    action_taken_remark = Column(String, nullable=True)      # action_taken_remark
    complaint_status_with_authority = Column(String, nullable=True)  # complaint_status_with_authority

    __table_args__ = (
        Index(
            "action_history_uniq",
            ticket_no.is_(None),
            func.coalesce(ticket_no, ""),
            action_taken_by.is_(None),
            func.coalesce(action_taken_by, ""),
            action_status.is_(None),
            func.coalesce(action_status, ""),
            action_taken_remark.is_(None),
            func.coalesce(action_taken_remark, ""),
            complaint_status_with_authority.is_(None),
            func.coalesce(complaint_status_with_authority, ""),
            unique=True,
        ),
    )


class APIRequestTracking(Base):
    """Track which complaint API request combinations have been processed."""

    __tablename__ = "api_request_tracking"

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    dist_id = Column(Integer, nullable=False)
    status = Column(Integer, nullable=False)
    office = Column(Integer, nullable=False)
    last_successful_fetch = Column(DateTime, nullable=True)
    records_count = Column(Integer, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "year", "dist_id", "status", "office", name="api_request_uniq"
        ),
    )


class ActionHistoryAPIRequestTracking(Base):
    """Track which action history API request combinations have been processed."""

    __tablename__ = "action_history_api_request_tracking"

    id = Column(Integer, primary_key=True)
    ticket_no = Column(String, ForeignKey("complaints.ticket_no"))
    complaint = relationship(
        "Complaint", backref="action_history_api_request_tracking"
    )
    last_successful_fetch = Column(DateTime, nullable=True)
    records_count = Column(Integer, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("ticket_no", name="action_history_api_request_uniq"),
    )
