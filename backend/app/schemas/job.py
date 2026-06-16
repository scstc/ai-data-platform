"""加工任务(Job)相关 schema。"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel, UtcDateTime


class OperatorSpec(CamelModel):
    """编排里的一个算子:名称 + 参数。"""

    name: str
    params: dict[str, Any] | None = None


class JobCreate(CamelModel):
    """新建加工任务入参:对某个数据集版本跑一串算子。"""

    name: str
    type: str = "clean"
    dataset_version_id: str
    operators: list[OperatorSpec]
    # 产物去向:
    #   version     → 写回输入数据集,产出新版本(默认,保持原行为)
    #   new_dataset → 另存为新数据集(名取 output_dataset_name),产物为其 v1
    output_mode: str = "version"
    output_dataset_name: str | None = None


class QualityJobCreate(CamelModel):
    """新建质量评估任务入参(#6):对某个数据集版本逐条算 filter stats。"""

    name: str
    dataset_version_id: str
    operators: list[OperatorSpec]


class JobRead(CamelModel):
    """加工任务读模型。"""

    id: str
    name: str
    type: str
    state: str
    progress: int
    error: str | None = None
    config_yaml: str | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    # 产物概要：{datasetId, datasetName, versionId, versionNo, rows}
    output: dict[str, Any] | None = None
    # 输入版本概要(经 job_inputs 反查)：{datasetId, datasetName, versionId, versionNo}
    input: dict[str, Any] | None = None
