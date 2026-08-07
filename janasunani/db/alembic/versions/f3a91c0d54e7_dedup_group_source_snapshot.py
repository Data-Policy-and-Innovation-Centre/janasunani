"""add source snapshot provenance to dedup groups

Issue #137 keeps the historical dedup backfill on the OLTP redaction path but
requires every persisted group assignment to say which source snapshot it
describes. dedup_signatures.source_record_digest is computed when a row is
indexed; a group snapshot aggregates those stored values, rather than current
OLTP values, so stale signatures cannot be relabelled as fresh. The nullable
columns are deliberate: an existing production index cannot be truthfully
backfilled by a schema migration alone. Re-run the dedup-index command to
populate them; downstream duplicate analytics must reject legacy NULL
provenance.

Revision ID: f3a91c0d54e7
Revises: e59fb4410dd6
Create Date: 2026-08-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a91c0d54e7"
down_revision: Union[str, None] = "e59fb4410dd6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dedup_signatures", sa.Column("source_record_digest", sa.String(), nullable=True)
    )
    op.add_column("dedup_groups", sa.Column("source_name", sa.String(), nullable=True))
    op.add_column(
        "dedup_groups", sa.Column("source_snapshot_id", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dedup_groups", "source_snapshot_id")
    op.drop_column("dedup_groups", "source_name")
    op.drop_column("dedup_signatures", "source_record_digest")
