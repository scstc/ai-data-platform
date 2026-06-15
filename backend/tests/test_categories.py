"""分类(Category)管理测试(#15)。

覆盖:
- 建(+dup 409);列表回 usageCount;改名(+404 缺失,+dup 409)。
- 删空(成功);删被引用(409 带用量文案)。
- require_admin 门控:非 admin POST/PATCH/DELETE → 403;匿名 → 401。
- 筛选闭环:建分类 → 挂到数据集(上传)→ GET /datasets?categoryId 仅命中它带 name。

测试意图(为何重要):
- 受控词表是管理动作,写端点必须真后端门控(不靠前端隐藏)——否则越权可改全局分类。
- 删除守卫保证不产生悬空引用(被引用拒删),这是数据完整性约束的核心。
- categoryId 过滤必须同时作用 count + list,否则分页 total 与可见行不一致。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """分类写端点加了 require_admin:默认以 admin 身份请求(种子用户见 seed_users)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _create(client: AsyncClient, name: str, note: str | None = None) -> dict:
    """便捷新建一个分类并返回 data 体。"""
    payload: dict[str, object] = {"name": name}
    if note is not None:
        payload["note"] = note
    resp = await client.post("/api/v1/categories", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    return body["data"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def test_create_returns_category(client: AsyncClient) -> None:
    """新建分类:id 形如 cat-、creator=admin、usageCount=0、camelCase 字段在。"""
    data = await _create(client, "金融风控", note="风控相关")
    assert data["id"].startswith("cat-")
    assert data["name"] == "金融风控"
    assert data["note"] == "风控相关"
    assert data["creator"] == "admin"
    assert data["usageCount"] == 0
    assert "createdAt" in data


async def test_create_duplicate_name_409(client: AsyncClient) -> None:
    """重名 → 409 + {success:false, message:'分类名已存在'}。"""
    await _create(client, "营销活动")
    resp = await client.post("/api/v1/categories", json={"name": "营销活动"})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "分类名已存在"


async def test_list_returns_usage_count(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """列表回全部分类 + 各自 usageCount(三实体引用合计)。"""
    cat = await _create(client, "金融风控")
    other = await _create(client, "营销活动")
    # 直接落库两个引用该分类的数据集
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(
                    id="dset-cat001",
                    name="ds1",
                    owner="admin",
                    creator="admin",
                    category_id=cat["id"],
                ),
                Dataset(
                    id="dset-cat002",
                    name="ds2",
                    owner="admin",
                    creator="admin",
                    category_id=cat["id"],
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/categories")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    by_id = {c["id"]: c for c in body["data"]}
    assert by_id[cat["id"]]["usageCount"] == 2
    assert by_id[other["id"]]["usageCount"] == 0


async def test_patch_rename(client: AsyncClient) -> None:
    """改名成功,回显新名。"""
    cat = await _create(client, "旧名")
    resp = await client.patch(
        f"/api/v1/categories/{cat['id']}", json={"name": "新名"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "新名"


async def test_patch_missing_404(client: AsyncClient) -> None:
    """改不存在的分类 → 404 + {success:false}。"""
    resp = await client.patch(
        "/api/v1/categories/cat-nope0", json={"name": "x"}
    )
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_patch_rename_clash_409(client: AsyncClient) -> None:
    """改名撞已有名 → 409。"""
    await _create(client, "甲")
    cat_b = await _create(client, "乙")
    resp = await client.patch(
        f"/api/v1/categories/{cat_b['id']}", json={"name": "甲"}
    )
    assert resp.status_code == 409
    assert resp.json()["message"] == "分类名已存在"


async def test_delete_empty_success(client: AsyncClient) -> None:
    """删空分类 → success;再删 → 404。"""
    cat = await _create(client, "待删")
    resp = await client.delete(f"/api/v1/categories/{cat['id']}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    resp = await client.delete(f"/api/v1/categories/{cat['id']}")
    assert resp.status_code == 404


async def test_delete_in_use_409(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """删被引用的分类 → 409,文案含用量数,且分类未被删。"""
    cat = await _create(client, "在用分类")
    async with session_factory() as session:
        session.add(
            Dataset(
                id="dset-used01",
                name="挂分类的数据集",
                owner="admin",
                creator="admin",
                category_id=cat["id"],
            )
        )
        await session.commit()

    resp = await client.delete(f"/api/v1/categories/{cat['id']}")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "分类正被 1 处引用,无法删除"
    # 守卫生效:分类仍在
    resp = await client.get("/api/v1/categories")
    assert any(c["id"] == cat["id"] for c in resp.json()["data"])


# ---------------------------------------------------------------------------
# require_admin 门控
# ---------------------------------------------------------------------------
async def test_non_admin_write_forbidden(
    client: AsyncClient, seed_users: None
) -> None:
    """user 角色调 POST/PATCH/DELETE → 403(后端越权拦截)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.post("/api/v1/categories", json={"name": "x"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["message"] == "无权限"

    resp = await client.patch(
        "/api/v1/categories/cat-any000", json={"name": "y"}
    )
    assert resp.status_code == 403

    resp = await client.delete("/api/v1/categories/cat-any000")
    assert resp.status_code == 403


async def test_anonymous_write_unauthorized(client: AsyncClient) -> None:
    """无 cookie 调写端点 → 401(未登录)。"""
    client.cookies.clear()
    resp = await client.post("/api/v1/categories", json={"name": "x"})
    assert resp.status_code == 401, resp.text

    resp = await client.patch(
        "/api/v1/categories/cat-any000", json={"name": "y"}
    )
    assert resp.status_code == 401

    resp = await client.delete("/api/v1/categories/cat-any000")
    assert resp.status_code == 401


async def test_list_visible_to_logged_in_user(
    client: AsyncClient, seed_users: None
) -> None:
    """普通登录用户也能浏览分类(GET 不门控)。"""
    await _create(client, "公共可见")
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.get("/api/v1/categories")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# 筛选闭环:挂到数据集 → 按 categoryId 过滤 + 列携带 categoryName
# ---------------------------------------------------------------------------
async def test_dataset_category_filter_and_name(client: AsyncClient) -> None:
    """建分类 → 上传时挂分类 → GET /datasets?categoryId 仅命中它且带 categoryName。"""
    cat = await _create(client, "风控数据")

    # 上传一个带分类的数据集
    files = {"file": ("a.jsonl", b'{"x":1}\n', "application/json")}
    resp = await client.post(
        "/api/v1/datasets/upload",
        files=files,
        data={"name": "带分类集", "categoryId": cat["id"]},
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert created["categoryId"] == cat["id"]
    assert created["categoryName"] == "风控数据"

    # 上传一个不带分类的数据集
    files2 = {"file": ("b.jsonl", b'{"y":2}\n', "application/json")}
    resp = await client.post(
        "/api/v1/datasets/upload", files=files2, data={"name": "无分类集"}
    )
    assert resp.status_code == 200, resp.text

    # 按 categoryId 过滤:total + list 都只剩带分类那条,且行携带 categoryName
    resp = await client.get(
        "/api/v1/datasets", params={"categoryId": cat["id"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["name"] == "带分类集"
    assert row["categoryId"] == cat["id"]
    assert row["categoryName"] == "风控数据"

    # 不带过滤:两条都在
    resp = await client.get("/api/v1/datasets")
    assert resp.json()["total"] == 2


async def test_dataset_patch_clears_category(client: AsyncClient) -> None:
    """PATCH 显式传 categoryId=null 清空分类。"""
    cat = await _create(client, "可清空分类")
    files = {"file": ("c.jsonl", b'{"z":3}\n', "application/json")}
    resp = await client.post(
        "/api/v1/datasets/upload",
        files=files,
        data={"name": "待清空", "categoryId": cat["id"]},
    )
    ds_id = resp.json()["data"]["id"]

    resp = await client.patch(
        f"/api/v1/datasets/{ds_id}", json={"categoryId": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["categoryId"] is None
