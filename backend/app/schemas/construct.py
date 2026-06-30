"""数据集构造层(construct)相关 schema(治理整改 G2/G3)。

把原始列(如 {id,content,rating} / {question,answer})确定性映射成训练 schema:
SFT messages / Alpaca / DPO preference / 评估 prompt-response / 预训练 text。
方式A:纯 Python 确定性列映射(不调 LLM;LLM 合成走 make/augment)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from app.schemas.common import CamelModel

# 训练用途与 schema 变体取值(与 semantic_registry.TrainType 对齐 + 文档 §4)
TRAIN_TYPES = ("pretrain", "sft", "distill", "dpo", "rlhf", "eval", "custom")
SCHEMA_VARIANTS = ("text", "alpaca", "messages", "preference", "prompt_only", "eval")

# (train_type → 允许的 schema_variant 集合);custom 放行任意。
_VALID_COMBOS: dict[str, set[str]] = {
    "pretrain": {"text"},
    "sft": {"alpaca", "messages"},
    "distill": {"alpaca", "messages"},
    "dpo": {"preference"},
    "rlhf": {"prompt_only"},
    "eval": {"eval"},
    "custom": set(SCHEMA_VARIANTS),
}


class FieldSource(CamelModel):
    """一个训练字段的取值来源:列引用 / 模板 / 常量。

    优先级 column > template > const。
    """

    column: str | None = None  # 取源记录的某列值
    template: str | None = None  # Python str.format 模板,占位符引用源列名
    const: str | None = None  # 固定常量(如 SFT 的固定 instruction)


class MessageTurnSpec(CamelModel):
    """messages 变体的一轮对话规格。"""

    role: Literal["system", "user", "assistant"]
    content: FieldSource


class ConstructGoal(CamelModel):
    """构造目标:目标训练 schema + 字段映射。"""

    train_type: str
    schema_variant: str
    # text/alpaca/preference/eval/prompt_only 用:训练字段名 → 取值来源
    field_mapping: dict[str, FieldSource] = {}
    # 仅 schema_variant=messages 用:多轮对话规格
    messages: list[MessageTurnSpec] = []
    note: str | None = None

    @model_validator(mode="after")
    def _check_combo(self) -> ConstructGoal:
        if self.train_type not in TRAIN_TYPES:
            raise ValueError(
                f"非法 trainType={self.train_type!r};允许:{', '.join(TRAIN_TYPES)}"
            )
        if self.schema_variant not in SCHEMA_VARIANTS:
            raise ValueError(
                f"非法 schemaVariant={self.schema_variant!r};"
                f"允许:{', '.join(SCHEMA_VARIANTS)}"
            )
        allowed = _VALID_COMBOS.get(self.train_type, set())
        if self.schema_variant not in allowed:
            raise ValueError(
                f"trainType={self.train_type} 不支持 schemaVariant="
                f"{self.schema_variant};允许:{', '.join(sorted(allowed))}"
            )
        if self.schema_variant == "messages" and not self.messages:
            raise ValueError("schemaVariant=messages 需提供非空 messages 规格")
        if self.schema_variant != "messages" and not self.field_mapping:
            raise ValueError(f"schemaVariant={self.schema_variant} 需提供 fieldMapping")
        return self


class ConstructJobCreate(CamelModel):
    """新建构造任务入参(无 operators——确定性映射,不进 DJ)。"""

    name: str
    dataset_version_id: str
    goal: ConstructGoal
    output_dataset_id: str | None = None


class ConstructReport(CamelModel):
    """构造报告。"""

    job_id: str
    input_version_id: str
    output_version_id: str | None = None
    train_type: str
    schema_variant: str
    input_count: int = 0
    output_count: int | None = None
    bad_rows: int = 0
    elapsed_seconds: float | None = None
    warnings: list[str] = []
    errors: list[str] = []
    raw: dict[str, Any] | None = None
