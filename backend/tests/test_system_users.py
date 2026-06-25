"""系统-用户 API 测试(CRUD + 重置密码 + 分配角色 + 守卫)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_user_create_list_resetpwd(client: AsyncClient, seed_rbac) -> None:
    """超管建用户(带角色+部门)→ 列表含其 role_key → 重复 username 409 → 重置密码。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))

    r = await client.post(
        "/api/v1/system/users",
        json={
            "username": "zhang",
            "password": "p@ss1234",
            "displayName": "张三",
            "deptId": "d-a",
            "roleIds": ["r-dc"],
        },
    )
    assert r.status_code == 200, r.text
    uid = r.json()["data"]["id"]

    lst = await client.get("/api/v1/system/users?current=1&pageSize=50")
    assert lst.status_code == 200, lst.text
    assert any(
        u["username"] == "zhang" and "r_dc" in u["roles"]
        for u in lst.json()["data"]
    )

    dup = await client.post(
        "/api/v1/system/users",
        json={"username": "zhang", "password": "x1234567"},
    )
    assert dup.status_code == 409

    rp = await client.put(
        f"/api/v1/system/users/{uid}/password", json={"password": "new12345"}
    )
    assert rp.status_code == 200


async def test_user_admin_undeletable(client: AsyncClient, seed_users) -> None:
    """内置 admin 用户(username=admin)禁删 → 409。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))
    d = await client.delete("/api/v1/system/users/usr-test01")
    assert d.status_code == 409
