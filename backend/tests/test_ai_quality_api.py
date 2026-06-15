"""generate-quality 端点(只推 filter 类、可运行算子;默认启发式 provider)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_generate_quality_filter_only(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/ai/generate-quality",
        json={"goal": "评估中文文本质量、过滤低质"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data["operators"], list) and data["operators"]
    # 每步算子均可查到、为 filter 类、且当前环境可运行
    from app.services import operator_catalog as oc

    for step in data["operators"]:
        assert set(step) == {"name", "params"}
        op = oc.get_operator(step["name"])
        assert op is not None
        assert op["category"] == "filter"
        assert oc.runnable_reason(step["name"]) is None
    assert isinstance(data["explanation"], str)


async def test_generate_quality_empty_goal_still_ok(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/ai/generate-quality", json={"goal": ""}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # 空 goal 不强求非空,但凡返回的算子都必须是 filter 类、可运行
    from app.services import operator_catalog as oc

    for step in body["data"]["operators"]:
        op = oc.get_operator(step["name"])
        assert op is not None and op["category"] == "filter"
        assert oc.runnable_reason(step["name"]) is None
