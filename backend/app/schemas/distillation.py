"""数据蒸馏（Distillation）相关 schema。

蒸馏没有任务级"目标"参数：保留多少、按什么字段排序、要不要去重,完全由
用户选的算子链自身决定(如 topk_specified_field_selector / random_selector /
document_minhash_deduplicator 各自的参数),不存在与算子链脱钩的另一套配置。
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.job import MemberOperatorConfig, OperatorSpec


class DistillationJobCreate(CamelModel):
    """新建数据蒸馏任务入参。"""

    name: str
    dataset_version_id: str
    # 治理工场:经流水线一键执行时回指来源(pipelines.id);手工建任务留空
    pipeline_id: str | None = None

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None

    # 选填:另存到别的数据集;默认沿用输入版本所属的数据集
    output_dataset_id: str | None = None
    # DJ text_keys:算子作用的主文本字段;留空则后端按字段名优先级自动探测。
    # 蒸馏数据通常无 text 字段(如 instruction),建议显式指定。
    text_keys: list[str] | None = None


class DistillationReport(CamelModel):
    """蒸馏报告:任务跑完后的输入/输出/统计摘要。"""

    job_id: str
    input_version_id: str
    output_version_id: str | None = None
    input_count: int = 0
    output_count: int | None = None
    keep_ratio_actual: float | None = None
    dedup_removed: int | None = None
    filter_removed: int | None = None
    elapsed_seconds: float | None = None
    operator_chain: list[str] = []
    warnings: list[str] = []
    # 兜底字段,前端兜底展示原始统计
    raw: dict[str, Any] | None = None
