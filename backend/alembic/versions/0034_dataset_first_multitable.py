"""dataset-first inversion: multitable members + authoritative task dataset_id

Revision ID: 0034_dataset_first_multitable
Revises: 0033_add_reproducibility_columns
Create Date: 2026-06-30 23:00:00.000000

数据集优先流程改造 阶段1:
- 新增 dataset_version_tables(版本多表成员,无 FK,仿 job_inputs)
- ingest_tasks.dataset_id 转权威(NOT NULL,先回填)
- upload_records.dataset_id(nullable,可追溯)
- 回填:每个现存 dataset_versions 生成一行成员(table_name='data')
面向 adp_gov 克隆库,可回退。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0034_dataset_first_multitable"
down_revision: Union[str, None] = "0033_add_reproducibility_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_version_tables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="parquet"),
        sa.Column("rows", sa.BigInteger(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("schema_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("schema_variant", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "dataset_version_id", "table_name", name="uq_dvt_version_table"
        ),
    )
    op.create_index("ix_dvt_version", "dataset_version_tables", ["dataset_version_id"])

    # upload_records 可追溯列
    op.add_column(
        "upload_records", sa.Column("dataset_id", sa.String(), nullable=True)
    )

    # 回填成员:每个现存版本一行(table_name='data',继承版本现值)
    op.execute(
        """
        INSERT INTO dataset_version_tables
            (id, dataset_version_id, table_name, storage_uri, format,
             rows, size, schema_snapshot, schema_variant, created_at)
        SELECT
            'dvt-' || substr(md5(random()::text || dv.id), 1, 6),
            dv.id, 'data', dv.storage_uri, dv.format,
            dv.rows, dv.size, dv.schema_snapshot, dv.schema_variant, now()
        FROM dataset_versions dv
        WHERE NOT EXISTS (
            SELECT 1 FROM dataset_version_tables t
            WHERE t.dataset_version_id = dv.id
        )
        """
    )

    # ingest_tasks.dataset_id 转权威:先给 NULL 行回填(经 Job 间接定位),再 NOT NULL
    op.execute(
        """
        UPDATE ingest_tasks t SET dataset_id = sub.dataset_id
        FROM (
            SELECT j.ingest_task_id AS task_id, dv.dataset_id AS dataset_id
            FROM jobs j
            JOIN dataset_versions dv ON dv.produced_by_job_id = j.id
            WHERE j.type = 'ingest' AND j.ingest_task_id IS NOT NULL
        ) sub
        WHERE t.id = sub.task_id AND t.dataset_id IS NULL
        """
    )
    # 仍为 NULL 的(无产出历史)填空串占位,保证可加 NOT NULL
    op.execute("UPDATE ingest_tasks SET dataset_id = '' WHERE dataset_id IS NULL")
    op.alter_column("ingest_tasks", "dataset_id", nullable=False)


def downgrade() -> None:
    op.alter_column("ingest_tasks", "dataset_id", nullable=True)
    op.drop_column("upload_records", "dataset_id")
    op.drop_index("ix_dvt_version", table_name="dataset_version_tables")
    op.drop_table("dataset_version_tables")
