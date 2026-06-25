"""系统域:getRouters + currentUser 扩展(API 层)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_routers_requires_login(client: AsyncClient) -> None:
    """匿名取 routers → 401。"""
    resp = await client.get("/api/v1/system/menus/routers")
    assert resp.status_code == 401


async def test_get_routers_scoped_by_role(
    client: AsyncClient, seed_rbac
) -> None:
    """u-mgr 登录态取 routers → 含 /system 目录及 /system/user 子项。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-mgr"))
    resp = await client.get("/api/v1/system/menus/routers")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data[0]["path"] == "/system"
    assert data[0]["children"][0]["path"] == "/system/user"


async def test_current_user_carries_roles_and_perms(
    client: AsyncClient, seed_users
) -> None:
    """admin 登录后 currentUser 带 permissions=['*:*:*']、access=admin(回归不破)。"""
    await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    me = await client.get("/api/currentUser")
    body = me.json()["data"]
    assert body["access"] == "admin"
    assert body["permissions"] == ["*:*:*"]
    # seed_users 不建 user_roles,故 roles 经 user_roles 查为空;permissions 仍通配
    assert "admin" in body["roles"] or body["roles"] == []
