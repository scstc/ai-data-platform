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


# --- UTF-8 BOM 兼容回归(2026-06-30 GIS 网点 ATM 上传报错修复) ---


def test_geojson_to_records_strips_utf8_bom():
    """带 UTF-8 BOM 的 GeoJSON 应被自动剥离,FeatureCollection 正常展平。

    复现来源:Windows/Excel 工具链保存的 JSON 默认带 BOM,
    平台需兼容(landing._geojson_to_records 已改用 utf-8-sig)。
    """
    body = (
        b"\xef\xbb\xbf"
        b'{"type":"FeatureCollection","features":['
        b'{"type":"Feature","geometry":{"type":"Point","coordinates":[112.9,28.2]},'
        b'"properties":{"name":"ATM-001","category":"bank"}}'
        b"]}"
    )
    records = landing._geojson_to_records(body)
    assert len(records) == 1
    assert records[0]["name"] == "ATM-001"
    assert records[0]["category"] == "bank"
    assert records[0]["lon"] == 112.9
    assert records[0]["lat"] == 28.2
    assert records[0]["geometry_type"] == "Point"


def test_normalize_records_json_strips_utf8_bom():
    """通用 JSON 入口:带 BOM 的顶层数组应被剥离 BOM 后正常规范化。"""
    body = b"\xef\xbb\xbf" + b'[{"text":"hello"},{"text":"world"}]'
    records = landing.normalize_to_records(body, "json")
    assert records == [{"text": "hello"}, {"text": "world"}]


def test_normalize_records_jsonl_strips_utf8_bom():
    """JSONL 入口:首字节 BOM 不应阻塞首行解析。"""
    body = b"\xef\xbb\xbf" + b'{"a":1}\n{"a":2}\n'
    records = landing.normalize_to_records(body, "jsonl")
    assert records == [{"a": 1}, {"a": 2}]


def test_normalize_records_csv_strips_utf8_bom():
    """CSV 入口:首字节 BOM 不应污染首行表头(否则首列名会变成 ﻿name)。"""
    body = b"\xef\xbb\xbf" + b"name,age\nAlice,30\nBob,25\n"
    records = landing.normalize_to_records(body, "csv")
    assert records == [
        {"name": "Alice", "age": "30"},
        {"name": "Bob", "age": "25"},
    ]
    # 关键:首列名不能是带 BOM 的 "﻿name"
    assert all("name" in r for r in records)


def test_normalize_records_plain_utf8_still_works():
    """回归锚:无 BOM 文件走 utf-8-sig 必须与原 utf-8 完全等价(无行为变化)。"""
    body = b'[{"text":"hello"}]'
    records = landing.normalize_to_records(body, "json")
    assert records == [{"text": "hello"}]
