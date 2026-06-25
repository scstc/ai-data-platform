"""系统-权限总览 API 测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_permission_overview(client: AsyncClient, seed_rbac) -> None:
    """总览:r-dc 聚合权限含 system:user:add;全量权限码目录含之。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))
    r = await client.get("/api/v1/system/permissions/overview")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    dc = next(x for x in data["roles"] if x["id"] == "r-dc")
    assert "system:user:add" in dc["perms"]
    assert "system:user:add" in data["allPerms"]


async def test_permission_overview_forbidden(
    client: AsyncClient, seed_rbac
) -> None:
    """u-staff 无 system:perm:list ⇒ 403。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.get("/api/v1/system/permissions/overview")
    assert r.status_code == 403
