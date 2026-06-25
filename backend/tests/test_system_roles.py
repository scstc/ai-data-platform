"""系统-角色 API 测试(CRUD + 授权 + 权限门控)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_role_crud_and_authorize(client: AsyncClient, seed_rbac) -> None:
    """超管建角色(带菜单授权)→ 列表/详情可见 → role_key 重复 409 → 删除。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))  # 通配

    r = await client.post(
        "/api/v1/system/roles",
        json={
            "name": "数据员",
            "roleKey": "dataops",
            "dataScope": "dept",
            "menuIds": ["m-user"],
            "deptIds": [],
        },
    )
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["id"]

    lst = await client.get("/api/v1/system/roles?current=1&pageSize=20")
    assert lst.status_code == 200
    assert any(x["roleKey"] == "dataops" for x in lst.json()["data"])

    detail = await client.get(f"/api/v1/system/roles/{rid}")
    assert "m-user" in detail.json()["data"]["menuIds"]

    dup = await client.post(
        "/api/v1/system/roles", json={"name": "x", "roleKey": "dataops"}
    )
    assert dup.status_code == 409

    d = await client.delete(f"/api/v1/system/roles/{rid}")
    assert d.status_code == 200


async def test_role_delete_blocked_when_assigned(
    client: AsyncClient, seed_rbac
) -> None:
    """r-dc 已分配给 u-mgr ⇒ 删除 409(被引用守卫)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))
    d = await client.delete("/api/v1/system/roles/r-dc")
    assert d.status_code == 409


async def test_role_list_forbidden_without_perm(
    client: AsyncClient, seed_rbac
) -> None:
    """u-staff(self 角色,无 system:role:list)→ 403(后端越权拦截)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.get("/api/v1/system/roles?current=1&pageSize=20")
    assert r.status_code == 403
