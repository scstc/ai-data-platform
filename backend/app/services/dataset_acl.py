"""数据集级 ACL:可见性过滤与级别判定(纯计算 + 只读查询)。

级别序:view < edit < admin。owner/超管隐式 admin;匿名(user None)在调用方放行,
本模块的 can_access 对匿名直接 True(兼容现状,生产无匿名)。

subject_type 两种生效:user(直授特定用户)/
all(组织内所有登录用户,subject_id 固定为 ALL_SUBJECT_ID)。
role 授权已取消:存量 role 行不再参与可见性/级别计算,仅在列表展示供删除。
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.dataset_acl import DatasetAcl
from app.models.user import User

# 级别排序:数值越大权限越高
_RANK = {"view": 1, "edit": 2, "admin": 3}

# "组织内所有人"主体的固定 subject_id(该 subject_type 下整表只此一行,见唯一约束)
ALL_SUBJECT_ID = "*"


async def _visible_dataset_ids(
    session: AsyncSession, user: User
) -> list[str]:
    """用户经 ACL 可见的数据集 id(直授 + 组织内所有人授权),不含 owner/超管隐式项。"""
    subject_conds = [
        and_(DatasetAcl.subject_type == "user", DatasetAcl.subject_id == user.id),
        DatasetAcl.subject_type == "all",
    ]
    return list(
        (
            await session.scalars(
                select(DatasetAcl.dataset_id).where(or_(*subject_conds))
            )
        ).all()
    )


async def visible_dataset_filter(stmt, session: AsyncSession, user: User | None):
    """给 ``select(Dataset)`` 注入可见性 WHERE。

    匿名/超管 ⇒ 不过滤(匿名沿用现状、超管全见);否则 owner/creator 或在 ACL 可见集内。
    """
    if user is None or user.role == "admin":
        return stmt
    visible = await _visible_dataset_ids(session, user)
    return stmt.where(
        or_(
            Dataset.owner == user.id,
            Dataset.creator == user.id,
            Dataset.id.in_(visible) if visible else Dataset.id.in_(["__none__"]),
        )
    )


async def get_acl_level(
    session: AsyncSession, user: User | None, dataset_id: str
) -> str | None:
    """用户对某数据集的生效级别:owner/超管⇒admin;否则直授/all 授权中最高;无⇒None。"""
    if user is None:
        return None
    if user.role == "admin":
        return "admin"
    dataset = await session.get(Dataset, dataset_id)
    if dataset is not None and (
        dataset.owner == user.id or dataset.creator == user.id
    ):
        return "admin"
    subject_conds = [
        and_(DatasetAcl.subject_type == "user", DatasetAcl.subject_id == user.id),
        DatasetAcl.subject_type == "all",
    ]
    conds = [DatasetAcl.dataset_id == dataset_id, or_(*subject_conds)]
    levels = list((await session.scalars(select(DatasetAcl.level).where(*conds))).all())
    if not levels:
        return None
    return max(levels, key=lambda lv: _RANK.get(lv, 0))


async def can_access(
    session: AsyncSession,
    user: User | None,
    dataset_id: str,
    required: str,
) -> bool:
    """匿名⇒True(兼容);否则生效级别 ≥ required。"""
    if user is None:
        return True
    level = await get_acl_level(session, user, dataset_id)
    if level is None:
        return False
    return _RANK.get(level, 0) >= _RANK.get(required, 0)
