"""RBAC 鉴权内核:角色/权限聚合、数据范围解析、菜单树、数据权限过滤。

纯计算 + DB 只读查询,不写库。会话由调用方注入,便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.menu import Menu
from app.models.rbac_links import RoleDept, RoleMenu, UserRole
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


# 数据范围由宽到窄;取用户多角色中最宽者
_SCOPE_ORDER = ["all", "dept_and_child", "custom", "dept", "self"]


@dataclass
class ScopeContext:
    """生效数据范围:scope + 预解析的可见部门集(dept_and_child/custom 用)。"""

    scope: str
    dept_ids: set[str] = field(default_factory=set)
    user_id: str = ""
    user_dept_id: str | None = None


async def _subtree_dept_ids(session: AsyncSession, dept_id: str) -> set[str]:
    """部门子树(含自身):自身 + ancestors 命中 '...,<dept_id>,' 的后代。"""
    if not dept_id:
        return set()
    like = f"%,{dept_id},%"
    stmt = select(Department.id).where(
        (Department.id == dept_id) | (Department.ancestors.like(like))
    )
    return set((await session.scalars(stmt)).all())


async def get_effective_data_scope(
    session: AsyncSession, user: User
) -> ScopeContext:
    """解析用户生效数据范围。admin ⇒ all;多角色取最宽;无角色退化为 self。"""
    if user.role == "admin":
        return ScopeContext("all", user_id=user.id, user_dept_id=user.dept_id)
    roles = await get_user_roles(session, user)
    scopes = {r.data_scope for r in roles}
    # 无角色 ⇒ 退化为 self(只能看自己),绝不放成 all
    scope = next((s for s in _SCOPE_ORDER if s in scopes), "self")
    ctx = ScopeContext(scope, user_id=user.id, user_dept_id=user.dept_id)
    if scope == "dept_and_child" and user.dept_id:
        ctx.dept_ids = await _subtree_dept_ids(session, user.dept_id)
    elif scope == "custom":
        role_ids = [r.id for r in roles if r.data_scope == "custom"]
        if role_ids:
            stmt = select(RoleDept.dept_id).where(RoleDept.role_id.in_(role_ids))
            ctx.dept_ids = set((await session.scalars(stmt)).all())
    return ctx


def apply_data_scope(stmt, model, creator_attr: str, ctx: ScopeContext):
    """按生效范围给 SELECT 注入 WHERE。creator_attr 为该模型的归属列名。

    all 不过滤;self 比 creator==本人;dept 比 dept_id==本人部门;
    dept_and_child/custom 比 dept_id ∈ 预解析集(空集 ⇒ 匹配不到,安全失败)。
    """
    if ctx.scope == "all":
        return stmt
    if ctx.scope == "self":
        return stmt.where(getattr(model, creator_attr) == ctx.user_id)
    if ctx.scope == "dept":
        return stmt.where(model.dept_id == ctx.user_dept_id)
    # dept_and_child | custom
    if not ctx.dept_ids:
        return stmt.where(model.dept_id.in_(["__none__"]))
    return stmt.where(model.dept_id.in_(ctx.dept_ids))
