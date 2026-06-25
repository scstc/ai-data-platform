"""系统-菜单:getRouters(下发当前用户菜单树供前端动态路由)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, require_user
from app.models.user import User
from app.services import rbac

router = APIRouter(prefix="/system/menus", tags=["system-menus"])


@router.get("/routers")
async def get_routers(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> dict:
    """当前用户可见菜单树(M/C),供前端 patchClientRoutes 生成动态路由。"""
    return {"success": True, "data": await rbac.build_router_tree(session, user)}
