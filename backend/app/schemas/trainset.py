"""数据合成(trainset)相关 schema。

数据合成——LLM 从源数据造训练样本(QA 对 / COT 推理链 / 偏好对),1→N。
与 augment(1→1 改写)、make(merge 拼接)区分:本模块专管「造新训练样本」。
所有算子都需 LLM,未配 OPENAI_API_KEY 时由后端 needs_api 拦截。

注意:内部标识用 ``trainset``,避开已被「数据集构造层」占用的 ``construct``。
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.job import MemberOperatorConfig, OperatorSpec


class TrainsetGoal(CamelModel):
    """数据合成目标(任务级参数)。"""

    # 生成模式:synthesize(LLM 造新样本);沿用 make 的 synthesize 语义
    mode: str = "synthesize"
    # 每个输入样本生成的目标条数(1→N 的 N;QA 类算子可 >1)
    target_per_sample: int = 1
    # 目标总条数(可选,截断上限;None 表示不限)
    target_total: int | None = None
    note: str | None = None


class TrainsetJobCreate(CamelModel):
    """新建数据合成任务入参。"""

    name: str
    dataset_version_id: str
    # 治理工场:经流水线一键执行时回指来源(pipelines.id);手工建任务留空
    pipeline_id: str | None = None

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None

    goal: TrainsetGoal
    output_dataset_id: str | None = None
    # DJ text_keys:算子作用的主文本字段;留空则后端按字段名优先级自动探测。
    text_keys: list[str] | None = None


class TrainsetReport(CamelModel):
    """数据合成报告(任务跑完后)。"""

    job_id: str
    input_version_id: str
    output_version_id: str | None = None
    mode: str  # synthesize
    input_count: int = 0
    output_count: int | None = None
    # 1→N 生成,扩增比通常 ≥1
    expansion_ratio: float | None = None
    elapsed_seconds: float | None = None
    operator_chain: list[str] = []
    warnings: list[str] = []
    raw: dict[str, Any] | None = None
