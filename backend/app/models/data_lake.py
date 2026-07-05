"""数据湖 ORM 模型 - ODS 原始数据层。

数据湖是所有外部异构数据源的统一入口，原样接入、原样存储、版本固化。
湖集分离：数据湖 = 原始原料（不可直接用于训练），数据集 = 加工成品（可直接训练）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import uuid7_hex


class DataLake(Base):
    """数据湖：多源异构数据的统一接入容器。

    职责：
    - 作为所有外部数据源的唯一入口（数据库/对象存储/HDFS/本地/API 汇入同一个湖）
    - 管理多个不可变的源头快照版本（DataLakeSnapshot）
    - 快照层记录每次接入的具体来源（datasource_id/upload_channel/data_category），
      湖本身不绑定类型——同一个湖里可以有 MySQL 快照 + OSS 快照 + PDF 快照并存

    治理文档语义："多源统一数据湖"，见 docs/数据治理.md §2.1。
    """

    __tablename__ = "data_lakes"

    # 主键形如 "lake-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 数据湖名称（用户可读，如"财务系统数据湖"、"用户行为日志湖"）
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # 归属与权限
    owner: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DataLakeObject(Base):
    """数据湖文件：一张表 / 一个文件的稳定身份，串起其多个版本快照。

    身份由 `(lake_id, identity_key)` 唯一确定（identity_key 推导规则见
    `app.services.data_lake.derive_identity_key`，回填迁移复用同一规则）。
    `latest_*` 冗余自最新版本，避免列表页逐个聚合快照。
    """

    __tablename__ = "data_lake_objects"

    __table_args__ = (
        UniqueConstraint("lake_id", "identity_key", name="uq_lake_object_identity"),
        Index("ix_dlo_lake_id", "lake_id"),
    )

    # 主键形如 "lobj-" + uuid7 hex
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: f"lobj-{uuid7_hex()}"
    )
    lake_id: Mapped[str] = mapped_column(String, nullable=False)
    # 身份键，见 derive_identity_key
    identity_key: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    # ingested：外部采集/上传；merged：湖内合并生成
    origin: Mapped[str] = mapped_column(String, nullable=False, default="ingested")
    data_category: Mapped[str] = mapped_column(String, nullable=False)
    storage_format: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 仅 merged：{mode, join_keys, inputs:[{object_id}]}
    merge_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DataLakeSnapshot(Base):
    """数据湖快照：一次数据源接入的不可变版本归档。

    核心不变量：
    - 快照一经写入永不修改，任何"更新"都是产出新快照
    - 原始数据原样存储（结构化→Parquet，文档/多媒体→原格式）
    - 版本命名规范：source_v年月日_批次_类型（如 source_v20260701_01_mysql），
      现语义降级为归档批次标签，判重主键让位给 (object_id, version_no)
    """

    __tablename__ = "data_lake_snapshots"

    __table_args__ = (
        UniqueConstraint("lake_id", "source_version", name="uq_lake_source_version"),
        UniqueConstraint("object_id", "version_no", name="uq_lake_object_version"),
        Index("ix_dls_lake_id", "lake_id"),
        Index("ix_dls_ingest_task", "ingest_task_id"),
        Index("ix_dls_object_id", "object_id"),
    )

    # 主键形如 "snap-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属数据湖（纯引用，无 FK）
    lake_id: Mapped[str] = mapped_column(String, nullable=False)
    # 源头快照版本号：source_v年月日_批次_类型（如 source_v20260701_01_mysql）
    source_version: Mapped[str] = mapped_column(String, nullable=False)
    # 原始数据存储位置（MinIO/S3 路径，结构化为 Parquet，文档/多媒体为原格式）
    # 如：s3://data-lake/lake-abc123/source_v20260701_01_mysql/table1.parquet
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    # 存储格式：parquet（结构化）| pdf/docx/xlsx/png/mp4 等（原格式）
    storage_format: Mapped[str] = mapped_column(String, nullable=False)
    # 数据类型：database | document | image | audio | video | text
    data_category: Mapped[str] = mapped_column(String, nullable=False)
    # 上传/接入渠道：oss | obs | minio | api | local | database
    upload_channel: Mapped[str] = mapped_column(String, nullable=False)
    # 本次接入的具体数据源引用（指向 datasources.id）；本地上传/API 推送无
    # datasource，为空。同一个湖里可以有多个不同 datasource_id 的快照并存。
    datasource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 差异化溯源字段（JSONB，数据库数据存 db_schema/db_table/db_engine，
    # 对象存储存 bucket_name/obj_key，本地/API存 original_filename）
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 快照统计信息
    rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 关联的采集任务（可空，手动入湖时无任务）
    ingest_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 所属文件（DataLakeObject.id）与该文件下的第几版；全 nullable 兼容存量
    # 与手工测试构造，服务层新写入必填
    object_id: Mapped[str | None] = mapped_column(String, nullable=True)
    version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 产出该版本的任务运行（手动上传/合并可为空）
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 合并版本血缘：[{object_id, snapshot_id, version_no}, ...]
    merge_inputs: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    # 快照不可变，仅记录创建时间
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
