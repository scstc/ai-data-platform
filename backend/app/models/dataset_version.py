"""数据集版本 ORM 模型(不可变快照)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
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
    # 多模态模态快照:该版本出现过的模态集合(images/audios/videos/text 子集);
    # 仅 semantic_type=multimodal 版本写入(落地时算)。供列表"图片/视频/音频/跨模态"
    # 子标签与筛选;空(存量/非多模态)→ 列表仅显示"多模态"主标签(见 list_datasets)。
    modalities: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # 来源渠道快照(去重后的列表):汇总该版本各表成员 source_upload_channel,
    # 每次 add_table_member 追加新渠道时并集更新。非湖抽取来源的成员不贡献值,
    # 全部成员都非湖抽取时为空。
    source_channels: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
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
    # 采集质量统计快照(切片 B):{rows, columns:[{name,null_rate}], ...};
    # 未配置质量策略的版本为空。版本不可变,写入即定格。
    quality_stats: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 采集时的表结构快照(切片 B):[{name, type}, ...],用于后续 schema drift 比较。
    schema_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 采集质量门结论(切片 B):skipped(未配置策略) | passed | failed。
    # 与 scan_verdict(安全扫描)正交:一个管"脏不脏",一个管"安不安全"。
    quality_verdict: Mapped[str] = mapped_column(
        String, nullable=False, server_default="skipped"
    )
    # 训练用途(治理整改 G1,见 docs/data-governance-remediation-plan.md 阶段1 /
    # docs/training-dataset-format-spec.md §4):
    # pretrain/sft/distill/dpo/rlhf/eval/custom。训练平台据此过滤可用数据集;
    # 校验在 Pydantic 层,DB 留 free-string。存量行为空。
    # record_count 复用 rows 列(语义等价),不另设冗余列。
    train_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # 该 train_type 的具体 schema 变体(§4):
    # text/alpaca/messages/preference/prompt_only/eval。
    # 供下游构造层/训练侧校验字段结构。
    schema_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    # 湖快照溯源集合(可溯源可复现整改 P1-①):媒体 manifest 整版本形态抽取自哪些
    # 湖快照(去重列表),land_media_manifest 新建时写定、续写 draft 时并集更新。
    # 表成员形态版本走 DatasetVersionTable.source_snapshot_id(逐成员),此列为空。
    source_snapshot_ids: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
