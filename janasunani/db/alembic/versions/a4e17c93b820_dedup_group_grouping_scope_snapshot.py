"""add grouping-scope provenance to dedup groups

f3a91c0d54e7 gave every group row the snapshot of the source records it
describes, which was complete while a grouping run could only ever cover one
district-year. Corpus-wide runs (#317) break that completeness: a ticket
outside a row's district-year can bridge two otherwise separate groups inside
it, so changing that outside ticket and regrouping changes the slice's
distinct-problem count while its source_snapshot_id -- which hashes only the
slice -- stays identical. Two artifacts with different numbers would then
present the same provenance, and the serving layer uses exactly that value to
reject mixed outputs.

source_snapshot_id keeps its meaning and stays the value a slice-scoped
consumer can recompute from the lake. grouping_scope_snapshot_id is the
digest of every record the grouping run actually read. A consumer cannot
reproduce it from one slice, and is not meant to: it exists so rows from
different group assignments are detectably different rather than silently
combinable.

Nullable for the same reason as f3a91c0d54e7 -- an existing index cannot be
truthfully backfilled by a migration, only by re-running the command.

Revision ID: a4e17c93b820
Revises: f3a91c0d54e7
Create Date: 2026-08-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4e17c93b820"
down_revision: Union[str, None] = "f3a91c0d54e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dedup_groups",
        sa.Column("grouping_scope_snapshot_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dedup_groups", "grouping_scope_snapshot_id")
