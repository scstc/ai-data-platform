"""系统-权限:角色×权限授权总览(给「权限管理」页一个落点;权限本体挂在菜单上)。"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import SessionDep, require_perm
from app.models.menu import Menu
from app.models.rbac_links import RoleMenu
from app.models.role import Role

router = APIRouter(prefix="/system/permissions", tags=["system-permissions"])


@router.get(
    "/overview", dependencies=[Depends(require_perm("system:perm:list"))]
)
async def overview(session: SessionDep) -> JSONResponse:
    """角色×权限总览:每个角色其聚合权限码 + 全量权限码目录(F 菜单 perms 去重)。

    超管角色(role_key=admin)标 ``*:*:*``(与运行时鉴权一致)。
    """
    roles = (await session.scalars(select(Role).order_by(Role.sort))).all()
    perms_map: dict[str, set[str]] = defaultdict(set)
    pairs = (
        await session.execute(
            select(RoleMenu.role_id, Menu.perms)
            .join(Menu, Menu.id == RoleMenu.menu_id)
            .where(Menu.perms.is_not(None))
        )
    ).all()
    for rid, perm in pairs:
        if perm:
            perms_map[rid].add(perm)
    role_list = [
        {
            "id": r.id,
            "name": r.name,
            "roleKey": r.role_key,
            "perms": ["*:*:*"]
            if r.role_key == "admin"
            else sorted(perms_map.get(r.id, set())),
        }
        for r in roles
    ]
    all_perms = sorted(
        {
            p
            for p in (
                await session.scalars(
                    select(Menu.perms).where(Menu.perms.is_not(None))
                )
            ).all()
            if p
        }
    )
    return JSONResponse(
        {"data": {"roles": role_list, "allPerms": all_perms}, "success": True}
    )
