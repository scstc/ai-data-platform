"""add_operators_table

Revision ID: c616c2fa4190
Revises: 0029_drop_sensitivity_level
Create Date: 2026-06-29 23:19:30.737181

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c616c2fa4190'
down_revision: Union[str, None] = '0029_drop_sensitivity_level'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'operators',
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('zh_label', sa.String(length=128), nullable=False),
        sa.Column('summary_en', sa.Text(), nullable=True),
        sa.Column('summary_zh', sa.Text(), nullable=True),
        sa.Column('desc_en', sa.Text(), nullable=True),
        sa.Column('desc_zh', sa.Text(), nullable=True),
        sa.Column('zh_usage_tip', sa.Text(), nullable=True),
        sa.Column('scenario_group', sa.String(length=64), nullable=True),
        sa.Column('resource_class', sa.String(length=32), nullable=False, server_default='cpu'),
        sa.Column('modality', sa.JSON(), nullable=True),
        sa.Column('frameworks', sa.JSON(), nullable=True),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('example', sa.Text(), nullable=True),
        sa.Column('detail_page', sa.String(length=256), nullable=True),
        sa.Column('recommend', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('runnable', sa.String(length=32), nullable=False, server_default='ready'),
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('name')
    )
    op.create_index('ix_operators_category', 'operators', ['category'])
    op.create_index('ix_operators_scenario_group', 'operators', ['scenario_group'])
    op.create_index('ix_operators_resource_class', 'operators', ['resource_class'])


def downgrade() -> None:
    op.drop_index('ix_operators_resource_class', table_name='operators')
    op.drop_index('ix_operators_scenario_group', table_name='operators')
    op.drop_index('ix_operators_category', table_name='operators')
    op.drop_table('operators')
