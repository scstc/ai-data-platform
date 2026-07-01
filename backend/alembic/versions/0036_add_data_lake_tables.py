"""add data lake tables

Revision ID: 0036_add_data_lake_tables
Revises: 0035_custom_operators
Create Date: 2026-07-01 20:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0036_add_data_lake_tables'
down_revision: str | None = '0035_custom_operators'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 创建数据湖容器表
    op.create_table('data_lakes',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('source_category', sa.String(), nullable=False),
    sa.Column('datasource_id', sa.String(), nullable=True),
    sa.Column('ingest_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('owner', sa.String(), nullable=False),
    sa.Column('creator', sa.String(), nullable=False),
    sa.Column('dept_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    # 创建数据湖快照表
    op.create_table('data_lake_snapshots',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('lake_id', sa.String(), nullable=False),
    sa.Column('source_version', sa.String(), nullable=False),
    sa.Column('storage_uri', sa.String(), nullable=False),
    sa.Column('storage_format', sa.String(), nullable=False),
    sa.Column('data_category', sa.String(), nullable=False),
    sa.Column('upload_channel', sa.String(), nullable=False),
    sa.Column('source_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('rows', sa.BigInteger(), nullable=True),
    sa.Column('size', sa.BigInteger(), nullable=True),
    sa.Column('ingest_task_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lake_id', 'source_version', name='uq_lake_source_version')
    )
    op.create_index('ix_dls_lake_id', 'data_lake_snapshots', ['lake_id'], unique=False)
    op.create_index('ix_dls_ingest_task', 'data_lake_snapshots', ['ingest_task_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_dls_ingest_task', table_name='data_lake_snapshots')
    op.drop_index('ix_dls_lake_id', table_name='data_lake_snapshots')
    op.drop_table('data_lake_snapshots')
    op.drop_table('data_lakes')
