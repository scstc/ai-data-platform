"""系统-部门 API 测试(ancestors 维护 + 移父子树重排 + 守卫)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_dept_create_ancestors_and_guard(
    client: AsyncClient, seed_rbac
) -> None:
    """建子部门 ancestors 正确;d-a 有用户 ⇒ 删除 409。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))
    r = await client.post(
        "/api/v1/system/depts", json={"name": "丙", "parentId": "d-a"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["ancestors"] == "0,d-root,d-a,"

    # d-a 下有用户 u-staff ⇒ 删除 409
    d = await client.delete("/api/v1/system/depts/d-a")
    assert d.status_code == 409


async def test_dept_move_reseats_subtree(
    client: AsyncClient, seed_rbac, session_factory
) -> None:
    """把 d-a 移到 d-b 下:d-a 及其新建子部门的 ancestors 同步重排(数据权限依赖此正确性)。"""
    from sqlalchemy import select

    from app.models.department import Department
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))
    c = await client.post(
        "/api/v1/system/depts", json={"name": "丙", "parentId": "d-a"}
    )
    cid = c.json()["data"]["id"]
    assert c.json()["data"]["ancestors"] == "0,d-root,d-a,"

    mv = await client.put("/api/v1/system/depts/d-a", json={"parentId": "d-b"})
    assert mv.status_code == 200, mv.text

    async with session_factory() as s:
        moved = (
            await s.scalars(select(Department).where(Department.id == "d-a"))
        ).first()
        child = (
            await s.scalars(select(Department).where(Department.id == cid))
        ).first()
        assert moved.ancestors == "0,d-root,d-b,"
        assert child.ancestors == "0,d-root,d-b,d-a,"


async def test_dept_move_cycle_blocked(
    client: AsyncClient, seed_rbac
) -> None:
    """把 d-root 移到其子 d-a 下 ⇒ 成环 409。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-super"))
    r = await client.put("/api/v1/system/depts/d-root", json={"parentId": "d-a"})
    assert r.status_code == 409
