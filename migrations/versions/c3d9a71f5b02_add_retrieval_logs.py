"""add retrieval_logs

Revision ID: c3d9a71f5b02
Revises: 7f3a91c2d4e8

知识库检索埋点表（见 src/models/database.py 的 RetrievalLog）：
记录每次检索的问句、BM25/向量两路召回结果、分段耗时与当时的检索口径。

索引设计：
- created_at：保留期清理按时间扫描；
- query_hash：高频问题聚合（GROUP BY）按它分组；
- id：与模型里 index=True 的声明保持一致（虽然主键本身已唯一，
  但模型声明了就建出来，避免模型与库结构出现无谓的差异）。

表会随检索量线性增长，靠 purge_old_retrieval_logs 按保留期清理，
不清理迟早撑爆磁盘。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d9a71f5b02'
down_revision: Union[str, Sequence[str], None] = '7f3a91c2d4e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'retrieval_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('query_text', sa.Text(), nullable=True),
        sa.Column('query_hash', sa.String(length=64), nullable=True),
        sa.Column('top_k', sa.Integer(), nullable=True),
        sa.Column('bm25_titles', sa.JSON(), nullable=True),
        sa.Column('vector_titles', sa.JSON(), nullable=True),
        sa.Column('merged_titles', sa.JSON(), nullable=True),
        sa.Column('bm25_ms', sa.Float(), nullable=True),
        sa.Column('vector_ms', sa.Float(), nullable=True),
        sa.Column('total_ms', sa.Float(), nullable=True),
        sa.Column('mode', sa.String(length=32), nullable=True),
        sa.Column('chunk_size', sa.Integer(), nullable=True),
        sa.Column('chunk_overlap', sa.Integer(), nullable=True),
        sa.Column('embedding_model', sa.String(length=64), nullable=True),
        sa.Column('degraded', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('retrieval_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_retrieval_logs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_retrieval_logs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_retrieval_logs_query_hash'), ['query_hash'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('retrieval_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_retrieval_logs_id'))
        batch_op.drop_index(batch_op.f('ix_retrieval_logs_created_at'))
        batch_op.drop_index(batch_op.f('ix_retrieval_logs_query_hash'))
    op.drop_table('retrieval_logs')
