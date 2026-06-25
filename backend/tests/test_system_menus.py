"""系统-菜单 API 测试(树 CRUD + 守卫 + 权限门控)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _flatten(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.get("children", [])))
    return out


async def test_menu_crud_and_guard(client: AsyncClient, seed_rbac) -> None:
    """建 C 菜单 → 树含之且含 F 按钮 → 有子菜单删 409 → 叶子删 200。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))

    r = await client.post(
        "/api/v1/system/menus",
        json={
            "name": "角色管理",
            "parentId": "m-sys",
            "menuType": "C",
            "path": "/system/role",
            "component": "system/role",
        },
    )
    assert r.status_code == 200, r.text
    mid = r.json()["data"]["id"]

    lst = await client.get("/api/v1/system/menus")
    assert lst.status_code == 200, lst.text
    flat = _flatten(lst.json()["data"])
    assert any(n["path"] == "/system/role" for n in flat)
    assert any(n["menuType"] == "F" for n in flat)  # 树含按钮(m-add)

    # m-user 有子按钮 m-add ⇒ 删 409
    d = await client.delete("/api/v1/system/menus/m-user")
    assert d.status_code == 409

    # 新建的叶子 C(无子)⇒ 删 200
    d2 = await client.delete(f"/api/v1/system/menus/{mid}")
    assert d2.status_code == 200


async def test_menu_list_forbidden(client: AsyncClient, seed_rbac) -> None:
    """u-staff 无 system:menu:list ⇒ 403。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.get("/api/v1/system/menus")
    assert r.status_code == 403
