import pytest

from app.api.v1.datasets import _members_of
from app.services.landing import add_table_member, create_dataset


@pytest.mark.asyncio
async def test_members_lists_each_table(db_session):
    ds = await create_dataset(db_session, name="多表读")
    ver, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="users")
    await add_table_member(db_session, ds.id, [{"b": 2}], table_name="orders")
    await db_session.refresh(ver)
    members = await _members_of(ver, db_session)
    names = {m.name for m in members}
    assert {"users", "orders"} <= names


@pytest.mark.asyncio
async def test_single_table_dataset_one_member(db_session):
    """回填/单表场景:land_records 产物有一个 'data' 成员,_members_of 返回它。"""
    ds = await create_dataset(db_session, name="单表读")
    ver, _ = await add_table_member(db_session, ds.id, [{"x": 1}], table_name="data")
    await db_session.refresh(ver)
    members = await _members_of(ver, db_session)
    assert [m.name for m in members] == ["data"]
