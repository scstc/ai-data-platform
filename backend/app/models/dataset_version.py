"""数据集版本 ORM 模型(不可变快照)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DatasetVersion(Base):
    """数据集的一个不可变版本快照。

    核心不变量:版本一经写入不再修改;任何"编辑"都是跑 Job 产出新版本。
    `produced_by_job_id` + JobInput 共同构成血缘(#11);承载 #13/#17(版本)/#18(托管)。
    """

    __tablename__ = "dataset_versions"

    __table_args__ = (
        UniqueConstraint("dataset_id", "version_no", name="uq_dataset_version_no"),
        Index("ix_dataset_versions_dataset_id", "dataset_id"),
        Index("ix_dataset_versions_produced_by", "produced_by_job_id"),
    )

    # 主键形如 "dsv-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属数据集(纯引用,沿用本仓库无 FK 约定)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    # 版本号:同一 dataset 内自增 1,2,3...(见 uq 约束)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 规范化存储位置(DJ 可读),如 outputs/<dataset>/<version>/data.jsonl 或 s3://...
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    # 逐条得分文件(#6),如 *_stats.jsonl;无质量 job 时为空
    stats_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    # 规范化格式:jsonl | csv | parquet 等
    format: Mapped[str] = mapped_column(String, nullable=False, default="jsonl")
    # 行数 / 字节大小(#13 元信息)
    rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 来源:managed(平台受管) | hosted(三方 S3 托管,#18,写/删受控)
    origin: Mapped[str] = mapped_column(String, nullable=False, default="managed")
    # hosted 版本据此找回 S3 凭证(指向 datasources.id);受管版本为空(#18)
    source_datasource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 产出该版本的 job(血缘上游);首次落地无 job 时可空
    produced_by_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 版本说明 / changelog
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # 版本不可变,仅记录创建时间(无 updated_at)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
