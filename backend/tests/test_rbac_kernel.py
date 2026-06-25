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


async def test_user_and_business_have_dept_id(session_factory) -> None:
    """User 与各业务模型都具备 dept_id 列(create_all 后可写读)。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.user import User

    async with session_factory() as s:
        s.add(
            User(
                id="usr-deptck",
                username="deptck",
                password_hash="x",
                role="user",
                dept_id="dept-000000",
            )
        )
        s.add(Dataset(id="dset-deptck", name="d", dept_id="dept-000000"))
        await s.commit()

    async with session_factory() as s:
        u = (
            await s.scalars(select(User).where(User.id == "usr-deptck"))
        ).first()
        d = (
            await s.scalars(select(Dataset).where(Dataset.id == "dset-deptck"))
        ).first()
        assert u.dept_id == "dept-000000"
        assert d.dept_id == "dept-000000"


async def test_get_user_perms_admin_is_wildcard(session_factory, seed_rbac) -> None:
    """role 列为 admin 的用户 ⇒ 通配权限(桥接遗留超管)。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        perms = await rbac.get_user_perms(s, u)
        assert perms == {"*:*:*"}
        assert rbac.has_perm(perms, "system:user:add") is True


async def test_get_user_perms_aggregates_granted_menus(
    session_factory, seed_rbac
) -> None:
    """u-mgr(角色 r-dc 授 m-add)聚合得 system:user:add;无关 perm 为假。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        perms = await rbac.get_user_perms(s, u)
        assert "system:user:add" in perms
        assert rbac.has_perm(perms, "system:role:remove") is False


async def test_effective_scope_widest_and_subtree(
    session_factory, seed_rbac
) -> None:
    """u-mgr 角色 r-dc=dept_and_child,dept=d-root ⇒ dept_ids 含 d-root 及子 d-a/d-b。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        ctx = await rbac.get_effective_data_scope(s, u)
        assert ctx.scope == "dept_and_child"
        assert {"d-root", "d-a", "d-b"} <= ctx.dept_ids


async def test_apply_data_scope_self_excludes_others(
    session_factory, seed_rbac
) -> None:
    """self 范围:apply_data_scope 后只查得本人 creator 的数据集(越权红线)。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        s.add_all(
            [
                Dataset(id="dset-mine", name="mine", creator="u-staff", dept_id="d-a"),
                Dataset(id="dset-other", name="other", creator="u-mgr", dept_id="d-root"),
            ]
        )
        await s.commit()
        u = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        ctx = await rbac.get_effective_data_scope(s, u)
        stmt = rbac.apply_data_scope(select(Dataset), Dataset, "creator", ctx)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"dset-mine"}  # 绝不含 dset-other


async def test_build_router_tree_filters_buttons_and_by_role(
    session_factory, seed_rbac
) -> None:
    """u-mgr 授 m-sys/m-user(+按钮 m-add):路由树含 system 目录及 user 子项,

    但**不含** F 按钮 m-add(按钮不进路由)。
    """
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        tree = await rbac.build_router_tree(s, u)
        assert len(tree) == 1 and tree[0]["path"] == "/system"
        children = tree[0]["children"]
        assert [c["path"] for c in children] == ["/system/user"]
        assert children[0]["component"] == "system/user"
        # 按钮不出现在任何层级
        flat = [tree[0], *children, *[g for c in children for g in c["children"]]]
        assert all(n["path"] is not None for n in flat)


async def test_build_router_tree_admin_sees_all(
    session_factory, seed_rbac
) -> None:
    """admin(u-super)无 role_menus 授权也应看到全部 M/C 菜单。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        tree = await rbac.build_router_tree(s, u)
        assert tree and tree[0]["path"] == "/system"
        assert [c["path"] for c in tree[0]["children"]] == ["/system/user"]
