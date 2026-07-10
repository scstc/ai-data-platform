"""采集任务 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class IngestTask(Base):
    """采集任务：从数据源拉取数据，带调度与进度状态机。"""

    __tablename__ = "ingest_tasks"

    # 主键形如 "task-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    datasource_id: Mapped[str] = mapped_column(String, nullable=False)
    datasource_name: Mapped[str] = mapped_column(String, nullable=False)
    # 调度配置：{mode:'once'|'cron', cron?:str}
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # 采集对象（extract spec）：{mode:'table', table} 或 {mode:'sql', sql}；可空
    extract: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 状态：pending | running | success | failed
    status: Mapped[str] = mapped_column(String, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 累计运行次数(冗余计数,避免列表 N+1;明细见 jobs 表 type=ingest)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 日志列表 list[str]
    logs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # 分类(#15):受控分类库引用 categories.id(无 FK,可空,单选)
    category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 「生成 CSV 数据集」绑定的数据集 id(datasets.id,无 FK,可空)。
    # 首次生成时落定,后续生成在同一数据集追加新版本(v1/v2/... 各落不同文件夹)。
    # 治理改造后:新任务优先用 lake_id 落湖,dataset_id 仅存量任务继续沿用。
    dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 目标数据湖(治理改造,见 docs/数据治理.md §5):新任务经此字段落湖快照,
    # 湖是原始归档层;数据集通过'从湖抽取'作业单独产生。无 FK,可空(存量任务空)。
    lake_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 创建人(RBAC self 数据范围依据);存量回填 "admin"
    creator: Mapped[str] = mapped_column(
        String, nullable=False, default="admin", server_default="admin"
    )
    # 所属部门(RBAC 数据权限快照);存量回填为根部门
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 任务级质量策略(切片 B):{maxNullRate, blockOnSchemaDrift},与
    # schemas.ingest_task.QualityPolicy 同形;未配置为空(等价 skipped 结论)。
    quality_policy: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 增量采集配置(切片 C):二选一形,与 schemas.ingest_task.Incremental 同形;
    # 未配置为空(等价全量采集)。
    #   库形  {column, type:'timestamp'|'integer'}  按 DB 列水位推进;
    #   文件形 {by:'mtime'|'name'}                 按 mtime/文件名推进。
    incremental: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 增量水位快照(切片 C):{value, updatedAt},由运行期写入;
    # 空表示该任务尚未做过增量采集(首次按全量跑)。
    watermark: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # 级联删除标记:绑定的数据集(dataset_id)过期打标时一并隐藏;
    # 归因列记录源数据集,恢复时只解除因它标记的
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_by_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
