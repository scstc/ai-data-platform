"""RBAC 鉴权内核:角色/权限聚合、数据范围解析、菜单树、数据权限过滤。

纯计算 + DB 只读查询,不写库。会话由调用方注入,便于单测。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.menu import Menu
from app.models.rbac_links import RoleMenu, UserRole
from app.models.role import Role
from app.models.user import User

# 超管权限通配:匹配任意 code
WILDCARD = "*:*:*"


def has_perm(perms: set[str], code: str) -> bool:
    """权限判定:持通配或精确命中即放行。"""
    return WILDCARD in perms or code in perms


async def get_user_roles(session: AsyncSession, user: User) -> list[Role]:
    """用户的角色列表(经 user_roles)。无则空列表。"""
    stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    return list((await session.scalars(stmt)).all())


async def get_user_perms(session: AsyncSession, user: User) -> set[str]:
    """聚合用户权限码。遗留 admin(user.role=='admin')⇒ 通配。

    否则:经 user_roles→role_menus→menus.perms 收集非空 perms。
    """
    if user.role == "admin":
        return {WILDCARD}
    stmt = (
        select(Menu.perms)
        .join(RoleMenu, RoleMenu.menu_id == Menu.id)
        .join(UserRole, UserRole.role_id == RoleMenu.role_id)
        .where(UserRole.user_id == user.id, Menu.perms.is_not(None))
    )
    return {p for p in (await session.scalars(stmt)).all() if p}
