"""add token_version to users

Revision ID: deb230aa5caa
Revises: 40046ce16ad0
Create Date: 2026-10-09

令牌撤销用的版本号：令牌里带签发时的版本，与库中不一致即失效（改密时 +1）。

用版本号而不是时间戳，是为了绕开精度问题——时间戳方案下"同一秒内签发的旧令牌"
会逃过撤销；把精度提到微秒又要求 iat 用浮点，与 JWT 的秒级惯例不符。
版本号不含时钟，判定精确。

server_default='0'：已有行必须能立刻取到值，否则 NOT NULL 加列会失败。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'deb230aa5caa'
down_revision: Union[str, Sequence[str], None] = '40046ce16ad0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer(),
                                      nullable=False, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
