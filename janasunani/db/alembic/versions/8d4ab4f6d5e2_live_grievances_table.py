"""live grievances table

Revision ID: 8d4ab4f6d5e2
Revises: dd0eea37cac6
Create Date: 2026-07-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d4ab4f6d5e2'
down_revision: Union[str, None] = 'dd0eea37cac6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'live_grievances',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('ticket_no', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('submitted_on', sa.DateTime(), nullable=False),
        sa.Column('district', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('subcategory', sa.String(), nullable=True),
        sa.Column('language', sa.String(), nullable=True),
        sa.Column('dept', sa.String(), nullable=True),
        sa.Column('office', sa.String(), nullable=True),
        sa.Column('routing_method', sa.String(), nullable=True),
        sa.Column('routing_confidence', sa.Float(), nullable=True),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_live_grievances_ticket_no', 'live_grievances', ['ticket_no'], unique=True)
    op.create_index('ix_live_grievances_submitted_on', 'live_grievances', ['submitted_on'])
    op.create_index('ix_live_grievances_district', 'live_grievances', ['district'])
    op.create_index('ix_live_grievances_category', 'live_grievances', ['category'])
    op.create_index('ix_live_grievances_dept', 'live_grievances', ['dept'])


def downgrade() -> None:
    op.drop_index('ix_live_grievances_dept', table_name='live_grievances')
    op.drop_index('ix_live_grievances_category', table_name='live_grievances')
    op.drop_index('ix_live_grievances_district', table_name='live_grievances')
    op.drop_index('ix_live_grievances_submitted_on', table_name='live_grievances')
    op.drop_index('ix_live_grievances_ticket_no', table_name='live_grievances')
    op.drop_table('live_grievances')
