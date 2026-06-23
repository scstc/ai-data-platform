"""任务 ORM 模型(唯一改动数据的执行单元)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Job(Base):
    """通用任务:读入版本、跑 data-juicer、产出新版本。

    `type` + `config_yaml` 做特化;ingest/clean/quality/synth/process/review/annotate
    共用此表与状态机。承载 #6–#10;采集任务每次运行也产一条 type=ingest 记录
    (ingest_task 收编,原 ingest_runs 表已退役,见迁移 0005)。
    """

    __tablename__ = "jobs"

    # 主键形如 "job-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 类型:ingest | clean | quality | synth | process | review | annotate | distillation | synthesis(make) | augmentation
    type: Mapped[str] = mapped_column(String, nullable=False)
    # type=ingest 时回指所属采集任务配置(ingest_tasks.id);其余类型为空
    ingest_task_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    # 状态机:pending | running | success | failed | cancelled
    state: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # data-juicer recipe(由 YAML 生成器产出);可空(草稿/排队中)
    config_yaml: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 进度 0~100
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 日志路径(目录/文件);产出版本经 DatasetVersion.produced_by_job_id 反查
    logs_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    # 失败原因
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # type=review 时存审核汇总报告(#4):
    # {totalRows, scannedRows, flaggedRows, sampleLimitApplied,
    #  byCategory, bySeverity, bySource};其余类型为空
    review_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 重跑用:建任务时存原始执行规格(JobCreate:算子 + 输出去向 + 输入版本),
    # POST /jobs/{id}/rerun 据此对原输入版本再跑一次。早于本特性的任务为空。
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
