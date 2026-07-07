"""数据湖级 ACL:可见性过滤与级别判定,镜像 app.services.dataset_acl。

级别序:view < edit < admin。owner/creator/超管隐式 admin;匿名(user None)
在调用方放行,本模块的 can_access 对匿名直接 True(兼容现状,生产无匿名)。

subject_type 两种:user(直授特定用户)/
all(组织内所有登录用户,subject_id 固定为 ALL_SUBJECT_ID)。
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_lake import DataLake
from app.models.data_lake_acl import DataLakeAcl
from app.models.user import User

# 级别排序:数值越大权限越高
_RANK = {"view": 1, "edit": 2, "admin": 3}

# "组织内所有人"主体的固定 subject_id(该 subject_type 下每湖只此一行,见唯一约束)
ALL_SUBJECT_ID = "*"


async def _visible_lake_ids(session: AsyncSession, user: User) -> list[str]:
    """用户经 ACL 可见的数据湖 id(直授 + 组织内所有人授权),不含 owner/超管隐式项。"""
    subject_conds = [
        and_(DataLakeAcl.subject_type == "user", DataLakeAcl.subject_id == user.id),
        DataLakeAcl.subject_type == "all",
    ]
    return list(
        (
            await session.scalars(
                select(DataLakeAcl.lake_id).where(or_(*subject_conds))
            )
        ).all()
    )


async def visible_lake_filter(stmt, session: AsyncSession, user: User | None):
    """给 ``select(DataLake)`` 注入可见性 WHERE。

    匿名/超管 ⇒ 不过滤(匿名沿用现状、超管全见);否则 owner/creator 或在 ACL 可见集内。
    """
    if user is None or user.role == "admin":
        return stmt
    visible = await _visible_lake_ids(session, user)
    return stmt.where(
        or_(
            DataLake.owner == user.id,
            DataLake.creator == user.id,
            DataLake.id.in_(visible) if visible else DataLake.id.in_(["__none__"]),
        )
    )


async def get_acl_level(
    session: AsyncSession, user: User | None, lake_id: str
) -> str | None:
    """用户对某数据湖的生效级别:owner/超管⇒admin;否则直授/all 授权中最高;无⇒None。"""
    if user is None:
        return None
    if user.role == "admin":
        return "admin"
    lake = await session.get(DataLake, lake_id)
    if lake is not None and (lake.owner == user.id or lake.creator == user.id):
        return "admin"
    subject_conds = [
        and_(DataLakeAcl.subject_type == "user", DataLakeAcl.subject_id == user.id),
        DataLakeAcl.subject_type == "all",
    ]
    conds = [DataLakeAcl.lake_id == lake_id, or_(*subject_conds)]
    levels = list(
        (await session.scalars(select(DataLakeAcl.level).where(*conds))).all()
    )
    if not levels:
        return None
    return max(levels, key=lambda lv: _RANK.get(lv, 0))


async def can_access(
    session: AsyncSession,
    user: User | None,
    lake_id: str,
    required: str,
) -> bool:
    """匿名⇒True(兼容);否则生效级别 ≥ required。"""
    if user is None:
        return True
    level = await get_acl_level(session, user, lake_id)
    if level is None:
        return False
    return _RANK.get(level, 0) >= _RANK.get(required, 0)
