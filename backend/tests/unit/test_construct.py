"""数据集构造层纯单测(G2/G3)—— 无 DB。

锁意图:原始列被正确映射成训练 schema,且必填字段为空时 Fail-loud 剔除(不落脏样本)。
改了校验逻辑(如允许 SFT output 为空)这些测试就该红。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.construct import ConstructGoal, FieldSource
from app.services.construct import build_training_records


def _goal(**kw) -> ConstructGoal:
    return ConstructGoal(**kw)


def test_alpaca_maps_columns_and_drops_empty_output():
    """alpaca:instruction(常量模板)+ input(content列)+ output(rating列);
    output 列为空的行被剔除并计 bad_rows(锁 'SFT output 必填非空' 意图)。"""
    goal = _goal(
        train_type="sft",
        schema_variant="alpaca",
        field_mapping={
            "instruction": FieldSource(const="分析这条反馈的情感"),
            "input": FieldSource(column="content"),
            "output": FieldSource(column="rating"),
        },
    )
    rows = [
        {"content": "产品很好", "rating": "正面"},
        {"content": "无评分", "rating": None},  # output 空 → bad
    ]
    good, bad, errors = build_training_records(rows, goal)
    assert good == [
        {"instruction": "分析这条反馈的情感", "input": "产品很好", "output": "正面"}
    ]
    assert bad == 1
    assert errors and "output" in errors[0]


def test_messages_builds_turns_and_flags_blank_content():
    """messages:user 引 content、assistant 引模板;assistant 内容空的行 bad。"""
    goal = _goal(
        train_type="sft",
        schema_variant="messages",
        messages=[
            {"role": "user", "content": {"column": "q"}},
            {"role": "assistant", "content": {"column": "a"}},
        ],
    )
    rows = [
        {"q": "信用卡怎么挂失?", "a": "可通过手机银行挂失"},
        {"q": "缺答案", "a": ""},  # assistant 空 → bad
    ]
    good, bad, _ = build_training_records(rows, goal)
    assert len(good) == 1
    assert good[0]["messages"][0] == {"role": "user", "content": "信用卡怎么挂失?"}
    assert good[0]["messages"][1]["role"] == "assistant"
    assert bad == 1


def test_template_missing_column_becomes_bad():
    """template 引用不存在的列 → 该行 bad + error,不冒成异常。"""
    goal = _goal(
        train_type="pretrain",
        schema_variant="text",
        field_mapping={"text": FieldSource(template="标题:{title} 正文:{body}")},
    )
    good, bad, errors = build_training_records([{"title": "只有标题"}], goal)
    assert good == []
    assert bad == 1
    assert errors and "模板字段缺失" in errors[0]


def test_eval_maps_prompt_response_with_optional_category():
    goal = _goal(
        train_type="eval",
        schema_variant="eval",
        field_mapping={
            "prompt": FieldSource(column="q"),
            "response": FieldSource(column="a"),
            "category": FieldSource(column="cat"),
        },
    )
    good, bad, _ = build_training_records(
        [{"q": "年费如何减免?", "a": "刷卡达标即可", "cat": "信用卡"}], goal
    )
    assert good == [
        {"prompt": "年费如何减免?", "response": "刷卡达标即可", "category": "信用卡"}
    ]
    assert bad == 0


def test_all_rows_invalid_yields_empty_good():
    """全部行不合规 → good 为空(由 run_construct_job 转 ConstructError)。"""
    goal = _goal(
        train_type="pretrain",
        schema_variant="text",
        field_mapping={"text": FieldSource(column="missing")},
    )
    good, bad, _ = build_training_records([{"x": 1}, {"y": 2}], goal)
    assert good == []
    assert bad == 2


def test_goal_rejects_invalid_combo():
    """sft + preference 是非法组合 → ConstructGoal 校验抛 ValidationError(422)。"""
    with pytest.raises(ValidationError):
        _goal(
            train_type="sft",
            schema_variant="preference",
            field_mapping={"prompt": FieldSource(column="p")},
        )


def test_goal_rejects_invalid_role():
    """role='tool' 非法 → MessageTurnSpec 校验抛 ValidationError。"""
    with pytest.raises(ValidationError):
        _goal(
            train_type="sft",
            schema_variant="messages",
            messages=[{"role": "tool", "content": {"column": "x"}}],
        )
