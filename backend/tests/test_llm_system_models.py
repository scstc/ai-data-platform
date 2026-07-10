"""「系统模型设置」（按能力位）端点测试。

覆盖:
- GET 返回全部 5 个能力位;未设置时 chat 位回退展示当前激活供应商。
- PUT chat 位联动激活语义:目标供应商 is_active=True、写 provider.model
  并刷新运行时缓存(消费方走 is_active,不感知 llm_system_models 表)。
- PUT 非 chat 能力位仅落表,不影响激活态。
- provider_id 置空 = 清除能力位;chat 位清除时全部取消激活。
- 未知能力位 → 422;不存在的供应商 → 404。
- PUT require_admin 门控:匿名 → 401。

测试意图(为何重要):
- chat 位是唯一有运行时消费方的能力位,若与 is_active 脱钩,界面显示的
  「系统推理模型」与实际推理用的模型将不一致 —— 故锁联动 + 缓存刷新。
- 其余能力位当前只做存储,但删除供应商必须级联清理,否则界面出现悬空引用。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.services.auth import sign_token
from app.services.llm_config import get_active_llm_config

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """默认以 admin 身份请求(PUT 端点 require_admin)。"""
    client.cookies.set("adp_session", sign_token("admin"))


async def _create_provider(
    client: AsyncClient, *, name: str = "t", model: str = "gpt-4o-mini"
) -> str:
    """新建一个供应商并返回其 id。"""
    resp = await client.post(
        "/api/v1/llm-providers",
        json={
            "name": name,
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-x",
            "model": model,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _by_cap(items: list[dict]) -> dict[str, dict]:
    return {i["capability"]: i for i in items}


async def test_get_returns_all_capabilities(client: AsyncClient) -> None:
    """GET 恒返回 5 个能力位,未设置的 providerId/model 为 None。"""
    resp = await client.get("/api/v1/llm-system-models")
    assert resp.status_code == 200
    caps = _by_cap(resp.json()["data"])
    assert set(caps) == {"chat", "embedding", "rerank", "speech2text", "tts"}


async def test_chat_falls_back_to_active_provider(client: AsyncClient) -> None:
    """chat 位未显式设置时,回退展示当前激活供应商及其模型。"""
    pid = await _create_provider(client, model="gpt-4o-mini")
    await client.post(f"/api/v1/llm-providers/{pid}/activate")
    resp = await client.get("/api/v1/llm-system-models")
    chat = _by_cap(resp.json()["data"])["chat"]
    assert chat["providerId"] == pid
    assert chat["model"] == "gpt-4o-mini"


async def test_put_chat_activates_provider_and_refreshes_cache(
    client: AsyncClient,
) -> None:
    """保存 chat 位 → 目标供应商激活、provider.model 更新、缓存即时生效。"""
    p1 = await _create_provider(client, name="a", model="m1")
    p2 = await _create_provider(client, name="b", model="m2")
    await client.post(f"/api/v1/llm-providers/{p1}/activate")

    resp = await client.put(
        "/api/v1/llm-system-models",
        json={
            "items": [
                {"capability": "chat", "providerId": p2, "model": "m2-new"}
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    chat = _by_cap(resp.json()["data"])["chat"]
    assert chat["providerId"] == p2
    assert chat["model"] == "m2-new"

    # 激活态转移到 p2,且 provider.model 已写回
    providers = (await client.get("/api/v1/llm-providers")).json()["data"]
    by_id = {p["id"]: p for p in providers}
    assert by_id[p2]["isActive"] is True
    assert by_id[p2]["model"] == "m2-new"
    assert by_id[p1]["isActive"] is False
    # 运行时缓存即时生效(消费方读 get_active_llm_config)
    assert get_active_llm_config().model == "m2-new"


async def test_put_non_chat_does_not_touch_activation(
    client: AsyncClient,
) -> None:
    """非 chat 能力位仅落表:激活态与 provider.model 均不受影响。"""
    pid = await _create_provider(client, model="chat-m")
    await client.post(f"/api/v1/llm-providers/{pid}/activate")

    resp = await client.put(
        "/api/v1/llm-system-models",
        json={
            "items": [
                {
                    "capability": "embedding",
                    "providerId": pid,
                    "model": "BAAI/bge-m3",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    caps = _by_cap(resp.json()["data"])
    assert caps["embedding"]["model"] == "BAAI/bge-m3"
    by_id = {
        p["id"]: p
        for p in (await client.get("/api/v1/llm-providers")).json()["data"]
    }
    assert by_id[pid]["model"] == "chat-m"  # 未被 embedding 位污染


async def test_put_clear_chat_deactivates_all(client: AsyncClient) -> None:
    """chat 位清除(providerId 置空)→ 所有供应商取消激活。"""
    pid = await _create_provider(client)
    await client.put(
        "/api/v1/llm-system-models",
        json={"items": [{"capability": "chat", "providerId": pid, "model": "m"}]},
    )
    resp = await client.put(
        "/api/v1/llm-system-models",
        json={"items": [{"capability": "chat", "providerId": None, "model": None}]},
    )
    assert resp.status_code == 200
    by_id = {
        p["id"]: p
        for p in (await client.get("/api/v1/llm-providers")).json()["data"]
    }
    assert by_id[pid]["isActive"] is False


async def test_put_rejects_unknown_capability_and_missing_provider(
    client: AsyncClient,
) -> None:
    """未知能力位 → 422;不存在的供应商 → 404。"""
    resp = await client.put(
        "/api/v1/llm-system-models",
        json={"items": [{"capability": "vision", "providerId": "x", "model": "m"}]},
    )
    assert resp.status_code == 422
    resp = await client.put(
        "/api/v1/llm-system-models",
        json={
            "items": [
                {"capability": "embedding", "providerId": "llm-nope", "model": "m"}
            ]
        },
    )
    assert resp.status_code == 404


async def test_delete_provider_cascades_system_models(
    client: AsyncClient,
) -> None:
    """删除供应商级联清理其能力位引用,GET 不再返回悬空 providerId。"""
    pid = await _create_provider(client)
    await client.put(
        "/api/v1/llm-system-models",
        json={
            "items": [{"capability": "rerank", "providerId": pid, "model": "r1"}]
        },
    )
    await client.delete(f"/api/v1/llm-providers/{pid}")
    caps = _by_cap((await client.get("/api/v1/llm-system-models")).json()["data"])
    assert caps["rerank"]["providerId"] is None


async def test_put_requires_admin(client: AsyncClient) -> None:
    """匿名 PUT → 401。"""
    client.cookies.clear()
    resp = await client.put(
        "/api/v1/llm-system-models", json={"items": []}
    )
    assert resp.status_code == 401
