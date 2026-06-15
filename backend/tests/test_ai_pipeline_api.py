"""generate-pipeline 端点(默认启发式 provider)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_generate_pipeline_dedup_goal(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/ai/generate-pipeline", json={"goal": "中文语料去重"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data["operators"], list) and data["operators"]
    # 每步含 name/params,且 name 都是 ready 算子(经 sanitize)
    from app.services import operator_catalog as oc
    for step in data["operators"]:
        assert set(step) == {"name", "params"}
        assert oc.runnable_reason(step["name"]) is None
    assert isinstance(data["explanation"], str) and data["explanation"]


async def test_generate_pipeline_empty_goal_still_ok(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/ai/generate-pipeline", json={"goal": ""})
    assert resp.status_code == 200
    assert resp.json()["data"]["operators"]  # 兜底场景仍给非空流水线
