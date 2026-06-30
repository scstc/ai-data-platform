"""评估数据集 + 裁判员(judge)相关 schema(治理整改 G4/G5)。

评估集落地走 services/eval_dataset.py(≥300 Fail-loud);裁判走 services/judge_runner.py。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import CamelModel, UtcDateTime


class JudgeJobConfig(CamelModel):
    """裁判任务配置:指定三角色字段名 + 通过分数线 + 采样上限。"""

    prompt_field: str = "prompt"
    reference_field: str = "response"  # 评估集里参考答案字段名(默认 response)
    completion_field: str = "completion"  # 模型回答字段名
    category_field: str = "category"
    pass_score: int = Field(default=60, ge=0, le=100)
    sample_limit: int | None = None  # 仅评前 N 行;None = 全量
    use_llm: bool = True  # False 强制走启发式裁判(测试/无 LLM)


class JudgeJobCreate(CamelModel):
    """新建裁判任务入参。"""

    dataset_version_id: str
    name: str | None = None
    config: JudgeJobConfig = JudgeJobConfig()


class EvalResultRead(CamelModel):
    """裁判逐条结果读模型。"""

    id: str
    job_id: str
    version_id: str
    row_index: int
    prompt: str
    reference: str
    completion: str
    score: int | None = None
    verdict: str
    category: str | None = None
    reason: str | None = None
    created_at: UtcDateTime


class EvalReport(CamelModel):
    """裁判汇总报告(落 job.eval_report)。"""

    total_items: int = 0
    scored_items: int = 0
    avg_score: float | None = None
    pass_rate: float | None = None
    by_category: dict[str, Any] = {}
    score_buckets: dict[str, int] = {}
    warnings: list[str] = []
