"""数据蒸馏（Distillation）相关 schema。

蒸馏任务的"任务级参数"与"步骤级算子"分离：
- 任务级参数（goal）：保留比例 / 排序字段 / 兜底策略
- 步骤级算子（operators）：走 data-juicer ``process`` 段
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.job import OperatorSpec


class DistillationGoal(CamelModel):
    """数据蒸馏目标(任务级参数,不写到 data-juicer 算子链里)。"""

    # 保留比例(0~1)和保留条数二选一,均不填则默认按 ratio=0.3
    keep_ratio: float | None = None
    keep_num: int | None = None
    # 排序字段(topk_specified_field_selector 取 top 用)
    score_field: str = "meta.score"
    # 不足时是否用 random_selector 兜底补齐
    fallback_random: bool = True
    # 是否启用 minhash 去重(语义记录,真正去重看算子链)
    enable_dedup: bool = True
    # 是否启用打分过滤(语义记录,真正过滤看算子链)
    enable_score_filter: bool = True


class DistillationJobCreate(CamelModel):
    """新建数据蒸馏任务入参。"""

    name: str
    dataset_version_id: str
    operators: list[OperatorSpec]
    goal: DistillationGoal
    # 选填:另存到别的数据集;默认沿用输入版本所属的数据集
    output_dataset_id: str | None = None


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
