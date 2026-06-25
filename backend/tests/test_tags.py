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


async def test_delete_in_use_409(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """删被引用的标签 → 409(带引用数),标签与关联均保留。"""
    tag = await _create(client, "在用")
    async with session_factory() as session:
        session.add(Dataset(id="dset-del01", name="ds", owner="admin", creator="admin"))
        session.add(DatasetTag(dataset_id="dset-del01", tag_id=tag["id"]))
        await session.commit()

    resp = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["message"] == "标签正被 1 个数据集引用,无法删除"
    # 守卫生效:标签与关联都在
    async with session_factory() as session:
        assert (await session.get(Tag, tag["id"])) is not None
        assert (await session.scalars(
            select(DatasetTag).where(DatasetTag.tag_id == tag["id"])
        )).all() != []


async def test_delete_unused_success(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """删未被引用的标签 → 200,标签消失。"""
    tag = await _create(client, "孤标签")
    resp = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert resp.status_code == 200
    async with session_factory() as session:
        assert (await session.get(Tag, tag["id"])) is None


async def test_delete_missing_404(client: AsyncClient) -> None:
    """删不存在的标签 → 404。"""
    assert (await client.delete("/api/v1/tags/tag-nope0")).status_code == 404


# ---------------------------------------------------------------------------
# 批量删除 + 合并
# ---------------------------------------------------------------------------
async def test_batch_delete_blocked_when_in_use(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """批量删:任一被引用 → 整批 409,所有标签保留。"""
    a = await _create(client, "甲")
    b = await _create(client, "乙")
    async with session_factory() as session:
        session.add(Dataset(id="dset-bd01", name="ds", owner="admin", creator="admin"))
        session.add(DatasetTag(dataset_id="dset-bd01", tag_id=a["id"]))  # 甲被引用
        await session.commit()

    resp = await client.request("DELETE", "/api/v1/tags", json={"ids": [a["id"], b["id"]]})
    assert resp.status_code == 409, resp.text
    async with session_factory() as session:
        assert (await session.get(Tag, a["id"])) is not None  # 甲保留
        assert (await session.get(Tag, b["id"])) is not None  # 乙也保留(整批拒)


async def test_batch_delete_unused_success(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """批量删未被引用的标签 → 200,全部消失。"""
    a = await _create(client, "甲")
    b = await _create(client, "乙")
    resp = await client.request("DELETE", "/api/v1/tags", json={"ids": [a["id"], b["id"]]})
    assert resp.status_code == 200
    async with session_factory() as session:
        assert (await session.get(Tag, a["id"])) is None
        assert (await session.get(Tag, b["id"])) is None


async def test_merge_reassigns_and_dedups(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """合并:source 的数据集全部重指到 target;同时含两标签的数据集去重;source 删除。"""
    source = await _create(client, "源")
    target = await _create(client, "目标")
    # ds_only_src: 只挂 source;ds_both: 同时挂 source+target;ds_only_tgt: 只挂 target
    async with session_factory() as session:
        session.add_all(
            [Dataset(id="d-only-src", name="a", owner="admin", creator="admin"),
             Dataset(id="d-both", name="b", owner="admin", creator="admin"),
             Dataset(id="d-only-tgt", name="c", owner="admin", creator="admin")]
        )
        session.add_all(
            [DatasetTag(dataset_id="d-only-src", tag_id=source["id"]),
             DatasetTag(dataset_id="d-both", tag_id=source["id"]),
             DatasetTag(dataset_id="d-both", tag_id=target["id"]),
             DatasetTag(dataset_id="d-only-tgt", tag_id=target["id"])]
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/tags/merge",
        json={"sourceId": source["id"], "targetId": target["id"]},
    )
    assert resp.status_code == 200, resp.text

    async with session_factory() as session:
        assert (await session.get(Tag, source["id"])) is None  # 源已删
        # 目标标签现在挂 3 个数据集(only-src 重指 + both 去重保留 1 + only-tgt)
        tgt_links = (await session.scalars(
            select(DatasetTag).where(DatasetTag.tag_id == target["id"])
        )).all()
        assert {r.dataset_id for r in tgt_links} == {"d-only-src", "d-both", "d-only-tgt"}
        assert len(tgt_links) == 3  # 无重复


async def test_merge_same_id_400(client: AsyncClient) -> None:
    t = await _create(client, "自合")
    resp = await client.post(
        "/api/v1/tags/merge", json={"sourceId": t["id"], "targetId": t["id"]}
    )
    assert resp.status_code == 400


async def test_merge_missing_404(client: AsyncClient) -> None:
    t = await _create(client, "存在")
    resp = await client.post(
        "/api/v1/tags/merge",
        json={"sourceId": "tag-nope0", "targetId": t["id"]},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# require_admin 门控
# ---------------------------------------------------------------------------
async def test_non_admin_write_forbidden(
    client: AsyncClient, seed_users: None
) -> None:
    """user 角调写端点 → 403。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    assert (await client.post("/api/v1/tags", json={"name": "x"})).status_code == 403
    assert (await client.patch("/api/v1/tags/tag-x0", json={"name": "y"})).status_code == 403
    assert (await client.delete("/api/v1/tags/tag-x0")).status_code == 403
    assert (
        await client.request("DELETE", "/api/v1/tags", json={"ids": ["tag-x0"]})
    ).status_code == 403
    assert (
        await client.post("/api/v1/tags/merge", json={"sourceId": "a", "targetId": "b"})
    ).status_code == 403


async def test_list_visible_to_non_admin(
    client: AsyncClient, seed_users: None
) -> None:
    """普通登录用户可浏览(GET 不门控)。"""
    from app.services.auth import sign_token

    await _create(client, "公共")
    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 200
