"""add rerank fields to retrieval_logs

Revision ID: d4e1b0c92a57
Revises: c3d9a71f5b02
Create Date: 2026-11-10 15:50:00.000000

重排是一次额外的外部 API 调用，成本与延迟必须能单独归因，
否则算总账时看不出来（检索埋点里只有 bm25_ms / vector_ms）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e1b0c92a57'
down_revision: Union[str, Sequence[str], None] = 'c3d9a71f5b02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('retrieval_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rerank_applied', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('rerank_ms', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('retrieval_logs', schema=None) as batch_op:
        batch_op.drop_column('rerank_ms')
        batch_op.drop_column('rerank_applied')
