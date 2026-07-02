"""测试成员级独立算子配置。"""
import pytest
from sqlalchemy import select

from app.models import DatasetVersionTable
from app.services.engine import run_process_job, _get_version_members
from app.services.landing import create_dataset, add_table_member


@pytest.mark.asyncio
async def test_member_independent_operators(db_session):
    """测试每个成员使用不同的算子"""
    # 1. 创建多成员数据集
    ds = await create_dataset(db_session, name="独立算子测试集")
    v1, _ = await add_table_member(
        db_session, ds.id,
        [{"text": "hello123world"}, {"text": "test456data"}],
        table_name="users"
    )
    await add_table_member(
        db_session, ds.id,
        [{"text": "foo bar baz"}, {"text": "alpha beta gamma"}],
        table_name="orders"
    )
    await db_session.refresh(v1)

    # 2. 为不同成员配置不同算子
    member_configs = [
        {
            "member_name": "users",
            "operators": [{"name": "alphanumeric_filter", "params": None}]
        },
        {
            "member_name": "orders",
            "operators": [
                {"name": "words_num_filter", "params": {"min_num": 2}},
                {"name": "text_length_filter", "params": {"min_len": 5}}
            ]
        }
    ]

    v2, _, _ = await run_process_job(
        db_session,
        job_id="job-independent",
        input_version=v1,
        member_configs=member_configs,
    )

    # 3. 验证产出版本包含两个成员
    members = await _get_version_members(db_session, v2.id)
    assert len(members) == 2
    assert {m.table_name for m in members} == {"users", "orders"}

    # 4. 验证每个成员都有数据（算子过滤后仍有行）
    for m in members:
        assert m.rows > 0, f"{m.table_name} 应该有数据"


@pytest.mark.asyncio
async def test_member_configs_validation(db_session):
    """测试 member_configs 校验"""
    ds = await create_dataset(db_session, name="校验测试")
    v1, _ = await add_table_member(db_session, ds.id, [{"text": "a"}], table_name="t1")
    await db_session.refresh(v1)

    # 指定不存在的成员应报错
    with pytest.raises(Exception):
        await run_process_job(
            db_session,
            job_id="job-bad",
            input_version=v1,
            member_configs=[
                {"member_name": "nonexistent", "operators": [{"name": "alphanumeric_filter"}]}
            ],
        )
