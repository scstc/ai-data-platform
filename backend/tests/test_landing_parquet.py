"""land_records parquet 落地 + 失败回退 jsonl 测试。

测试覆盖:
- storage_format="parquet" → version.format == "parquet", uri ends data.parquet
- 同列异构记录 → records_to_parquet_bytes 抛 ParquetCodecError → 回退 jsonl
"""
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import landing


@pytest.mark.asyncio
async def test_land_records_parquet_sets_format(monkeypatch, session_factory: async_sessionmaker):
    # db_session: 沿用 conftest 既有 async session_factory fixture
    async def fake_upload_parquet(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.parquet"

    monkeypatch.setattr(
        "app.services.external_store.upload_parquet_to_uploads", fake_upload_parquet
    )
    async with session_factory() as session:
        ds, ver = await landing.land_records(
            session,
            [{"id": 1, "amt": Decimal("9.9")}],
            dataset_name="t",
            source_kind="database",
            storage_format="parquet",
        )
    assert ver.format == "parquet"
    assert ver.storage_uri.endswith("data.parquet")


@pytest.mark.asyncio
async def test_land_records_parquet_falls_back_to_jsonl(monkeypatch, session_factory: async_sessionmaker):
    # 嵌套异构 → records_to_parquet_bytes 抛 ParquetCodecError → 回退 jsonl
    async def fake_upload_jsonl(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.jsonl"

    monkeypatch.setattr(
        "app.services.external_store.upload_jsonl_to_uploads", fake_upload_jsonl
    )
    async with session_factory() as session:
        ds, ver = await landing.land_records(
            session,
            [{"x": 1}, {"x": {"nested": True}}],
            dataset_name="t",
            source_kind="database",
            storage_format="parquet",
        )
    assert ver.format == "jsonl"
    assert ver.storage_uri.endswith("data.jsonl")
