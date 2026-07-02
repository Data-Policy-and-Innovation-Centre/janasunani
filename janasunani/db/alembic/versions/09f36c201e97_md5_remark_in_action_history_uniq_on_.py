"""md5 remark in action_history_uniq on postgres

PostgreSQL caps btree index entries at ~2.7 KB; real ``action_taken_remark``
values run to multiple KB, so the raw functional dedup index from the baseline
cannot hold the data (``ProgramLimitExceededError`` on the first cloud
migration). Recreate it with the remark md5-digested — same dedup semantics
(the paired ``IS NULL`` flag still distinguishes NULL from ``''``).

SQLite has no such limit (and no ``md5()``): no-op there. The model-side
definition compiles per-dialect via ``dedup_remark`` in ``db/models.py``.

Revision ID: 09f36c201e97
Revises: daa6c55ad888
Create Date: 2026-07-02 16:08:03.223909
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '09f36c201e97'
down_revision: Union[str, None] = 'daa6c55ad888'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_COLUMNS = """
    (ticket_no IS NULL), coalesce(ticket_no, ''),
    (action_taken_by IS NULL), coalesce(action_taken_by, ''),
    (action_status IS NULL), coalesce(action_status, ''),
    (action_taken_remark IS NULL), {remark_expr},
    (complaint_status_with_authority IS NULL),
    coalesce(complaint_status_with_authority, '')
"""


def _recreate_index(remark_expr: str) -> None:
    op.drop_index("action_history_uniq", table_name="action_history")
    op.execute(
        "CREATE UNIQUE INDEX action_history_uniq ON action_history ("
        + _INDEX_COLUMNS.format(remark_expr=remark_expr)
        + ")"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _recreate_index("md5(coalesce(action_taken_remark, ''))")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _recreate_index("coalesce(action_taken_remark, '')")
