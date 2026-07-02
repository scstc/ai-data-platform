"""内容安全审核(#4)相关 schema。"""

from __future__ import annotations

from typing import Literal

from app.schemas.common import CamelModel, UtcDateTime


class CustomRegexSpec(CamelModel):
    """一条命名自定义正则。"""

    name: str
    pattern: str


class RuleWordSpec(CamelModel):
    """规则库敏感词条目(建任务时由 ruleIds 解析冻结进 config)。"""

    word: str
    category: str = "other"
    severity: str = "medium"


class RuleRegexSpec(CamelModel):
    """规则库正则条目(建任务时由 ruleIds 解析冻结进 config)。"""

    name: str
    pattern: str
    category: str = "other"
    severity: str = "medium"


class ReviewJobConfig(CamelModel):
    """审核配置(入参 config)。

    categories 仅作 UI 侧选择透传(本期内置词表/LLM 覆盖全部类别,不据此裁剪);
    检测手段由 useLlm / usePii / useFlaggedWords 开关控制。
    action:命中行的处置方式——tag 打标(默认,产出带 safety 字段的打标版本);
    delete 删除(产出净化版本,命中行写 removed 存档,强制全量扫描)。
    ruleWords / ruleRegex:规则库条目,建任务时按 ruleIds 解析冻结于此
    (重跑/继续复用冻结值,不受规则库后续增删影响)。
    scanFields:成员表名 -> 参与扫描的字段列表;未配置的表沿用默认取文本逻辑
    (text 优先,否则首个字符串字段)。旧单文件版本用固定键 "data"。
    """

    categories: list[str] = []
    custom_words: list[str] = []
    custom_regex: list[CustomRegexSpec] = []
    rule_words: list[RuleWordSpec] = []
    rule_regex: list[RuleRegexSpec] = []
    action: Literal["tag", "delete"] = "tag"
    use_llm: bool = False
    use_pii: bool = True
    use_flagged_words: bool = True
    sample_limit: int | None = None
    scan_fields: dict[str, list[str]] = {}


class ReviewJobCreate(CamelModel):
    """新建审核任务入参。

    target_members:多表版本时只审这些成员(表名);None/空 = 全部成员。
    rule_ids:选用的规则库条目 id,创建时解析进 config.rule_words/rule_regex。
    """

    dataset_version_id: str
    name: str | None = None
    config: ReviewJobConfig = ReviewJobConfig()
    target_members: list[str] | None = None
    rule_ids: list[str] = []


class ReviewReport(CamelModel):
    """审核汇总报告(存于 job.review_report)。"""

    total_rows: int
    scanned_rows: int
    flagged_rows: int
    sample_limit_applied: bool
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    # 多表版本:逐成员命中行数(table -> flaggedRows)
    by_table: dict[str, int] = {}
    # action=delete:删除行数与逐表存档 URI(table -> removed.jsonl 位置)
    deleted_rows: int | None = None
    removed_archives: dict[str, str] = {}
    action: str | None = None
    warnings: list[str] = []


class ReviewFindingRead(CamelModel):
    """单条命中记录读模型。"""

    id: str
    job_id: str
    version_id: str
    table_name: str | None = None
    row_index: int
    field: str | None = None
    category: str
    severity: str
    source: str
    detail: str | None = None
    snippet: str | None = None
    created_at: UtcDateTime


class ReviewRuleCreate(CamelModel):
    """新建规则库条目。kind=word 时 pattern 作子串;kind=regex 时作正则。"""

    name: str
    kind: Literal["word", "regex"]
    pattern: str
    category: str = "other"
    severity: Literal["high", "medium", "low"] = "medium"
    enabled: bool = True


class ReviewRuleUpdate(CamelModel):
    """更新规则库条目(全部可选,只改给出的字段)。"""

    name: str | None = None
    kind: Literal["word", "regex"] | None = None
    pattern: str | None = None
    category: str | None = None
    severity: Literal["high", "medium", "low"] | None = None
    enabled: bool | None = None


class ReviewRuleRead(CamelModel):
    """规则库条目读模型。"""

    id: str
    name: str
    kind: str
    pattern: str
    category: str
    severity: str
    enabled: bool
    created_at: UtcDateTime
