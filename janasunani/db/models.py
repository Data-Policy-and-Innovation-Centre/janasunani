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
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql.functions import GenericFunction

Base = declarative_base()


class dedup_remark(GenericFunction):  # noqa: N801 - compiles to SQL, named like SQL
    """The remark's contribution to the ``action_history_uniq`` dedup key.

    SQLite indexes the coalesced text directly. PostgreSQL indexes its md5
    digest instead: real remarks run to multiple KB and Postgres btree entries
    cap at ~2.7 KB (raw text broke the first cloud migration with
    ``ProgramLimitExceededError``). Same dedup semantics either way — the
    paired ``IS NULL`` flag keeps NULL distinct from ``''``.
    """

    inherit_cache = True


@compiles(dedup_remark)
def _dedup_remark_sqlite(element, compiler, **kw):
    (col,) = list(element.clauses)
    return f"coalesce({compiler.process(col, **kw)}, '')"


@compiles(dedup_remark, "postgresql")
def _dedup_remark_postgres(element, compiler, **kw):
    (col,) = list(element.clauses)
    return f"md5(coalesce({compiler.process(col, **kw)}, ''))"


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
            func.dedup_remark(action_taken_remark),
            complaint_status_with_authority.is_(None),
            func.coalesce(complaint_status_with_authority, ""),
            unique=True,
        ),
    )


class PipelinePage(Base):
    """A processed document page, exported from the pipeline's artifact DB.

    Faithful copy of the pipeline's ``pages`` table (see
    ``janasunani/pipeline/db.py``); the exporter upserts by ``page_id``.
    Index rule: keys and indexed columns are short identifiers ONLY — the
    unbounded text columns (``extracted_text``, ``redacted_text``) must never
    enter a btree key (Postgres caps entries at ~2.7 KB; see ``dedup_remark``).
    Date columns stay TEXT: they arrive as ISO strings from the artifact DB
    and are provenance metadata, not query dimensions.
    """

    __tablename__ = "pages"

    page_id = Column(String, primary_key=True)
    doc_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    full_path = Column(String, nullable=False)
    page_path = Column(String, nullable=True)
    scanned = Column(String, nullable=True)
    handwritten = Column(String, nullable=True)
    printed = Column(String, nullable=True)
    image_quality = Column(String, nullable=True)
    color_scale = Column(String, nullable=True)
    language = Column(String, nullable=True)
    model = Column(String, nullable=True)
    date_classified = Column(String, nullable=True)
    data_access_date = Column(String, nullable=True)
    extracted_text = Column(String, nullable=True)
    ocr_model = Column(String, nullable=True)
    extracted_date = Column(String, nullable=True)
    redacted_text = Column(String, nullable=True)
    ticket_number = Column(String, nullable=True, index=True)
    page_type = Column(String, nullable=True)
    page_type_class = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("doc_id", "page_number", name="pages_doc_page_uniq"),
    )


class PipelineDocument(Base):
    """A processed document's rollup (summary, category), exported from the
    pipeline's artifact DB ``documents`` table; upserted by ``doc_id``.
    ``grievance``/``summary`` are unbounded text — never index them."""

    __tablename__ = "documents"

    doc_id = Column(String, primary_key=True)
    ticket_number = Column(String, nullable=True, index=True)
    grievance = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    grievance_category = Column(String, nullable=True)


class LiveGrievance(Base):
    """One live demo submission persisted by the serving API.

    Historical complaints stay faithful to the dump in ``complaints``. Demo
    submissions use this sibling table so the API can write/read live results
    without mutating the historical schema. The full API response is kept in
    ``result_json`` to preserve the frozen serving contract exactly.
    """

    __tablename__ = "live_grievances"

    id = Column(String, primary_key=True)
    ticket_no = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, nullable=False)
    submitted_on = Column(DateTime, nullable=False, index=True)
    district = Column(String, nullable=True, index=True)
    source = Column(String, nullable=False)
    category = Column(String, nullable=True, index=True)
    subcategory = Column(String, nullable=True)
    language = Column(String, nullable=True)
    dept = Column(String, nullable=True, index=True)
    office = Column(String, nullable=True)
    routing_method = Column(String, nullable=True)
    routing_confidence = Column(Float, nullable=True)
    result_json = Column(JSON, nullable=False)


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


class GrievanceRedaction(Base):
    """Presidio output for the historical ``complaints.grievance`` text.

    ``complaints.grievance`` is citizen prose that has never met ``pii_tagger``
    — it predates the document pipeline entirely. It is controlled by access
    today, and ROADMAP §3.2 requires it to be controlled by redaction before it
    reaches the lake. This table holds that redaction.

    Written to a side table rather than a column on ``complaints`` so the
    original is never overwritten: a redaction is a derived artifact of one
    analyzer version, and re-running a better analyzer must not have destroyed
    the input. ``analyzer_version`` records which one produced the row, so a
    re-run can be scoped rather than total.
    """

    __tablename__ = "grievance_redactions"

    # ticket_no is the PK, not a surrogate id: exactly one current redaction
    # per complaint, and the upsert conflict target needs to be the identity.
    ticket_no = Column(String, ForeignKey("complaints.ticket_no"), primary_key=True)

    # Text, and deliberately not indexed anywhere. Postgres btree entries cap
    # at roughly 2.7KB and grievance prose exceeds it; an index on this column
    # broke the migration once already (see PipelinePage.extracted_text).
    grievance_redacted = Column(Text, nullable=True)

    redacted_at = Column(DateTime, nullable=False, server_default=func.now())
    analyzer_version = Column(String, nullable=True)


