"""add_operator_effect_demo

Revision ID: 0030_add_operator_effect_demo
Revises: c616c2fa4190
Create Date: 2026-06-30 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0030_add_operator_effect_demo'
down_revision: Union[str, None] = 'c616c2fa4190'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('operators', sa.Column('effect_demo', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('operators', 'effect_demo')
