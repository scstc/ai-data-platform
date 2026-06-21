"""LLM 供应商「多模型管理 + 获取模型」端点测试。

覆盖:
- 手动添加 / 列表 / 去重;删除单个模型。
- select-model 写回 provider.model;**激活态下即时刷新运行时缓存**。
- fetch-models 对不可达供应商**优雅降级**(data.success=false 且保留既有模型)。
- 删除供应商**级联清理**其名下模型(无 DB 外键,应用层兜底)。
- 写端点 require_admin 门控:非 admin → 403,匿名 → 401。

测试意图(为何重要):
- 多模型只是候选清单,真正驱动所有 AI 功能的是「当前模型」;切换必须即时生效,
  否则配置改了但推理还用旧模型 —— 故锁 select-model 刷新缓存这条行为。
- 各供应商 /models 能力参差(智谱 GLM 根本没有),拉取失败必须降级而非报错,
  让用户回退到预置/手填 —— 故锁 fetch-models 的容错路径。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.services.auth import sign_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """默认以 admin 身份请求(写端点均 require_admin)。"""
    client.cookies.set("adp_session", sign_token("admin"))


async def _create_provider(
    client: AsyncClient,
    *,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> str:
    """新建一个供应商并返回其 id。"""
    resp = await client.post(
        "/api/v1/llm-providers",
        json={
            "name": "t",
            "provider": "openai",
            "baseUrl": base_url,
            "apiKey": "sk-x",
            "model": model,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


async def test_new_provider_has_empty_model_list(client: AsyncClient) -> None:
    """新建供应商时,其初始 model 不自动入 llm_models(回填只针对迁移时的老数据)。

    新建端点本身不写 llm_models;列表初始为空,需手动添加 / 拉取。
    """
    pid = await _create_provider(client)
    resp = await client.get(f"/api/v1/llm-providers/{pid}/models")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


async def test_add_list_dedup_delete(client: AsyncClient) -> None:
    """手动添加 → 列表可见;重复添加去重;删除后消失。"""
    pid = await _create_provider(client)

    r1 = await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "gpt-4o"}
    )
    assert r1.status_code == 200, r1.text
    models = r1.json()["data"]
    assert [m["model"] for m in models] == ["gpt-4o"]
    assert models[0]["source"] == "manual"
    model_id = models[0]["id"]

    # 重复添加 → 去重(仍只有一条)
    r2 = await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "gpt-4o"}
    )
    assert len(r2.json()["data"]) == 1

    # 删除
    rd = await client.delete(f"/api/v1/llm-providers/{pid}/models/{model_id}")
    assert rd.status_code == 200, rd.text
    rl = await client.get(f"/api/v1/llm-providers/{pid}/models")
    assert rl.json()["data"] == []


async def test_select_model_active_refreshes_runtime_cache(
    client: AsyncClient,
) -> None:
    """激活供应商后 select-model 切换当前模型 → get_active_llm_config 立即生效。"""
    from app.services.llm_config import get_active_llm_config

    pid = await _create_provider(client, model="gpt-4o-mini")
    # 激活 → 运行时缓存指向该供应商
    ra = await client.post(f"/api/v1/llm-providers/{pid}/activate")
    assert ra.status_code == 200, ra.text
    assert get_active_llm_config().model == "gpt-4o-mini"

    # 切换当前模型 → 缓存即时刷新
    rs = await client.post(
        f"/api/v1/llm-providers/{pid}/select-model", json={"model": "gpt-4o"}
    )
    assert rs.status_code == 200, rs.text
    assert rs.json()["data"]["model"] == "gpt-4o"
    assert get_active_llm_config().model == "gpt-4o"


async def test_fetch_models_unreachable_degrades_gracefully(
    client: AsyncClient,
) -> None:
    """供应商不可达时 fetch-models 不报 500:data.success=false 且保留既有模型。"""
    # base_url 指向必然拒绝的本地端口
    pid = await _create_provider(client, base_url="http://127.0.0.1:1/v1")
    await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "keep-me"}
    )

    resp = await client.post(f"/api/v1/llm-providers/{pid}/fetch-models")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["success"] is False
    assert data["added"] == 0
    # 既有手填模型仍在,前端可继续走预置/手填兜底
    assert any(m["model"] == "keep-me" for m in data["models"])


async def test_delete_provider_cascades_models(client: AsyncClient) -> None:
    """删除供应商 → 其名下模型一并清理(应用层级联)。"""
    pid = await _create_provider(client)
    await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "gpt-4o"}
    )

    rd = await client.delete(f"/api/v1/llm-providers/{pid}")
    assert rd.status_code == 200, rd.text
    # 供应商已不存在 → 列表模型端点 404
    rl = await client.get(f"/api/v1/llm-providers/{pid}/models")
    assert rl.status_code == 404


async def test_write_endpoints_require_admin(client: AsyncClient) -> None:
    """非 admin 写模型 → 403;匿名 → 401。"""
    pid = await _create_provider(client)  # admin 身份建好

    client.cookies.set("adp_session", sign_token("user"))
    r403 = await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "x"}
    )
    assert r403.status_code == 403, r403.text

    client.cookies.clear()
    r401 = await client.post(
        f"/api/v1/llm-providers/{pid}/models", json={"model": "x"}
    )
    assert r401.status_code == 401, r401.text
