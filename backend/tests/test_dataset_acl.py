"""数据集级 ACL 测试(共享/成员权限:用户/角色 × view/edit/admin)。

测试意图(为何重要):
- 私有默认:u-mgr 建的数据集,u-staff 在列表里**绝不可见**、直取 404——不靠前端隐藏;
- 授权后按级别生效(view 能看不能改、edit 能改不能管 ACL、admin 能管 ACL);
- 角色授权被持该角色者继承;owner/超管绕过;匿名沿用现状(看全部)。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_dataset_acl_model_roundtrip(session_factory) -> None:
    """DatasetAcl 可建表往返;唯一约束 (dataset,subject_type,subject_id) 拒重复。"""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.dataset_acl import DatasetAcl

    async with session_factory() as s:
        s.add(
            DatasetAcl(
                id="dac-aaaaaa",
                dataset_id="dset-x",
                subject_type="user",
                subject_id="usr-x",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        row = (
            await s.scalars(select(DatasetAcl).where(DatasetAcl.id == "dac-aaaaaa"))
        ).first()
        assert row is not None and row.level == "view"

    # 同 (dataset, subject_type, subject_id) 重复 ⇒ IntegrityError
    async with session_factory() as s:
        s.add(
            DatasetAcl(
                id="dac-bbbbbb",
                dataset_id="dset-x",
                subject_type="user",
                subject_id="usr-x",
                level="edit",
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()


async def _make_datasets(session_factory) -> None:
    """造两份私有数据集:dset-mgr(u-mgr 所有)、dset-staff(u-staff 所有)。"""
    from app.models.dataset import Dataset

    async with session_factory() as s:
        s.add_all(
            [
                Dataset(id="dset-mgr", name="mgr 的", owner="u-mgr", creator="u-mgr"),
                Dataset(id="dset-staff", name="staff 的", owner="u-staff", creator="u-staff"),
            ]
        )
        await s.commit()


async def test_visible_filter_hides_others_private(session_factory, seed_rbac) -> None:
    """u-staff 经 visible_dataset_filter 后只看到自己的;绝不含 u-mgr 的私有集(越权红线)。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.user import User
    from app.services import dataset_acl

    await _make_datasets(session_factory)
    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        stmt = await dataset_acl.visible_dataset_filter(select(Dataset), s, u)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"dset-staff"}


async def test_visible_filter_after_user_grant(session_factory, seed_rbac) -> None:
    """给 u-staff 授 dset-mgr 的 view ⇒ 其列表含 dset-mgr。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.dataset_acl import DatasetAcl
    from app.models.user import User
    from app.services import dataset_acl

    await _make_datasets(session_factory)
    async with session_factory() as s:
        s.add(
            DatasetAcl(
                id="dac-grant1",
                dataset_id="dset-mgr",
                subject_type="user",
                subject_id="u-staff",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        stmt = await dataset_acl.visible_dataset_filter(select(Dataset), s, u)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"dset-staff", "dset-mgr"}


async def test_get_acl_level_owner_and_admin_and_role_inheritance(
    session_factory, seed_rbac
) -> None:
    """owner⇒admin;超管⇒admin;角色授 edit 被持该角色者继承;无授权⇒None。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.dataset_acl import DatasetAcl
    from app.models.user import User
    from app.services import dataset_acl

    await _make_datasets(session_factory)
    async with session_factory() as s:
        # 给 r-dc(u-mgr 持有)授 dset-staff 的 edit
        s.add(
            DatasetAcl(
                id="dac-role1",
                dataset_id="dset-staff",
                subject_type="role",
                subject_id="r-dc",
                level="edit",
            )
        )
        await s.commit()

    async with session_factory() as s:
        mgr = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        sup = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        staff = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        # owner ⇒ admin
        assert await dataset_acl.get_acl_level(s, mgr, "dset-mgr") == "admin"
        # 超管 ⇒ admin
        assert await dataset_acl.get_acl_level(s, sup, "dset-staff") == "admin"
        # u-mgr 经 r-dc 角色继承 edit(对 dset-staff)
        assert await dataset_acl.get_acl_level(s, mgr, "dset-staff") == "edit"
        # u-staff 对 dset-mgr 无任何授权 ⇒ None
        assert await dataset_acl.get_acl_level(s, staff, "dset-mgr") is None


async def test_can_access_anon_passthrough(session_factory, seed_rbac) -> None:
    """匿名(user None)⇒ can_access True(兼容现状,生产无匿名)。"""
    from app.services import dataset_acl

    await _make_datasets(session_factory)
    async with session_factory() as s:
        assert await dataset_acl.can_access(s, None, "dset-mgr", "view") is True
        assert await dataset_acl.can_access(s, None, "dset-mgr", "admin") is True


async def test_can_access_level_ranking(session_factory, seed_rbac) -> None:
    """持 view 级:can view=True、can edit=False、can admin=False。"""
    from sqlalchemy import select

    from app.models.dataset_acl import DatasetAcl
    from app.models.user import User
    from app.services import dataset_acl

    await _make_datasets(session_factory)
    async with session_factory() as s:
        s.add(
            DatasetAcl(
                id="dac-rank1",
                dataset_id="dset-mgr",
                subject_type="user",
                subject_id="u-staff",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        assert await dataset_acl.can_access(s, u, "dset-mgr", "view") is True
        assert await dataset_acl.can_access(s, u, "dset-mgr", "edit") is False
        assert await dataset_acl.can_access(s, u, "dset-mgr", "admin") is False
