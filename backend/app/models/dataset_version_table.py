"""数据集版本-表成员 ORM 模型(一个版本承载多个表/parquet 成员)。

仿 job_inputs 的无 FK 弱关联子表:一行 = 一张表 = 一个 parquet 文件。
版本级 storage_uri/format/rows/size/schema_snapshot 退化为跨成员 rollup;
单表数据集 = 恰好一个成员,旧字段语义对单成员仍成立。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DatasetVersionTable(Base):
    """版本内的一个表成员(parquet/jsonl 文件)。"""

    __tablename__ = "dataset_version_tables"

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "table_name", name="uq_dvt_version_table"
        ),
        Index("ix_dvt_version", "dataset_version_id"),
    )

    # 主键形如 "dvt-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属版本(纯引用,无 FK)
    dataset_version_id: Mapped[str] = mapped_column(String, nullable=False)
    # 表名/成员名,版本内唯一
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    # 单成员文件位置 s3://<bucket>/<dataset_id>/v<n>/<table>.parquet
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False, default="parquet")
    rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    schema_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 成员级 schema 变体(默认继承版本级)
    schema_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    # 质量评估:该成员逐条 stats 文件路径(dj-analyze 产出,如 <table>_stats.jsonl);
    # 未跑过质量评估的成员为空。
    stats_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
