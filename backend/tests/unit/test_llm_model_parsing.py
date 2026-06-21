"""LLM「获取模型」响应解析纯单测 —— 多模型管理 / 获取模型功能。

不依赖 DB/网络。锁的是需求意图:各供应商 /models 响应格式不一(OpenAI 标准
envelope、Together 裸数组、项为纯字符串),拉取必须**容错归一**;解析不出时
返回空列表,让上层走「预置清单 + 手填」兜底(智谱 GLM 根本没有该接口)。
"""

from __future__ import annotations

from app.api.v1.llm_config import _extract_model_ids


def test_openai_standard_envelope():
    """OpenAI/DeepSeek/MiniMax 标准:{"object":"list","data":[{"id":...}]}。"""
    body = {
        "object": "list",
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
        ],
    }
    assert _extract_model_ids(body) == ["gpt-4o", "gpt-4o-mini"]


def test_bare_array_of_objects():
    """Together 等返回裸数组而非 envelope,仍需取出 id。"""
    body = [{"id": "m-a"}, {"id": "m-b"}]
    assert _extract_model_ids(body) == ["m-a", "m-b"]


def test_array_of_strings():
    """部分实现 data 项直接是字符串。"""
    assert _extract_model_ids(["m-a", "m-b"]) == ["m-a", "m-b"]


def test_dedup_preserves_order():
    """重复 id 去重且保持首次出现顺序。"""
    body = {"data": [{"id": "x"}, {"id": "y"}, {"id": "x"}]}
    assert _extract_model_ids(body) == ["x", "y"]


def test_model_or_name_fallback_field():
    """项里没有 id 时回退取 model / name。"""
    assert _extract_model_ids([{"model": "mm"}, {"name": "nn"}]) == ["mm", "nn"]


def test_junk_shapes_return_empty():
    """无法解析的响应一律返回空 —— 触发上层预置清单兜底,绝不抛错。"""
    assert _extract_model_ids({"error": "unauthorized"}) == []
    assert _extract_model_ids("nope") == []
    assert _extract_model_ids(None) == []
    assert _extract_model_ids({"data": "not-a-list"}) == []
    assert _extract_model_ids([{"foo": "bar"}, 123, None]) == []
