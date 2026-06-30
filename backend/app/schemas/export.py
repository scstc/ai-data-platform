"""数据集交付/导出(export)相关 schema(治理整改 G8/G9)。"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from app.schemas.common import CamelModel


class ExportGoal(CamelModel):
    """交付目标:格式 + 分片 + 三件套开关 + 目标存储。"""

    export_format: Literal["parquet", "jsonl"] = "parquet"
    export_shard_size: int | None = None  # 每片约 N 条;None=单文件
    include_stats: bool = True
    include_card: bool = True
    # 目标存储:None=平台内置 MinIO uploads 桶;否则指向 s3 数据源(本期默认平台)
    target_datasource_id: str | None = None
    target_bucket: str | None = None
    target_prefix: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ExportGoal:
        if self.export_shard_size is not None and self.export_shard_size <= 0:
            raise ValueError("exportShardSize 须为正整数或 null(单文件)")
        if self.target_datasource_id and not self.target_bucket:
            raise ValueError("指定 targetDatasourceId 时 targetBucket 必填")
        return self


class ExportJobCreate(CamelModel):
    """新建交付任务入参。"""

    name: str
    dataset_version_id: str
    goal: ExportGoal = ExportGoal()


class ExportFileItem(CamelModel):
    """单个交付对象。"""

    name: str
    key: str
    bucket: str
    size: int
    presigned_url: str | None = None


class ExportReport(CamelModel):
    """交付报告。"""

    job_id: str
    version_id: str
    target_uri: str
    files: list[ExportFileItem] = []
    record_count: int = 0
    shard_count: int = 0
    train_format: str = "parquet"
    included_stats: bool = True
    included_card: bool = True
    elapsed_seconds: float | None = None
    warnings: list[str] = []
    errors: list[str] = []
