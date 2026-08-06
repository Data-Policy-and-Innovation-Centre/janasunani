"""grievance_redactions table

Holds the Presidio redaction of the historical ``complaints.grievance`` text,
which predates the document pipeline and has never met ``pii_tagger``
(ROADMAP §3.2).

A side table rather than a column on ``complaints``: the original must survive
a re-run under a better analyzer, so the redaction is stored as a derived
artifact stamped with the version that produced it.

``grievance_redacted`` is deliberately unindexed. Postgres btree entries cap at
roughly 2.7 KB and grievance prose exceeds it — an index on a text column of
this kind broke the first cloud migration with ``ProgramLimitExceededError``.

Revision ID: b7c1e94af203
Revises: 8d4ab4f6d5e2
Create Date: 2026-08-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1e94af203"
down_revision: Union[str, None] = "8d4ab4f6d5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grievance_redactions",
        sa.Column("ticket_no", sa.String(), nullable=False),
        sa.Column("grievance_redacted", sa.Text(), nullable=True),
        sa.Column(
            "redacted_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("analyzer_version", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_no"], ["complaints.ticket_no"]),
        sa.PrimaryKeyConstraint("ticket_no"),
    )


def downgrade() -> None:
    op.drop_table("grievance_redactions")
