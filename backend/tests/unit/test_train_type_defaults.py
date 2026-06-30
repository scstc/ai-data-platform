"""train_type 默认推断纯单测(G1)—— 无 DB。

锁意图:数据按语义自动获得正确训练用途默认,训练平台才能按 train_type 过滤到。
qa→sft / text→pretrain / preference→dpo,否则平台选 SFT 训练时筛不到 qa 数据集。
"""

from __future__ import annotations

import pytest

from app.services.semantic_registry import (
    SemanticValidationError,
    default_schema_variant,
    infer_train_type,
    parse_train_type,
)


@pytest.mark.parametrize(
    "semantic, expected",
    [
        ("qa", "sft"),
        ("cot", "sft"),
        ("preference", "dpo"),
        ("text", "pretrain"),
        ("eval", "eval"),
        ("gis", None),  # 无确定训练用途
        ("structured", None),
        (None, None),
        ("bogus", None),  # 非法串收敛为 None,不抛
    ],
)
def test_infer_train_type(semantic, expected):
    assert infer_train_type(semantic) == expected


@pytest.mark.parametrize(
    "train_type, expected",
    [
        ("pretrain", "text"),
        ("sft", "messages"),
        ("dpo", "preference"),
        ("rlhf", "prompt_only"),
        ("eval", "eval"),
        ("distill", None),  # 无固定变体
        ("custom", None),
        (None, None),
        ("bogus", None),
    ],
)
def test_default_schema_variant(train_type, expected):
    assert default_schema_variant(train_type) == expected


def test_parse_train_type_passes_none_and_valid():
    assert parse_train_type(None) is None
    assert parse_train_type("sft") == "sft"


def test_parse_train_type_rejects_invalid():
    with pytest.raises(SemanticValidationError):
        parse_train_type("not-a-train-type")
