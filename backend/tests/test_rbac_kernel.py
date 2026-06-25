"""RBAC 鉴权内核测试(DB 后端,经 create_all 建表 + seed_rbac 造数)。

测试意图(为何重要):
- self 范围用户的列表**绝不**含他人数据——这是数据权限的根本红线(不靠前端);
- 多角色权限聚合 / 最宽 data_scope 选取 / 部门子树 / 菜单树裁剪必须正确,
  否则要么越权(放大可见域)要么误锁(管理员被一起锁死)。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_rbac_models_roundtrip(session_factory) -> None:
    """模型可建表并往返:create_all 已建 RBAC 表,插入角色可查回。"""
    from app.models.role import Role

    async with session_factory() as s:
        s.add(
            Role(
                id="role-aaaaaa",
                name="测试角色",
                role_key="tester",
                sort=1,
                data_scope="self",
                status="0",
            )
        )
        await s.commit()

    from sqlalchemy import select

    async with session_factory() as s:
        row = (
            await s.scalars(select(Role).where(Role.role_key == "tester"))
        ).first()
        assert row is not None
        assert row.data_scope == "self"
        assert row.name == "测试角色"
