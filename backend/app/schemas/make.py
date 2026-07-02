"""数据合成(make)相关 schema。

需求文档 #8:数据合成——LLM 造新数据。
增强(改写)走 augment 模块,本模块专管「LLM 造新数据」(1→N / batch+gen_num)。
所有算子都需 LLM,未配 OPENAI_API_KEY 时由后端 needs_api 拦截。
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.job import MemberOperatorConfig, OperatorSpec



class MakeGoal(CamelModel):
    """数据合成目标(任务级参数)。"""

    # 合成模式:固定为 synthesize(后续可能扩展回 augment 之类的,先固定)
    mode: str = "synthesize"
    # 每个输入样本生成的目标条数(1→N 的 N;QA 类算子可 >1)
    target_per_sample: int = 1
    # 目标总条数(可选,截断上限;None 表示不限)
    target_total: int | None = None
    # 备注(落到 DatasetVersion.note)
    note: str | None = None


class MakeJobCreate(CamelModel):
    """新建数据合成任务入参。"""

    name: str
    dataset_version_id: str

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None

    goal: MakeGoal
    output_dataset_id: str | None = None
    # DJ text_keys:算子作用的主文本字段;留空则后端按字段名优先级自动探测。
    # 用于数据无 text 字段的场景(如 GIS address)。
    text_keys: list[str] | None = None


class MakeReport(CamelModel):
    """合成报告(任务跑完后)。"""

    job_id: str
    input_version_id: str
    output_version_id: str | None = None
    mode: str  # synthesize
    input_count: int = 0
    output_count: int | None = None
    expansion_ratio: float | None = None
    elapsed_seconds: float | None = None
    operator_chain: list[str] = []
    warnings: list[str] = []
    raw: dict[str, Any] | None = None
