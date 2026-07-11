"""数据湖抽取(services/lake_extract.py)DB 级测试:失败路径不留空版本孤儿。

背景:``landing.add_table_member`` → ``_target_draft_version`` 建 draft 版本
时会单独提交(见其实现),若同一次 add_table_member 调用随后写成员/落盘失败,
DB 里会残留一个已提交、零成员的空版本——``extract_to_new_dataset`` 抽取多个
快照时,前面几个快照成功、某一个快照解析/落盘失败会撞上这个中间态。

本测试直接对 ``_cleanup_orphan_empty_versions`` 这个兜底函数做意图覆盖(不
经由需要真实 MinIO/湖快照下载的完整 extract_to_new_dataset 链路):

- 本次尝试新建的空版本(不在 existing_version_ids 里)必须被删除。
- 本次尝试新建但已有成员的版本不受影响(不能误删有效产出)。
- 调用前就存在的空 draft 版本(用户此前建的,不在本次失败范围内)必须保留,
  不能被这次失败的清理误伤。

按纪律:DB 级测试连 adp_test(共享慢库),本轮只写不跑,留给统一验证阶段。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.services.lake_extract import _cleanup_orphan_empty_versions


def _version(vid: str, dataset_id: str, vno: int) -> DatasetVersion:
    return DatasetVersion(
        id=vid,
        dataset_id=dataset_id,
        version_no=vno,
        storage_uri=f"pending://{dataset_id}/v{vno}/",
        format="jsonl",
        rows=0,
        size=0,
    )


@pytest.mark.asyncio
async def test_cleanup_deletes_only_new_empty_versions(
    session_factory: async_sessionmaker,
) -> None:
    """核心场景:抽取中途失败,只清掉本次新建的空版本,保留有成员的产出和
    调用前就存在的空 draft(不属于本次失败,不该被株连删除)。
    """
    dataset_id = "dset-lakeextract1"

    async with session_factory() as session:
        session.add(Dataset(id=dataset_id, name="湖抽取测试集"))
        # 调用前已存在的空 draft(比如用户之前手工建了个空版本占位)——
        # 不在本次失败范围内,清理时必须原样保留
        pre_existing_empty = _version("dsv-pre-empty", dataset_id, 1)
        session.add(pre_existing_empty)
        await session.commit()

        existing_version_ids = {pre_existing_empty.id}

        # 模拟本次抽取尝试:新建了两个版本——
        # v2 成功写入一个成员(有效产出,不该被清理)
        v2 = _version("dsv-new-with-member", dataset_id, 2)
        session.add(v2)
        session.add(
            DatasetVersionTable(
                id="dvt-1",
                dataset_version_id=v2.id,
                table_name="data",
                storage_uri="s3://bucket/x.jsonl",
                format="jsonl",
                rows=10,
                size=100,
            )
        )
        # v3 是本次尝试对下一个快照建的 draft,随后写成员失败,零成员残留
        v3_empty_orphan = _version("dsv-new-empty-orphan", dataset_id, 3)
        session.add(v3_empty_orphan)
        await session.commit()

        await _cleanup_orphan_empty_versions(session, dataset_id, existing_version_ids)

        remaining_ids = set(
            (
                await session.execute(
                    select(DatasetVersion.id).where(
                        DatasetVersion.dataset_id == dataset_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining_ids == {pre_existing_empty.id, v2.id}, remaining_ids


@pytest.mark.asyncio
async def test_cleanup_is_noop_when_nothing_new_is_empty(
    session_factory: async_sessionmaker,
) -> None:
    """反证:本次新建的版本都有成员(抽取实际是整体成功的)时,清理不该动
    任何一行——否则会误删刚落地的真实产出。
    """
    dataset_id = "dset-lakeextract2"

    async with session_factory() as session:
        session.add(Dataset(id=dataset_id, name="湖抽取测试集2"))
        await session.commit()
        existing_version_ids: set[str] = set()

        v1 = _version("dsv-ok-1", dataset_id, 1)
        session.add(v1)
        session.add(
            DatasetVersionTable(
                id="dvt-ok-1",
                dataset_version_id=v1.id,
                table_name="data",
                storage_uri="s3://bucket/y.jsonl",
                format="jsonl",
                rows=5,
                size=50,
            )
        )
        await session.commit()

        await _cleanup_orphan_empty_versions(session, dataset_id, existing_version_ids)

        remaining = (
            await session.execute(
                select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
            )
        ).scalars().all()
        assert [v.id for v in remaining] == [v1.id]
