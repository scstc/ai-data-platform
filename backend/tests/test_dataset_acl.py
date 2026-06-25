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
