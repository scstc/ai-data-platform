"""数据湖级 ACL 测试(共享/成员权限:指定用户/组织内所有人 × view/edit/admin)。

测试意图(为何重要):
- 私有默认:u-mgr 建的数据湖,u-staff 在列表里不可见、直取 404——不靠前端隐藏;
- 授权后按级别生效(view 能看不能改、edit 能改不能管 ACL、admin 能管 ACL);
- owner/creator/超管绕过;匿名沿用现状(看全部);角色授权不存在(仅 user/all)。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _make_lakes(session_factory) -> None:
    """造两份私有数据湖:lake-mgr(u-mgr 所有)、lake-staff(u-staff 所有)。"""
    from app.models.data_lake import DataLake

    async with session_factory() as s:
        s.add_all(
            [
                DataLake(
                    id="lake-mgr", name="mgr 的湖", owner="u-mgr", creator="u-mgr"
                ),
                DataLake(
                    id="lake-staff",
                    name="staff 的湖",
                    owner="u-staff",
                    creator="u-staff",
                ),
            ]
        )
        await s.commit()


async def test_lake_acl_model_roundtrip(session_factory) -> None:
    """DataLakeAcl 可建表往返;唯一约束 (lake,subject_type,subject_id) 拒重复。"""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.data_lake_acl import DataLakeAcl

    async with session_factory() as s:
        s.add(
            DataLakeAcl(
                id="lac-aaaaaa",
                lake_id="lake-x",
                subject_type="user",
                subject_id="usr-x",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        row = (
            await s.scalars(select(DataLakeAcl).where(DataLakeAcl.id == "lac-aaaaaa"))
        ).first()
        assert row is not None and row.level == "view"

    async with session_factory() as s:
        s.add(
            DataLakeAcl(
                id="lac-bbbbbb",
                lake_id="lake-x",
                subject_type="user",
                subject_id="usr-x",
                level="edit",
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_visible_filter_and_grants(session_factory, seed_rbac) -> None:
    """u-staff 默认只见自己的湖;user 直授与 all 授权都能带来可见性。"""
    from sqlalchemy import select

    from app.models.data_lake import DataLake
    from app.models.data_lake_acl import DataLakeAcl
    from app.models.user import User
    from app.services import lake_acl

    await _make_lakes(session_factory)
    async with session_factory() as s:
        staff = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        stmt = await lake_acl.visible_lake_filter(select(DataLake), s, staff)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"lake-staff"}, "默认私有:不见 u-mgr 的湖(越权红线)"

    async with session_factory() as s:
        s.add(
            DataLakeAcl(
                id="lac-grant1",
                lake_id="lake-mgr",
                subject_type="user",
                subject_id="u-staff",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        staff = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        stmt = await lake_acl.visible_lake_filter(select(DataLake), s, staff)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"lake-staff", "lake-mgr"}


async def test_get_acl_level_owner_admin_and_rank(session_factory, seed_rbac) -> None:
    """owner⇒admin;超管⇒admin;view 授权者 can edit=False;无授权⇒None。"""
    from sqlalchemy import select

    from app.models.data_lake_acl import DataLakeAcl
    from app.models.user import User
    from app.services import lake_acl

    await _make_lakes(session_factory)
    async with session_factory() as s:
        s.add(
            DataLakeAcl(
                id="lac-rank1",
                lake_id="lake-mgr",
                subject_type="user",
                subject_id="u-staff",
                level="view",
            )
        )
        await s.commit()

    async with session_factory() as s:
        mgr = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        sup = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        staff = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        assert await lake_acl.get_acl_level(s, mgr, "lake-mgr") == "admin"
        assert await lake_acl.get_acl_level(s, sup, "lake-mgr") == "admin"
        assert await lake_acl.get_acl_level(s, staff, "lake-mgr") == "view"
        assert await lake_acl.can_access(s, staff, "lake-mgr", "view") is True
        assert await lake_acl.can_access(s, staff, "lake-mgr", "edit") is False
        assert await lake_acl.get_acl_level(s, mgr, "lake-staff") is None


async def test_api_list_detail_visibility(client, session_factory, seed_rbac) -> None:
    """API 层:u-mgr 私有湖对 u-staff 列表不可见、直取 404;owner/超管可见含 myLevel。"""
    from app.services.auth import sign_token

    await _make_lakes(session_factory)

    # u-staff:列表不含、直取 404
    client.cookies.set("adp_session", sign_token("u-staff"))
    lst = await client.get("/api/v1/data-lakes?page=1&pageSize=50")
    assert lst.status_code == 200
    assert all(d["id"] != "lake-mgr" for d in lst.json()["data"]), (
        "u-staff 不应看到 u-mgr 私有湖"
    )
    miss = await client.get("/api/v1/data-lakes/lake-mgr")
    assert miss.status_code == 404

    # owner u-mgr:可见,myLevel=admin
    client.cookies.set("adp_session", sign_token("u-mgr"))
    hit = await client.get("/api/v1/data-lakes/lake-mgr")
    assert hit.status_code == 200, hit.text
    assert hit.json()["myLevel"] == "admin"

    # 超管:可见
    client.cookies.set("adp_session", sign_token("u-super"))
    assert (await client.get("/api/v1/data-lakes/lake-mgr")).status_code == 200


async def test_api_patch_and_delete_gated(client, session_factory, seed_rbac) -> None:
    """PATCH 需 edit 级(无授权 403,owner 200);DELETE 仅 owner/creator/超管。"""
    from app.services.auth import sign_token

    await _make_lakes(session_factory)

    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.patch("/api/v1/data-lakes/lake-mgr", json={"description": "x"})
    assert r.status_code == 403, r.text
    assert (await client.delete("/api/v1/data-lakes/lake-mgr")).status_code == 403

    client.cookies.set("adp_session", sign_token("u-mgr"))
    r2 = await client.patch(
        "/api/v1/data-lakes/lake-mgr", json={"description": "changed"}
    )
    assert r2.status_code == 200, r2.text
    assert (await client.delete("/api/v1/data-lakes/lake-mgr")).status_code == 200


async def test_api_acl_share_flow(client, session_factory, seed_rbac) -> None:
    """owner 经 ACL 端点授 u-staff view ⇒ u-staff 列表可见;非 admin 级管不了 ACL;
    subjectType=role 已取消 → 400;all 归一 subjectId="*"。"""
    from app.services.auth import sign_token

    await _make_lakes(session_factory)

    # u-staff 管不了 ACL(非 owner 非 admin)→ 403
    client.cookies.set("adp_session", sign_token("u-staff"))
    bad = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert bad.status_code == 403, bad.text

    # owner 授 u-staff view;重复授权 409;role → 400
    client.cookies.set("adp_session", sign_token("u-mgr"))
    add = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert add.status_code == 200, add.text
    dup = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "edit"},
    )
    assert dup.status_code == 409, dup.text
    role = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "role", "subjectId": "r-dc", "level": "view"},
    )
    assert role.status_code == 400, role.text
    allrow = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "all", "subjectId": "ignored", "level": "view"},
    )
    assert allrow.status_code == 200, allrow.text
    assert allrow.json()["data"]["subjectId"] == "*"

    # list 回填 subjectName
    lst = await client.get("/api/v1/data-lakes/lake-mgr/acl")
    assert lst.status_code == 200, lst.text
    by_type = {d["subjectType"]: d["subjectName"] for d in lst.json()["data"]}
    assert by_type["user"] == "u-staff"
    assert by_type["all"] == "组织内所有人"

    # 授权后:u-staff 列表可见
    client.cookies.set("adp_session", sign_token("u-staff"))
    lst1 = await client.get("/api/v1/data-lakes?page=1&pageSize=50")
    assert any(d["id"] == "lake-mgr" for d in lst1.json()["data"])


async def test_api_extract_to_dataset_gated(client, session_factory, seed_rbac) -> None:
    """抽取生成数据集:需源湖 edit 及以上;view/无授权 403,
    升到 edit 后放行(ACL 门控先于业务校验)。"""
    from app.services.auth import sign_token

    await _make_lakes(session_factory)
    body = {"snapshotIds": ["snap-not-exist"], "datasetName": "extracted"}

    # 无授权 → 403
    client.cookies.set("adp_session", sign_token("u-staff"))
    denied = await client.post(
        "/api/v1/data-lakes/lake-mgr/extract-to-dataset", json=body
    )
    assert denied.status_code == 403, denied.text

    # owner 授 view → 抽取仍 403(view 只读,不能加工)
    client.cookies.set("adp_session", sign_token("u-mgr"))
    grant = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert grant.status_code == 200, grant.text
    acl_id = grant.json()["data"]["id"]
    client.cookies.set("adp_session", sign_token("u-staff"))
    still_denied = await client.post(
        "/api/v1/data-lakes/lake-mgr/extract-to-dataset", json=body
    )
    assert still_denied.status_code == 403, still_denied.text

    # 升到 edit → 通过 ACL 门控(快照不存在走业务层错误,不是 403 权限错误)
    client.cookies.set("adp_session", sign_token("u-mgr"))
    upd = await client.put(
        f"/api/v1/data-lakes/lake-mgr/acl/{acl_id}", json={"level": "edit"}
    )
    assert upd.status_code == 200, upd.text
    client.cookies.set("adp_session", sign_token("u-staff"))
    passed = await client.post(
        "/api/v1/data-lakes/lake-mgr/extract-to-dataset", json=body
    )
    assert passed.status_code != 403, passed.text


async def test_api_acl_candidates_and_level_update(
    client, session_factory, seed_rbac
) -> None:
    """candidates:非 admin 403、type=role 400、可搜到用户;PUT 改级别生效。"""
    from app.services.auth import sign_token

    await _make_lakes(session_factory)

    client.cookies.set("adp_session", sign_token("u-staff"))
    denied = await client.get(
        "/api/v1/data-lakes/lake-mgr/acl/candidates?q=staff&type=user"
    )
    assert denied.status_code == 403, denied.text

    client.cookies.set("adp_session", sign_token("u-mgr"))
    users = await client.get(
        "/api/v1/data-lakes/lake-mgr/acl/candidates?q=staff&type=user"
    )
    assert users.status_code == 200, users.text
    assert any(u["id"] == "u-staff" for u in users.json()["data"])
    roles = await client.get(
        "/api/v1/data-lakes/lake-mgr/acl/candidates?q=x&type=role"
    )
    assert roles.status_code == 400, roles.text

    add = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    acl_id = add.json()["data"]["id"]
    upd = await client.put(
        f"/api/v1/data-lakes/lake-mgr/acl/{acl_id}", json={"level": "edit"}
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["data"]["level"] == "edit"

    # 升到 edit 后 u-staff 可 PATCH 湖元数据
    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.patch("/api/v1/data-lakes/lake-mgr", json={"description": "y"})
    assert r.status_code == 200, r.text

    # 移除授权后回到 403
    client.cookies.set("adp_session", sign_token("u-mgr"))
    rm = await client.delete(f"/api/v1/data-lakes/lake-mgr/acl/{acl_id}")
    assert rm.status_code == 200, rm.text
    client.cookies.set("adp_session", sign_token("u-staff"))
    r2 = await client.patch("/api/v1/data-lakes/lake-mgr", json={"description": "z"})
    assert r2.status_code == 403, r2.text


async def test_api_snapshot_reads_gated(client, session_factory, seed_rbac) -> None:
    """快照粒度读接口(详情/presigned-url/preview):未登录 401;
    登录但对源湖无 view 404(不泄露存在性);授 view 后详情放行。"""
    from app.models.data_lake import DataLakeSnapshot
    from app.services.auth import sign_token

    await _make_lakes(session_factory)
    async with session_factory() as s:
        s.add(
            DataLakeSnapshot(
                id="snap-aclro1",
                lake_id="lake-mgr",
                source_version="source_v20260701_01_local",
                storage_uri="s3://data-lake/lake-mgr/source_v20260701_01_local/a.jsonl",
                storage_format="jsonl",
                data_category="text",
                upload_channel="local",
            )
        )
        await s.commit()

    paths = [
        "/api/v1/data-lake-snapshots/snap-aclro1",
        "/api/v1/data-lake-snapshots/snap-aclro1/presigned-url",
        "/api/v1/data-lake-snapshots/snap-aclro1/preview",
    ]

    # 未登录 → 401
    client.cookies.clear()
    for p in paths:
        r = await client.get(p)
        assert r.status_code == 401, f"{p}: {r.status_code} {r.text}"

    # 登录但对源湖无授权 → 与不存在同样 404
    client.cookies.set("adp_session", sign_token("u-staff"))
    for p in paths:
        r = await client.get(p)
        assert r.status_code == 404, f"{p}: {r.status_code} {r.text}"

    # owner 授 view → 详情放行(presigned-url/preview 依赖对象存储,门控之后
    # 走业务逻辑,不在本用例断言)
    client.cookies.set("adp_session", sign_token("u-mgr"))
    grant = await client.post(
        "/api/v1/data-lakes/lake-mgr/acl",
        json={"subjectType": "user", "subjectId": "u-staff", "level": "view"},
    )
    assert grant.status_code == 200, grant.text
    client.cookies.set("adp_session", sign_token("u-staff"))
    detail = await client.get(paths[0])
    assert detail.status_code == 200, detail.text
    assert detail.json()["lakeId"] == "lake-mgr"
