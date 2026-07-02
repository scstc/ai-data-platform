"""数据增强(augment)相关 schema。

需求文档 #8:数据增强——LLM 改写已有数据。
合成(造新数据)走 make 模块,本模块专管「LLM 改写 1 条数据」(1→1 改写/优化/校准/打标)。
所有算子都需 LLM,未配 OPENAI_API_KEY 时由后端 needs_api 拦截。
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.job import MemberOperatorConfig, OperatorSpec



class AugmentGoal(CamelModel):
    """数据增强目标(任务级参数)。"""

    mode: str = "augment"
    # 增强通常 1→1,保留 target_per_sample 字段是 schema 兼容(后续若 1→N 也支持)
    target_per_sample: int = 1
    # 目标总条数(可选)
    target_total: int | None = None
    note: str | None = None


class AugmentJobCreate(CamelModel):
    """新建数据增强任务入参。"""

    name: str
    dataset_version_id: str

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None

    goal: AugmentGoal
    output_dataset_id: str | None = None
    # DJ text_keys:算子作用的主文本字段;留空则后端按字段名优先级自动探测。
    # 用于数据无 text 字段的场景(如 GIS address)。
    text_keys: list[str] | None = None


class AugmentReport(CamelModel):
    """增强报告(任务跑完后)。"""

    job_id: str
    input_version_id: str
    output_version_id: str | None = None
    mode: str  # augment
    input_count: int = 0
    output_count: int | None = None
    # 增强 1→1 时扩增比通常 = 1;若算子意外扩增也会偏离 1
    expansion_ratio: float | None = None
    elapsed_seconds: float | None = None
    operator_chain: list[str] = []
    warnings: list[str] = []
    raw: dict[str, Any] | None = None
