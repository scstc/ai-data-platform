"""评估 + 裁判纯单测(G4/G5/G16)—— 无 DB / 无网络。

锁意图:
- SemanticType.EVAL 必填 prompt/response(别名归一)。
- ≥300 是确定性 Fail-loud 硬门(需求反例:299 拒绝、300 通过、阈值可配)。
- 裁判聚合正确;LLM 越界分数被收敛。
"""

from __future__ import annotations

import pytest

from app.services.ai.llm import OpenAICompatProvider
from app.services.eval_dataset import EvalValidationError, validate_eval_records
from app.services.judge_runner import _aggregate
from app.services.semantic_registry import (
    SemanticValidationError,
    apply_semantic_spec,
)


# --- G16:eval 语义类型 ------------------------------------------------------
def test_eval_semantic_requires_prompt_response():
    """{prompt,response} 全有 → 校验通过、bad_rows=0。"""
    records = [{"prompt": "年费如何减免?", "response": "刷卡达标即可"}]
    out, report = apply_semantic_spec(records, "eval", strict=True)
    assert report.bad_rows == 0
    assert out[0]["prompt"] and out[0]["response"]


def test_eval_semantic_alias_normalization():
    """question→prompt、answer→response 别名归一。"""
    records = [{"question": "什么是LPR?", "answer": "贷款市场报价利率"}]
    out, _ = apply_semantic_spec(records, "eval", strict=False)
    assert out[0].get("prompt") == "什么是LPR?"
    assert out[0].get("response") == "贷款市场报价利率"


def test_eval_semantic_missing_prompt_strict_raises():
    """strict 模式缺 prompt → SemanticValidationError。"""
    with pytest.raises(SemanticValidationError):
        apply_semantic_spec([{"response": "只有答案"}], "eval", strict=True)


# --- G4:≥300 确定性硬门 -----------------------------------------------------
def test_validate_eval_records_rejects_below_threshold():
    rows = [{"prompt": "p", "response": "r"}] * 299
    with pytest.raises(EvalValidationError) as ei:
        validate_eval_records(rows, min_count=300)
    assert "300" in str(ei.value)


def test_validate_eval_records_accepts_threshold():
    rows = [{"prompt": "p", "response": "r"}] * 300
    validate_eval_records(rows, min_count=300)  # 不抛即通过


def test_validate_eval_records_threshold_configurable():
    validate_eval_records([{"prompt": "p", "response": "r"}] * 5, min_count=5)
    with pytest.raises(EvalValidationError):
        validate_eval_records([{"prompt": "p", "response": "r"}] * 4, min_count=5)


# --- G5:裁判聚合 + 越界收敛 -------------------------------------------------
def test_aggregate_computes_avg_and_pass_rate():
    results = [
        {"score": 90, "verdict": "pass", "category": "信用卡"},
        {"score": 40, "verdict": "fail", "category": "信用卡"},
        {"score": None, "verdict": "unscored", "category": None},
    ]
    rep = _aggregate(results)
    assert rep["totalItems"] == 3
    assert rep["scoredItems"] == 2
    assert rep["avgScore"] == 65.0  # (90+40)/2
    assert rep["passRate"] == round(1 / 3, 4)
    assert rep["byCategory"]["信用卡"] == {"total": 2, "pass": 1}
    assert rep["scoreBuckets"]["80-100"] == 1
    assert rep["scoreBuckets"]["0-59"] == 1


def test_normalize_judgment_clamps_and_infers():
    """LLM 越界 score 收敛到 [0,100];verdict 缺失按 score 兜底。"""
    n = OpenAICompatProvider._normalize_judgment
    assert n({"score": 150, "verdict": "pass"})["score"] == 100
    assert n({"score": -5, "verdict": "fail"})["score"] == 0
    # verdict 非法 → 按 score>=60 兜底
    assert n({"score": 75, "verdict": "maybe"})["verdict"] == "pass"
    assert n({"score": 30, "verdict": "??"})["verdict"] == "fail"
    # 缺 score → unscored
    assert n({"reason": "x"})["verdict"] == "unscored"
    assert n(None)["verdict"] == "unscored"
