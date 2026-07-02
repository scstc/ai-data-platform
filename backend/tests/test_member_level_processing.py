"""测试成员级算子配置功能。"""
import pytest
from sqlalchemy import select

from app.models import DatasetVersionTable
from app.services.engine import run_process_job, _get_version_members
from app.services.landing import create_dataset, add_table_member


@pytest.mark.asyncio
async def test_get_version_members(db_session):
    """测试查询版本成员"""
    ds = await create_dataset(db_session, name="测试集")
    v, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="users")
    await add_table_member(db_session, ds.id, [{"b": 2}], table_name="orders")

    members = await _get_version_members(db_session, v.id)
    assert len(members) == 2
    assert {m.table_name for m in members} == {"users", "orders"}


@pytest.mark.asyncio
async def test_process_selected_members(db_session):
    """测试只处理选定成员"""
    # 1. 创建多成员数据集
    ds = await create_dataset(db_session, name="多表集")
    v1, _ = await add_table_member(db_session, ds.id, [{"text": "hello world"}], table_name="users")
    await add_table_member(db_session, ds.id, [{"text": "foo bar"}], table_name="orders")
    await add_table_member(db_session, ds.id, [{"text": "test data"}], table_name="logs")
    await db_session.refresh(v1)

    # 2. 只处理 users 和 orders（使用简单的过滤算子）
    operators = [{"name": "alphanumeric_filter", "params": None}]
    v2, _, _ = await run_process_job(
        db_session,
        job_id="job-test",
        input_version=v1,
        operators=operators,
        target_members=["users", "orders"],
    )

    # 3. 验证产出版本只有 2 个成员
    members = await _get_version_members(db_session, v2.id)
    assert len(members) == 2
    assert {m.table_name for m in members} == {"users", "orders"}

    # 4. 验证版本汇总统计
    assert v2.rows == sum(m.rows or 0 for m in members)
    assert v2.format == "multi"


@pytest.mark.asyncio
async def test_process_all_members_when_none(db_session):
    """测试 target_members=None 时处理所有成员"""
    ds = await create_dataset(db_session, name="全处理集")
    v1, _ = await add_table_member(db_session, ds.id, [{"text": "a"}], table_name="t1")
    await add_table_member(db_session, ds.id, [{"text": "b"}], table_name="t2")
    await db_session.refresh(v1)

    operators = [{"name": "alphanumeric_filter", "params": None}]
    v2, _, _ = await run_process_job(
        db_session,
        job_id="job-test2",
        input_version=v1,
        operators=operators,
        target_members=None,  # 显式传 None
    )

    members = await _get_version_members(db_session, v2.id)
    assert len(members) == 2  # 处理了所有成员
