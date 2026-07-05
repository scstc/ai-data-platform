"""流水线(pipeline)相关 schema:治理工场的"命名算子编排,可保存复用"。"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel, UtcDateTime
from app.schemas.job import OperatorSpec


class PipelineSpec(CamelModel):
    """流水线编排内容:算子链 + 可选的任务级目标 / 作用字段。

    goal 结构随 scenario 而异(蒸馏/合成/增强各有各的 Goal schema),此处存原始
    dict,交由 execute 按 scenario 分发时再校验/重建为对应的 xxxGoal。
    """

    operators: list[OperatorSpec]
    goal: dict[str, Any] | None = None
    text_keys: list[str] | None = None


class PipelineCreate(CamelModel):
    """新建流水线入参。"""

    name: str
    description: str | None = None
    scenario: str
    spec: PipelineSpec


class PipelineUpdate(CamelModel):
    """更新流水线入参(全部可选,只改给出的字段)。"""

    name: str | None = None
    description: str | None = None
    scenario: str | None = None
    spec: PipelineSpec | None = None


class PipelineRead(CamelModel):
    """流水线读模型。"""

    id: str
    name: str
    description: str | None = None
    scenario: str
    spec: PipelineSpec
    is_preset: bool = False
    created_by: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class PipelineExecuteRequest(CamelModel):
    """一键执行入参:name 缺省时按流水线名 + 时间自动生成任务名。"""

    name: str | None = None
    dataset_version_id: str
