"""任务 ORM 模型(唯一改动数据的执行单元)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
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
    # 类型:ingest | clean | quality | synth | process | review | annotate |
    # distillation | synthesis(make) | augmentation | construct | judge | extract
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
    # type=judge 时存裁判汇总报告(治理整改 G5):
    # {totalItems, scoredItems, avgScore, passRate, byCategory, scoreBuckets, warnings}
    eval_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 重跑用:建任务时存原始执行规格(JobCreate:算子 + 输出去向 + 输入版本),
    # POST /jobs/{id}/rerun 据此对原输入版本再跑一次。早于本特性的任务为空。
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 触发来源(切片 C):manual(手工) | cron(定时,Task 3 真跑);
    # 存量 job 一律 'manual'(迁移 0025 server_default 安全回填)。
    trigger: Mapped[str] = mapped_column(
        String, nullable=False, default="manual", server_default="manual"
    )
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 所属部门(RBAC 数据权限快照);存量回填为根部门
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 可复现凭证(治理整改 G18,规范 Q9):执行时记录;早于本特性 / 未执行的 job 为空。
    dj_version: Mapped[str | None] = mapped_column(String, nullable=True)
    image_tag: Mapped[str | None] = mapped_column(String, nullable=True)
    # 执行器类型:single(默认单机) | ray(分布式)
    executor_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # 目标成员：指定要处理的表成员名列表(成员级算子配置);None=处理所有成员
    target_members: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # 成员级独立算子配置：[{memberName, operators, textKeys?}]
    member_configs: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    # 治理工场:经流水线(pipelines.id)一键执行时回指来源;手工建任务为空
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # 级联删除标记:所属数据集过期打标时,输入或产出涉及它的 job 一并隐藏;
    # 归因列记录源数据集,恢复时只解除因它标记的(共享任务不误恢复)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_by_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # ── 任务队列可靠性(整改 P0,迁移 0069)──
    # 已尝试次数 / 允许的最大尝试次数;runner 认领前校验 attempts < max_attempts
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # worker 存活探测:认领时置当前时间,运行期定期续跳;长时间未续跳视为僵死
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 认领该 job 的 worker 标识;未认领为空
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 入队时间(区别于 created_at:排队等待时长 = claimed 时刻 - queued_at)
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 任务依赖(预留,本迁移仅建列未消费):需等该 job 完成才可入队
    depends_on_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 非致命告警列表(如降级/跳过某成员);语义 list[str],无告警为空
    warnings: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
