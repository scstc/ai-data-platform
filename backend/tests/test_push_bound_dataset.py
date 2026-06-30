"""API 推送绑定数据集存在性校验(Task 11)。

数据集优先流程:``datasource.config.boundDatasetId`` 指向不存在的数据集时,
``land_push_records`` 必须 fail loud(LandingError),不再隐式重建数据集。
"""

from __future__ import annotations

import pytest

from app.models.datasource import DataSource
from app.services.connectors.push import land_push_records
from app.services.landing import LandingError


async def _make_push_datasource(
    session, *, bound_dataset_id: str | None
) -> DataSource:
    """建一个最小 api 推送数据源(可带 boundDatasetId)。"""
    config: dict = {}
    if bound_dataset_id is not None:
        config["boundDatasetId"] = bound_dataset_id
    ds = DataSource(
        id="ds-push-1",
        name="推送源",
        type="api",
        status="active",
        config=config,
        creator="admin",
    )
    session.add(ds)
    await session.commit()
    await session.refresh(ds)
    return ds


@pytest.mark.asyncio
async def test_push_rejects_unknown_bound_dataset(db_session):
    """boundDatasetId 指向不存在的数据集 → LandingError(拒绝落地)。"""
    ds_src = await _make_push_datasource(db_session, bound_dataset_id="dset-nope")
    with pytest.raises(LandingError):
        await land_push_records(db_session, ds_src, [{"x": 1}])
