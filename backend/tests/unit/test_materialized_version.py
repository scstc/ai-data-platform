"""materialized_version parquet 分支纯单测。

不依赖 DB / 真实 S3：monkeypatch cached_bytes + platform_config,
验证 parquet 版本直接 yield 本地 .parquet 路径供 dj ParquetFormatter 原生读取。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_materialized_version_parquet_yields_parquet_path(monkeypatch, tmp_path):
    from app.models.dataset_version import DatasetVersion
    from app.services import external_store as es
    from app.services.landing import records_to_parquet_bytes

    parquet_bytes = records_to_parquet_bytes([{"id": 1}])

    async def fake_cached_bytes(cfg, bucket, key):
        return parquet_bytes

    monkeypatch.setattr(es, "cached_bytes", fake_cached_bytes)
    monkeypatch.setattr(
        es, "platform_config", lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"}
    )
    v = DatasetVersion(
        id="dsv-x", dataset_id="dset-x", version_no=1,
        storage_uri="s3://uploads/dset-x/v1/data.parquet", format="parquet",
        origin="managed",
    )
    async with es.materialized_version(v, session=None) as p:
        assert str(p).endswith(".parquet")
        assert p.exists()
