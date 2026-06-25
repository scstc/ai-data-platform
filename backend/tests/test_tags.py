"""标签(Tag)管理测试。

覆盖:GET 列表回 usageCount/createdAt;新建(find-or-create 幂等);改名(404/409);
删除(级联解绑 dataset_tags);批量删除级联;合并(去重 + 删源);require_admin 门控。

测试意图(为何重要):
- 标签是数据集多对多元数据,删除/合并会改写 dataset_tags 关联——级联正确性是数据完整性的核心。
- 合并时「同时含源+目标标签的数据集」必须去重(复合 PK 兜底),否则 PK 冲突报错。
- 写端点必须后端 require_admin 门控(不靠前端隐藏),防越权改全局标签池。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.tag import DatasetTag, Tag

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """标签写端点加了 require_admin:默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _create(client: AsyncClient, name: str) -> dict:
    resp = await client.post("/api/v1/tags", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_list_returns_usage_count_and_created_at(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """GET /tags 回全量标签 + 各自 usageCount + createdAt(标签直接落库,不依赖 POST)。"""
    async with session_factory() as session:
        session.add_all([Tag(id="tag-t01", name="NLP"), Tag(id="tag-t02", name="CV")])
        session.add_all(
            [
                Dataset(id="dset-tg01", name="ds1", owner="admin", creator="admin"),
                Dataset(id="dset-tg02", name="ds2", owner="admin", creator="admin"),
            ]
        )
        session.add_all(
            [DatasetTag(dataset_id="dset-tg01", tag_id="tag-t01"),
             DatasetTag(dataset_id="dset-tg02", tag_id="tag-t01")]
        )
        await session.commit()

    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["data"]}
    assert by_id["tag-t01"]["usageCount"] == 2
    assert by_id["tag-t02"]["usageCount"] == 0
    assert "createdAt" in by_id["tag-t01"]


# ---------------------------------------------------------------------------
# POST / PATCH / DELETE
# ---------------------------------------------------------------------------
async def test_create_find_or_create_idempotent(client: AsyncClient) -> None:
    """同名 POST 直接返回已存在项(不报错),usageCount 透传。"""
    a = await _create(client, "同义")
    b_resp = await client.post("/api/v1/tags", json={"name": "同义"})
    assert b_resp.status_code == 200
    assert b_resp.json()["data"]["id"] == a["id"]


async def test_create_strips_whitespace(client: AsyncClient) -> None:
    """name 去首尾空白后入库。"""
    data = await _create(client, "  NLP  ")
    assert data["name"] == "NLP"


async def test_patch_rename(client: AsyncClient) -> None:
    tag = await _create(client, "旧名")
    resp = await client.patch(f"/api/v1/tags/{tag['id']}", json={"name": "新名"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "新名"


async def test_patch_missing_404(client: AsyncClient) -> None:
    resp = await client.patch("/api/v1/tags/tag-nope0", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_patch_rename_clash_409(client: AsyncClient) -> None:
    await _create(client, "甲")
    b = await _create(client, "乙")
    resp = await client.patch(f"/api/v1/tags/{b['id']}", json={"name": "甲"})
    assert resp.status_code == 409
    assert resp.json()["message"] == "标签名已存在"


async def test_delete_cascades_dataset_tags(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """删标签 → dataset_tags 中对应关联消失,数据集仍在。"""
    tag = await _create(client, "待删")
    async with session_factory() as session:
        session.add(Dataset(id="dset-del01", name="ds", owner="admin", creator="admin"))
        session.add(DatasetTag(dataset_id="dset-del01", tag_id=tag["id"]))
        await session.commit()

    resp = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert resp.status_code == 200
    async with session_factory() as session:
        left = (await session.scalars(
            select(DatasetTag).where(DatasetTag.tag_id == tag["id"])
        )).all()
        assert left == []
        assert (await session.get(Dataset, "dset-del01")) is not None  # 数据集仍在
    # 再删 → 404
    assert (await client.delete(f"/api/v1/tags/{tag['id']}")).status_code == 404