class DedupSignature(Base):
    """MinHash/LSH signature and salted identity keys for one redacted
    grievance (Phase 14 dedup index backfill, #71).

    Built from ``grievance_redactions.grievance_redacted`` **only** — never
    from ``complaints.grievance`` — by
    :mod:`janasunani.pipeline.dedup_index`, which mirrors
    :mod:`janasunani.pipeline.redact_grievance`'s slice-scoped, resumable
    shape one stage downstream. One row per ticket; upserted, so re-running
    the backfill (or running it under different ``index_version``
    parameters) replaces the row rather than accumulating one per run.

    ``dpic-infra`` classified, the same tier as the raw lake: redaction
    lowers exposure but does not declassify what is derived from citizen
    prose, since distinctive phrasing can re-identify a filing after a
    phone number no longer can (ROADMAP §3.2). Deliberately **not** listed
    in ``olap.materialize.LAKE_TABLES`` — see that module's comment next to
    the tuple for why this differs from ``grievance_redactions``.
    """

    __tablename__ = "dedup_signatures"

    # ticket_no is the PK, not a surrogate id: exactly one current signature
    # per complaint, and the upsert conflict target needs to be the
    # identity — same reasoning as GrievanceRedaction.ticket_no.
    ticket_no = Column(String, ForeignKey("complaints.ticket_no"), primary_key=True)

    # Denormalized from complaints (not re-joined on read) so the slice can
    # be reconciled and the grouping pass can block without a join back to
    # complaints — same rationale as PipelinePage.ticket_number.
    district = Column(String, nullable=False, index=True)
    created_year = Column(Integer, nullable=False, index=True)

    # Blocking dimensions computed at index time. `script` partitions
    # Odia-script filings from romanized ones — cross-script recall is
    # unsupported, see pipeline/dedup_index.py's module docstring.
    # `window_index`/`block_key` partition by time within the slice.
    # `block_key` is the literal key the backfill runner buckets LSH
    # candidates on; it is stored rather than recomputed on read so the
    # grouping pass (and any later audit) partitions on exactly what the
    # signature was actually built under, not a re-derivation that could
    # drift from it.
    script = Column(String, nullable=False)
    window_index = Column(Integer, nullable=True)
    block_key = Column(String, nullable=False, index=True)

    # 0 means minhash_signature() abstained on an empty shingle set (see
    # that function's docstring) — `signature` is then NULL, not a
    # comparable-looking sentinel.
    num_shingles = Column(Integer, nullable=False)
    signature = Column(JSON, nullable=True)

    # A separate path from the text columns above (dedup.py module
    # docstring point 3): salted hashes of petitioner_mobile /
    # petitioner_email, computed from those `complaints` columns directly
    # and never from redacted text.
    identity_key_mobile = Column(String, nullable=True, index=True)
    identity_key_email = Column(String, nullable=True, index=True)

    index_version = Column(String, nullable=True)
    indexed_at = Column(DateTime, nullable=False, server_default=func.now())


class DedupGroup(Base):
    """Duplicate-group assignment for one ticket (Phase 14 dedup index
    backfill, #71).

    Written by :mod:`janasunani.pipeline.dedup_index`'s union-find pass over
    verified MinHash/LSH candidate pairs (campaign / near-duplicate text)
    and identity-key matches (resubmission), within one district-year
    slice. Every ticket with a `DedupSignature` row in the slice gets a
    `DedupGroup` row, including singletons (`duplicate_group_id ==
    ticket_no`, `group_size == 1`) — "not a duplicate of anything found" is
    itself the reconciliation signal this table exists to make queryable
    against the slice definition, rather than an absence that looks the
    same as "not processed yet".

    Recomputed and upserted whole on every grouping run, not incrementally:
    a slice's signatures fit comfortably in memory, and a late-arriving
    resubmission can change a group id that already exists, so a partial/
    incremental update would drift from a true recompute.

    Same `dpic-infra` classification and lake exclusion as
    `DedupSignature` — see that class's docstring.
    """

    __tablename__ = "dedup_groups"

    ticket_no = Column(String, ForeignKey("complaints.ticket_no"), primary_key=True)
    district = Column(String, nullable=False, index=True)
    created_year = Column(Integer, nullable=False, index=True)
    block_key = Column(String, nullable=False, index=True)

    duplicate_group_id = Column(String, nullable=False, index=True)
    group_size = Column(Integer, nullable=False)

    index_version = Column(String, nullable=True)
    grouped_at = Column(DateTime, nullable=False, server_default=func.now())
