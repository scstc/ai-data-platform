"""add dataset_version train_type and schema_variant (治理整改 G1)

Revision ID: 0031_add_train_type_metadata
Revises: 0030_add_operator_effect_demo
Create Date: 2026-06-30 00:31:00.000000

数据治理整改方案 阶段1 / G1:为 DatasetVersion 增训练用途元数据。
训练平台据此按 train_type 过滤可用数据集(见 docs/training-dataset-format-spec.md §4)。
两列均 nullable、无 server_default,存量行留空,可安全回退。
record_count 复用现有 rows 列(语义等价),不新增冗余列,在 API 层映射输出。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0031_add_train_type_metadata'
down_revision: Union[str, None] = '0030_add_operator_effect_demo'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 训练用途:pretrain/sft/distill/dpo/rlhf/eval/custom;校验在 Pydantic 层,DB 留 free-string
    op.add_column(
        'dataset_versions',
        sa.Column('train_type', sa.String(), nullable=True),
    )
    # 该 train_type 的具体 schema 变体:text/alpaca/messages/preference/prompt_only/eval
    op.add_column(
        'dataset_versions',
        sa.Column('schema_variant', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dataset_versions', 'schema_variant')
    op.drop_column('dataset_versions', 'train_type')
