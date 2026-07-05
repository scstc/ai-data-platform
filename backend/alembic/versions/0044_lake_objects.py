"""数据湖三层模型：新增 data_lake_objects，snapshots 加 object_id/version_no 等列

Revision ID: 0044_lake_objects
Revises: 0043_review_finding_field
Create Date: 2026-07-04

背景见 docs/数据湖文件版本模型整改.md。原有 DataLake -> DataLakeSnapshot 两层
模型里，快照只是"一次接入动作"，区分不了"同一张表/文件的第 N 个版本"。本迁
移引入 DataLakeObject 承载"文件的稳定身份"，快照下挂到具体文件下按
version_no 排列。

存量数据回填规则（与 app.services.data_lake.derive_identity_key 保持同步，
迁移内不 import app 代码，规则在下方 `_derive_identity_key` 拷贝一份）：
1. source_metadata.db_table 存在 -> key=f"{datasource_id or 'ds'}:{db_table}"，
   display=db_table
2. source_metadata.obj_key 存在 ->
   key=f"{datasource_id or 'ds'}:{bucket_name}/{obj_key}"，
   display=obj_key 最后一段
3. source_metadata.hdfs_path 存在 -> key=f"{datasource_id or 'ds'}:{hdfs_path}"，
   display=路径最后一段
4. 否则 -> key=f"local:{original_filename}"，display=同名

按 (lake_id, identity_key) 分组，组内按 (created_at, id) 排序编 version_no
1..n，插入 data_lake_objects 一行，object 的 latest_* 取组内最后一条。

**本迁移只建结构 + 回填数据，不在此处收紧 NOT NULL**（设计文档 §3 第 3 步待
存量验证通过后另行处理）。**不要在本仓库执行 upgrade**——只对 adp_gov 库单
独执行，dev 库 `adp` 不动。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044_lake_objects"
down_revision: str | None = "0043_review_finding_field"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _derive_identity_key(
    datasource_id: str | None, source_metadata: dict | None
) -> tuple[str, str]:
    """与 app.services.data_lake.derive_identity_key 规则一致的迁移期拷贝。

    迁移里没有 original_filename 单独入参，本地/API 场景直接从
    source_metadata.original_filename 取。
    """
    meta = source_metadata or {}
    ds = datasource_id or "ds"

    db_table = meta.get("db_table")
    if db_table:
        return f"{ds}:{db_table}", db_table

    obj_key = meta.get("obj_key")
    if obj_key:
        bucket_name = meta.get("bucket_name") or ""
        display = obj_key.rsplit("/", 1)[-1]
        return f"{ds}:{bucket_name}/{obj_key}", display

    hdfs_path = meta.get("hdfs_path")
    if hdfs_path:
        display = hdfs_path.rsplit("/", 1)[-1]
        return f"{ds}:{hdfs_path}", display

    name = meta.get("original_filename")
    return f"local:{name}", name or ""


def upgrade() -> None:
    op.create_table(
        "data_lake_objects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("lake_id", sa.String(), nullable=False),
        sa.Column("identity_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column(
            "origin", sa.String(), nullable=False, server_default="ingested"
        ),
        sa.Column("data_category", sa.String(), nullable=False),
        sa.Column("storage_format", sa.String(), nullable=True),
        sa.Column(
            "latest_version_no", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("latest_snapshot_id", sa.String(), nullable=True),
        sa.Column(
            "merge_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "lake_id", "identity_key", name="uq_lake_object_identity"
        ),
    )
    op.create_index("ix_dlo_lake_id", "data_lake_objects", ["lake_id"])

    op.add_column(
        "data_lake_snapshots", sa.Column("object_id", sa.String(), nullable=True)
    )
    op.add_column(
        "data_lake_snapshots", sa.Column("version_no", sa.Integer(), nullable=True)
    )
    op.add_column(
        "data_lake_snapshots", sa.Column("job_id", sa.String(), nullable=True)
    )
    op.add_column(
        "data_lake_snapshots",
        sa.Column(
            "merge_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.create_index("ix_dls_object_id", "data_lake_snapshots", ["object_id"])
    op.create_unique_constraint(
        "uq_lake_object_version",
        "data_lake_snapshots",
        ["object_id", "version_no"],
    )

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, lake_id, datasource_id, source_metadata,
                   data_category, storage_format, rows, size, created_at
            FROM data_lake_snapshots
            ORDER BY lake_id, created_at, id
            """
        )
    ).mappings().all()

    # (lake_id, identity_key) -> [snapshot 行...]，保持已排序的顺序
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        identity_key, display_name = _derive_identity_key(
            row["datasource_id"], row["source_metadata"]
        )
        key = (row["lake_id"], identity_key)
        groups.setdefault(key, []).append(
            {**row, "identity_key": identity_key, "display_name": display_name}
        )

    for (lake_id, identity_key), snaps in groups.items():
        object_id = f"lobj-{uuid.uuid4().hex}"
        last = snaps[-1]
        bind.execute(
            sa.text(
                """
                INSERT INTO data_lake_objects
                    (id, lake_id, identity_key, display_name, origin,
                     data_category, storage_format, latest_version_no,
                     latest_snapshot_id, created_at, updated_at)
                VALUES
                    (:id, :lake_id, :identity_key, :display_name, 'ingested',
                     :data_category, :storage_format, :latest_version_no,
                     :latest_snapshot_id, now(), now())
                """
            ),
            {
                "id": object_id,
                "lake_id": lake_id,
                "identity_key": identity_key,
                "display_name": last["display_name"],
                "data_category": last["data_category"],
                "storage_format": last["storage_format"],
                "latest_version_no": len(snaps),
                "latest_snapshot_id": last["id"],
            },
        )
        for version_no, snap in enumerate(snaps, start=1):
            bind.execute(
                sa.text(
                    """
                    UPDATE data_lake_snapshots
                    SET object_id = :object_id, version_no = :version_no
                    WHERE id = :id
                    """
                ),
                {
                    "object_id": object_id,
                    "version_no": version_no,
                    "id": snap["id"],
                },
            )


def downgrade() -> None:
    op.drop_constraint(
        "uq_lake_object_version", "data_lake_snapshots", type_="unique"
    )
    op.drop_index("ix_dls_object_id", table_name="data_lake_snapshots")
    op.drop_column("data_lake_snapshots", "merge_inputs")
    op.drop_column("data_lake_snapshots", "job_id")
    op.drop_column("data_lake_snapshots", "version_no")
    op.drop_column("data_lake_snapshots", "object_id")

    op.drop_index("ix_dlo_lake_id", table_name="data_lake_objects")
    op.drop_table("data_lake_objects")
