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


async def test_api_list_detail_visibility(client, session_factory, seed_rbac) -> None:
    """API 层:u-mgr 私有集对 u-staff 在列表不可见、直取 404;owner/超管可见;匿名兼容。"""
    from app.models.dataset import Dataset
    from app.services.auth import sign_token

    # 经 session_factory 造一份 u-mgr 所有的私有数据集(client 共用同一测试库)
    async with session_factory() as s:
        s.add(Dataset(id="dset-apimgr", name="api mgr", owner="u-mgr", creator="u-mgr"))
        await s.commit()

    # u-staff:列表不含、直取 404
    client.cookies.set("adp_session", sign_token("u-staff"))
    lst = await client.get("/api/v1/datasets?current=1&pageSize=50")
    assert lst.status_code == 200
    assert all(d["id"] != "dset-apimgr" for d in lst.json()["data"]), "u-staff 不应看到 u-mgr 私有集"
    miss = await client.get("/api/v1/datasets/dset-apimgr")
    assert miss.status_code == 404

    # owner u-mgr:可见
    client.cookies.set("adp_session", sign_token("u-mgr"))
    hit = await client.get("/api/v1/datasets/dset-apimgr")
    assert hit.status_code == 200

    # 超管:可见
    client.cookies.set("adp_session", sign_token("u-super"))
    assert (await client.get("/api/v1/datasets/dset-apimgr")).status_code == 200

    # 匿名:列表仍含(兼容现状)
    client.cookies.delete("adp_session")
    anon = await client.get("/api/v1/datasets?current=1&pageSize=50")
    assert anon.status_code == 200
    assert any(d["id"] == "dset-apimgr" for d in anon.json()["data"])


async def test_api_patch_gated_by_edit(client, session_factory, seed_rbac) -> None:
    """PATCH:owner 可改(200);非 owner 非 admin → 403。"""
    from app.models.dataset import Dataset
    from app.services.auth import sign_token

    async with session_factory() as s:
        s.add(Dataset(id="dset-patch", name="p", owner="u-mgr", creator="u-mgr"))
        await s.commit()

    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.patch("/api/v1/datasets/dset-patch", json={"description": "x"})
    assert r.status_code == 403, r.text

    client.cookies.set("adp_session", sign_token("u-mgr"))
    r2 = await client.patch(
        "/api/v1/datasets/dset-patch", json={"description": "changed"}
    )
    assert r2.status_code == 200, r2.text


async def test_api_acl_share_grants_visibility(
    client, session_factory, seed_rbac
) -> None:
    """owner 经 ACL 端点给 u-staff 授 view ⇒ u-staff 列表可见;非 admin 不能管 ACL。"""
    from app.models.dataset import Dataset
    from app.services.auth import sign_token

    async with session_factory() as s:
        s.add(Dataset(id="dset-share", name="s", owner="u-mgr", creator="u-mgr"))
        await s.commit()

    # u-staff 管不了 ACL(非 owner 非 admin)→ 403
    client.cookies.set("adp_session", sign_token("u-staff"))
    bad = await client.post(
        "/api/v1/datasets/dset-share/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert bad.status_code == 403, bad.text

    # 授权前:u-staff 看不到
    lst0 = await client.get("/api/v1/datasets?current=1&pageSize=50")
    assert all(d["id"] != "dset-share" for d in lst0.json()["data"])

    # owner 授 u-staff view
    client.cookies.set("adp_session", sign_token("u-mgr"))
    add = await client.post(
        "/api/v1/datasets/dset-share/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert add.status_code == 200, add.text

    # 授权后:u-staff 列表可见
    client.cookies.set("adp_session", sign_token("u-staff"))
    lst1 = await client.get("/api/v1/datasets?current=1&pageSize=50")
    assert any(d["id"] == "dset-share" for d in lst1.json()["data"])


async def test_api_delete_owner_only(client, session_factory, seed_rbac) -> None:
    """DELETE:非 owner 非 admin → 403;owner → 200。"""
    from app.models.dataset import Dataset
    from app.services.auth import sign_token

    async with session_factory() as s:
        s.add(Dataset(id="dset-del", name="d", owner="u-mgr", creator="u-mgr"))
        await s.commit()

    client.cookies.set("adp_session", sign_token("u-staff"))
    assert (await client.delete("/api/v1/datasets/dset-del")).status_code == 403

    client.cookies.set("adp_session", sign_token("u-mgr"))
    assert (await client.delete("/api/v1/datasets/dset-del")).status_code == 200
