"""pipeline pages and documents tables

OLTP landing tables for the document-processing pipeline's outputs (the
exporter upserts from the pipeline's SQLite artifact DB). Index rule: keys
and indexed columns are short identifiers only — the unbounded text columns
(extracted_text, redacted_text, grievance, summary) never enter a btree key
(Postgres caps entries at ~2.7 KB; see revision 09f36c201e97).

Revision ID: dd0eea37cac6
Revises: 09f36c201e97
Create Date: 2026-07-02 23:11:19.014370
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd0eea37cac6'
down_revision: Union[str, None] = '09f36c201e97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pages',
        sa.Column('page_id', sa.String(), nullable=False),
        sa.Column('doc_id', sa.String(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('full_path', sa.String(), nullable=False),
        sa.Column('page_path', sa.String(), nullable=True),
        sa.Column('scanned', sa.String(), nullable=True),
        sa.Column('handwritten', sa.String(), nullable=True),
        sa.Column('printed', sa.String(), nullable=True),
        sa.Column('image_quality', sa.String(), nullable=True),
        sa.Column('color_scale', sa.String(), nullable=True),
        sa.Column('language', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('date_classified', sa.String(), nullable=True),
        sa.Column('data_access_date', sa.String(), nullable=True),
        sa.Column('extracted_text', sa.String(), nullable=True),
        sa.Column('ocr_model', sa.String(), nullable=True),
        sa.Column('extracted_date', sa.String(), nullable=True),
        sa.Column('redacted_text', sa.String(), nullable=True),
        sa.Column('ticket_number', sa.String(), nullable=True),
        sa.Column('page_type', sa.String(), nullable=True),
        sa.Column('page_type_class', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('page_id'),
        sa.UniqueConstraint('doc_id', 'page_number', name='pages_doc_page_uniq'),
    )
    op.create_index('ix_pages_doc_id', 'pages', ['doc_id'])
    op.create_index('ix_pages_ticket_number', 'pages', ['ticket_number'])

    op.create_table(
        'documents',
        sa.Column('doc_id', sa.String(), nullable=False),
        sa.Column('ticket_number', sa.String(), nullable=True),
        sa.Column('grievance', sa.String(), nullable=True),
        sa.Column('summary', sa.String(), nullable=True),
        sa.Column('grievance_category', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('doc_id'),
    )
    op.create_index('ix_documents_ticket_number', 'documents', ['ticket_number'])


def downgrade() -> None:
    op.drop_index('ix_documents_ticket_number', table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_pages_ticket_number', table_name='pages')
    op.drop_index('ix_pages_doc_id', table_name='pages')
    op.drop_table('pages')
