"""preview_version 对 parquet 格式 s3:// 版本走 DuckDB 分支的集成测试。

测试策略:monkeypatch `_duck_query`、`_version_storage_cfg`、`s3_settings_for_duckdb`
避免真实 MinIO 连接;断言 parquet 分支被路由并原样返回 rows/columns/total。
"""

import pytest

import app.api.v1.datasets as datasets_module
from app.models.dataset_version import DatasetVersion


@pytest.mark.asyncio
async def test_preview_parquet_routes_through_duckdb(
    client, session_factory, monkeypatch
):
    """parquet 格式 s3:// 版本应走 DuckDB 分支,不走 head_records,返回正确 shape。"""
    # ── 1. 插入 parquet 版本 ──────────────────────────────────────────────────
    async with session_factory() as session:
        ver = DatasetVersion(
            id="dsv-pq-preview",
            dataset_id="ds-pq-preview",
            version_no=1,
            storage_uri="s3://uploads/ds-pq-preview/v1/data.parquet",
            format="parquet",
            rows=2,
        )
        session.add(ver)
        await session.commit()

    # ── 2. 桩:_version_storage_cfg → 非 None(跳过真实 MinIO 配置读取) ────────
    async def fake_storage_cfg(version, session):
        return {"endpoint": "minio:9000", "access_key": "A", "secret_key": "S"}

    monkeypatch.setattr(datasets_module, "_version_storage_cfg", fake_storage_cfg)

    # ── 3. 桩:s3_settings_for_duckdb → 固定 tuple ───────────────────────────
    fake_s3 = ("minio:9000", False, "A", "S")
    monkeypatch.setattr(
        datasets_module, "s3_settings_for_duckdb", lambda cfg: fake_s3
    )

    # ── 4. 桩:_duck_query → 固定结果(断言参数,不真正调用 DuckDB) ───────────
    fixed_rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    fixed_cols = ["id", "name"]
    duck_call_args: list = []

    def fake_duck_query(path, fmt, sql, limit, offset, s3):
        duck_call_args.append({"path": path, "fmt": fmt, "s3": s3})
        return fixed_rows, fixed_cols, len(fixed_rows)

    monkeypatch.setattr(datasets_module, "_duck_query", fake_duck_query)

    # ── 5. 调用预览接口 ───────────────────────────────────────────────────────
    resp = await client.get("/api/v1/dataset-versions/dsv-pq-preview/preview")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    assert body["data"] == fixed_rows
    assert body["columns"] == fixed_cols
    assert body["total"] == 2

    # DuckDB 确实被调用,且格式为 parquet
    assert len(duck_call_args) == 1
    assert duck_call_args[0]["fmt"] == "parquet"
    assert duck_call_args[0]["s3"] == fake_s3

    # 整数列保真(parquet 路径不做字符串转换)
    assert isinstance(body["data"][0]["id"], int)
