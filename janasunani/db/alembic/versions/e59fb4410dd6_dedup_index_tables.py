"""dedup index tables

Two tables for the Phase 14 dedup index backfill (#71), written by
``janasunani.pipeline.dedup_index``:

- ``dedup_signatures`` — one MinHash/LSH signature plus salted identity keys
  per redacted grievance, built from ``grievance_redactions.grievance_redacted``
  only. ``signature`` is a JSON array of the MinHash integers (portable across
  SQLite/Postgres, same approach as ``live_grievances.result_json``).
- ``dedup_groups`` — one duplicate-group assignment per ticket, from the
  union-find pass over verified LSH candidate pairs and identity-key matches.

Both are ``dpic-infra`` classified, same as the raw lake: redaction lowers
exposure but does not declassify what is derived from citizen prose (ROADMAP
§3.2). Deliberately not wired into ``olap.materialize.LAKE_TABLES`` — see the
comment on that tuple.

Index rule: no unbounded text column here (signatures are integers, keys are
hashes), so nothing in this revision risks the ``ProgramLimitExceededError``
that motivated leaving ``grievance_redacted``/``extracted_text`` unindexed.

Revision ID: e59fb4410dd6
Revises: b7c1e94af203
Create Date: 2026-08-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e59fb4410dd6"
down_revision: Union[str, None] = "b7c1e94af203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dedup_signatures",
        sa.Column("ticket_no", sa.String(), nullable=False),
        sa.Column("district", sa.String(), nullable=False),
        sa.Column("created_year", sa.Integer(), nullable=False),
        sa.Column("script", sa.String(), nullable=False),
        sa.Column("window_index", sa.Integer(), nullable=True),
        sa.Column("block_key", sa.String(), nullable=False),
        sa.Column("num_shingles", sa.Integer(), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=True),
        sa.Column("identity_key_mobile", sa.String(), nullable=True),
        sa.Column("identity_key_email", sa.String(), nullable=True),
        sa.Column("index_version", sa.String(), nullable=True),
        sa.Column(
            "indexed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_no"], ["complaints.ticket_no"]),
        sa.PrimaryKeyConstraint("ticket_no"),
    )
    op.create_index(
        "ix_dedup_signatures_district", "dedup_signatures", ["district"]
    )
    op.create_index(
        "ix_dedup_signatures_created_year", "dedup_signatures", ["created_year"]
    )
    op.create_index(
        "ix_dedup_signatures_block_key", "dedup_signatures", ["block_key"]
    )
    op.create_index(
        "ix_dedup_signatures_identity_key_mobile",
        "dedup_signatures",
        ["identity_key_mobile"],
    )
    op.create_index(
        "ix_dedup_signatures_identity_key_email",
        "dedup_signatures",
        ["identity_key_email"],
    )

    op.create_table(
        "dedup_groups",
        sa.Column("ticket_no", sa.String(), nullable=False),
        sa.Column("district", sa.String(), nullable=False),
        sa.Column("created_year", sa.Integer(), nullable=False),
        sa.Column("block_key", sa.String(), nullable=False),
        sa.Column("duplicate_group_id", sa.String(), nullable=False),
        sa.Column("group_size", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.String(), nullable=True),
        sa.Column(
            "grouped_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_no"], ["complaints.ticket_no"]),
        sa.PrimaryKeyConstraint("ticket_no"),
    )
    op.create_index("ix_dedup_groups_district", "dedup_groups", ["district"])
    op.create_index(
        "ix_dedup_groups_created_year", "dedup_groups", ["created_year"]
    )
    op.create_index("ix_dedup_groups_block_key", "dedup_groups", ["block_key"])
    op.create_index(
        "ix_dedup_groups_duplicate_group_id",
        "dedup_groups",
        ["duplicate_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dedup_groups_duplicate_group_id", table_name="dedup_groups")
    op.drop_index("ix_dedup_groups_block_key", table_name="dedup_groups")
    op.drop_index("ix_dedup_groups_created_year", table_name="dedup_groups")
    op.drop_index("ix_dedup_groups_district", table_name="dedup_groups")
    op.drop_table("dedup_groups")

    op.drop_index(
        "ix_dedup_signatures_identity_key_email", table_name="dedup_signatures"
    )
    op.drop_index(
        "ix_dedup_signatures_identity_key_mobile", table_name="dedup_signatures"
    )
    op.drop_index("ix_dedup_signatures_block_key", table_name="dedup_signatures")
    op.drop_index(
        "ix_dedup_signatures_created_year", table_name="dedup_signatures"
    )
    op.drop_index("ix_dedup_signatures_district", table_name="dedup_signatures")
    op.drop_table("dedup_signatures")
