"""add cooldowns

Revision ID: 7f3a91c2d4e8
Revises: 830983e1bfd8

跨副本 / 跨重启共享的「冷却期」记录（登录限流 + LLM 熔断），
见 src/services/shared_cooldown.py 的取舍说明。

复合主键 (kind, key) 天然保证每个 key 至多一行；until 建索引是因为
写入时会顺手删除已过期行、并在超过行数上限时按 until 最早的先删。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3a91c2d4e8'
down_revision: Union[str, Sequence[str], None] = '830983e1bfd8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'cooldowns',
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('until', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('kind', 'key'),
    )
    with op.batch_alter_table('cooldowns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cooldowns_until'), ['until'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('cooldowns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cooldowns_until'))
    op.drop_table('cooldowns')
