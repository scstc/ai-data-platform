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
    # 语义类型快照(#1/#2/#8):该版本数据的 LLM 语义类型(SemanticType 之一);
    # 跨类型加工/融合产物可追溯(融合记 fusion)。与 data_type 正交,见 docs/plan/14。
    semantic_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # 来源:managed(平台受管) | hosted(三方 S3 托管,#18) | synthetic(LLM 合成/增强产出,#8)
    # synthetic 用于数据合成与增强任务,前端可按 origin 区分「原始数据 vs 合成数据」。
    origin: Mapped[str] = mapped_column(String, nullable=False, default="managed")
    # hosted 版本据此找回 S3 凭证(指向 datasources.id);受管版本为空(#18)
    source_datasource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 产出该版本的 job(血缘上游);首次落地无 job 时可空
    produced_by_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 版本说明 / changelog
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # 安全扫描结论(#4 发布门,见 docs/plan/11):unscanned | passed | failed。
    # 内容审核任务完成后自动回写;auto 结论可被有权限者人工覆盖。
    scan_verdict: Mapped[str] = mapped_column(
        String, nullable=False, server_default="unscanned"
    )
    # 结论来源:auto(按命中自动判) | manual(人工接受风险/驳回);未扫描为空
    verdict_source: Mapped[str | None] = mapped_column(String, nullable=True)
    # 人工覆盖理由(接受风险 / 驳回时填)
    verdict_note: Mapped[str | None] = mapped_column(String, nullable=True)
    # 发布状态(湖→仓边界,#13/#15/#19 之前的门):draft | published | unpublished。
    # draft→published 需 scan_verdict=passed;算法侧只消费 published 版本。
    publish_status: Mapped[str] = mapped_column(
        String, nullable=False, server_default="draft"
    )
    # 发布时间(可追溯);未发布为空
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 版本不可变,仅记录创建时间(无 updated_at)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
