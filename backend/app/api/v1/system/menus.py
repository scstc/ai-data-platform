"""系统-菜单:getRouters(动态路由下发)+ 菜单树 CRUD(M/C/F + perms)。"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_perm, require_user
from app.models.menu import Menu
from app.models.rbac_links import RoleMenu
from app.models.user import User
from app.schemas.system import MenuCreate, MenuRead, MenuUpdate
from app.services import rbac

router = APIRouter(prefix="/system/menus", tags=["system-menus"])


def _new_id() -> str:
    """生成形如 menu-<6位hex> 的主键。"""
    return f"menu-{secrets.token_hex(3)}"


def _would_cycle(
    parent_map: dict[str, str | None], node_id: str, new_parent_id: str
) -> bool:
    """新上级是否为本节点自身或其后代(沿 new_parent 上溯遇到 node 即成环)。"""
    cur: str | None = new_parent_id
    seen: set[str] = set()
    while cur and cur not in seen:
        if cur == node_id:
            return True
        seen.add(cur)
        cur = parent_map.get(cur)
    return False


@router.get("/routers")
async def get_routers(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> dict:
    """当前用户可见菜单树(M/C),供前端 patchClientRoutes 生成动态路由。"""
    return {"success": True, "data": await rbac.build_router_tree(session, user)}


@router.get("", dependencies=[Depends(require_perm("system:menu:list"))])
async def list_menus(session: SessionDep) -> JSONResponse:
    """全部菜单(含 M/C/F)组装为树,供菜单管理页。"""
    rows = (await session.scalars(select(Menu).order_by(Menu.sort))).all()
    by_id: dict[str, MenuRead] = {m.id: MenuRead.model_validate(m) for m in rows}
    roots: list[MenuRead] = []
    for m in rows:
        item = by_id[m.id]
        if m.parent_id and m.parent_id in by_id:
            by_id[m.parent_id].children.append(item)
        else:
            roots.append(item)
    data = [r.model_dump(by_alias=True, mode="json") for r in roots]
    return JSONResponse({"data": data, "success": True})


@router.post("", dependencies=[Depends(require_perm("system:menu:add"))])
async def create_menu(body: MenuCreate, session: SessionDep) -> JSONResponse:
    """新建菜单;menu_type 须 M/C/F;上级不存在 404。"""
    if body.menu_type not in ("M", "C", "F"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "menu_type 须为 M/C/F"},
        )
    parent_id = body.parent_id or None
    if parent_id:
        parent = await session.get(Menu, parent_id)
        if parent is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "上级菜单不存在"},
            )
    menu = Menu(
        id=_new_id(),
        parent_id=parent_id,
        name=body.name,
        menu_type=body.menu_type,
        path=body.path,
        component=body.component,
        perms=body.perms,
        icon=body.icon,
        sort=body.sort,
        visible=body.visible,
        status=body.status,
        is_frame=body.is_frame,
    )
    session.add(menu)
    await session.commit()
    await session.refresh(menu)
    return JSONResponse(
        {
            "data": MenuRead.model_validate(menu).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.put("/{menu_id}", dependencies=[Depends(require_perm("system:menu:edit"))])
async def update_menu(
    menu_id: str, body: MenuUpdate, session: SessionDep
) -> JSONResponse:
    """改菜单;移父做环检测(新上级不能是自身或后代)。"""
    menu = await session.get(Menu, menu_id)
    if menu is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "菜单不存在"}
        )
    updates = body.model_dump(exclude_unset=True)
    if "parent_id" in updates:
        new_parent_id = updates["parent_id"] or None
        if new_parent_id:
            if new_parent_id == menu_id:
                return JSONResponse(
                    status_code=409,
                    content={"success": False, "message": "不能将上级设为自身"},
                )
            parent = await session.get(Menu, new_parent_id)
            if parent is None:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "message": "上级菜单不存在"},
                )
            parent_map = dict(
                (
                    await session.execute(select(Menu.id, Menu.parent_id))
                ).all()
            )
            if _would_cycle(parent_map, menu_id, new_parent_id):
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "message": "不能将上级设为子菜单(会成环)",
                    },
                )
    for field, value in updates.items():
        setattr(menu, field, value)
    await session.commit()
    await session.refresh(menu)
    return JSONResponse(
        {
            "data": MenuRead.model_validate(menu).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.delete(
    "/{menu_id}", dependencies=[Depends(require_perm("system:menu:remove"))]
)
async def delete_menu(menu_id: str, session: SessionDep) -> JSONResponse:
    """删除菜单:有子菜单 409;否则清角色授权引用后删除。"""
    menu = await session.get(Menu, menu_id)
    if menu is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "菜单不存在"}
        )
    children = (
        await session.scalar(
            select(func.count())
            .select_from(Menu)
            .where(Menu.parent_id == menu_id)
        )
    ) or 0
    if children > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"菜单有 {children} 个子菜单,请先删除子菜单",
            },
        )
    await session.execute(
        RoleMenu.__table__.delete().where(RoleMenu.menu_id == menu_id)
    )
    await session.delete(menu)
    await session.commit()
    return JSONResponse({"success": True})
