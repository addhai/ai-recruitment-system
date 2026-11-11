"""add schema validation columns to llm_call_logs

Revision ID: b8c1d4e7f902
Revises: d4e1b0c92a57
Create Date: 2026-11-11 15:30:00.000000

为什么加这三列

`invoke_json` 只保证"返回的是可解析的 JSON"，不保证字段齐全、类型正确。
模型返回 {"score": null} 时，这次调用在埋点里是 status=ok / degraded=false，
节点层兜底成 70 分 —— 埋点完全看不出这次调用不合规。

注入实验证明这类违规不是小事：bad_schema 模式下任务存活率只有 0.400
（超时/5xx/坏 JSON 都是 1.000，因为后三者被 invoke_json 捕获并降级了），
也就是说类型违规是唯一会穿透到驱动层的故障。

但真实流量里它到底发生几次、修正一次能不能救回来，此前一个数字都给不出来。
这三列就是用来把它量出来的：

- schema_valid：首次返回是否结构合规（无契约的 call_site 留空）
- schema_fixed：发起修正调用后是否变合规
- schema_errors：首次校验的错误明细
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c1d4e7f902'
down_revision: Union[str, Sequence[str], None] = 'd4e1b0c92a57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('llm_call_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('schema_valid', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('schema_fixed', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('schema_errors', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('is_repair', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('llm_call_logs', schema=None) as batch_op:
        batch_op.drop_column('is_repair')
        batch_op.drop_column('schema_errors')
        batch_op.drop_column('schema_fixed')
        batch_op.drop_column('schema_valid')
