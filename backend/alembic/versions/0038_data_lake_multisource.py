"""data lake multi-source: 数据湖改为多源汇聚容器,类型下移到快照层。

Revision ID: 0038_data_lake_multisource
Revises: 0037_seed_data_lake_menu
Create Date: 2026-07-01

治理文档语义调整("多源统一数据湖",见 docs/数据治理.md §2.1):
- 一个数据湖可以汇聚数据库 + 对象存储 + HDFS + 本地上传等多种来源的快照
- 类型和来源属于每一次接入(快照),不属于湖本身

改造:
- data_lakes: 去掉 source_category / datasource_id / ingest_config
  (湖=纯容器,不绑定单一类型/单一数据源)
- data_lake_snapshots: 新增 datasource_id
  (记录本次接入的具体数据源,本地上传/API 推送为 NULL)

此改造是破坏性 schema 变更;在 adp_gov 治理整改库执行,dev 库不动。
现有 data_lakes 行(若有)在 downgrade 时无法恢复 source_category 值,
故 downgrade 用 server_default 'database' 兜底(仅供本地回滚,生产禁用)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_data_lake_multisource"
down_revision: str | None = "0037_seed_data_lake_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # data_lakes: 湖=纯容器,去掉类型和数据源绑定
    op.drop_column("data_lakes", "source_category")
    op.drop_column("data_lakes", "datasource_id")
    op.drop_column("data_lakes", "ingest_config")

    # data_lake_snapshots: 新增 datasource_id(可空,本地/API 无)
    op.add_column(
        "data_lake_snapshots",
        sa.Column("datasource_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_lake_snapshots", "datasource_id")

    # data_lakes 回退:恢复三列,source_category 用 server_default 兜底
    # (无法恢复原值,仅供本地开发回滚)
    op.add_column(
        "data_lakes",
        sa.Column(
            "source_category",
            sa.String(),
            nullable=False,
            server_default="database",
        ),
    )
    op.add_column(
        "data_lakes",
        sa.Column("datasource_id", sa.String(), nullable=True),
    )
    op.add_column(
        "data_lakes",
        sa.Column(
            "ingest_config",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
