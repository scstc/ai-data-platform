"""内容安全审核(#4)相关 schema。"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class CustomRegexSpec(CamelModel):
    """一条命名自定义正则。"""

    name: str
    pattern: str


class ReviewJobConfig(CamelModel):
    """审核配置(入参 config)。

    categories 仅作 UI 侧选择透传(本期内置词表/LLM 覆盖全部类别,不据此裁剪);
    检测手段由 useLlm / usePii / useFlaggedWords 开关控制。
    """

    categories: list[str] = []
    custom_words: list[str] = []
    custom_regex: list[CustomRegexSpec] = []
    use_llm: bool = False
    use_pii: bool = True
    use_flagged_words: bool = True
    sample_limit: int | None = None


class ReviewJobCreate(CamelModel):
    """新建审核任务入参。"""

    dataset_version_id: str
    name: str | None = None
    config: ReviewJobConfig = ReviewJobConfig()


class ReviewReport(CamelModel):
    """审核汇总报告(存于 job.review_report)。"""

    total_rows: int
    scanned_rows: int
    flagged_rows: int
    sample_limit_applied: bool
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    warnings: list[str] = []


class ReviewFindingRead(CamelModel):
    """单条命中记录读模型。"""

    id: str
    job_id: str
    version_id: str
    row_index: int
    category: str
    severity: str
    source: str
    detail: str | None = None
    snippet: str | None = None
    created_at: datetime
