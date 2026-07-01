"""custom operators: user-uploaded operator columns on operators table

Revision ID: 0035_custom_operators
Revises: 0034_dataset_first_multitable
Create Date: 2026-07-01 00:00:00.000000

算子市场:自定义算子上传(治理整改分支)。
- operators.is_custom:区分内置(data-juicer 快照)与用户上传
- operators.source_object_key:上传的 .py 源码相对路径(相对 UPLOAD_DIR/custom_operators/)
- operators.created_by:上传者 username(内置算子为空)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0035_custom_operators"
down_revision: Union[str, None] = "0034_dataset_first_multitable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "operators",
        sa.Column(
            "is_custom",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "operators",
        sa.Column("source_object_key", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "operators",
        sa.Column("created_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("operators", "created_by")
    op.drop_column("operators", "source_object_key")
    op.drop_column("operators", "is_custom")
