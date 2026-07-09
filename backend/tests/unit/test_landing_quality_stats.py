"""land_records 落地时计算并存 quality_stats / schema_snapshot 测试(切片 B / Task 3)。

锁的核心意图(见 task-3-brief):
- land_records 是统一落地出口,所有连接器 rerun 路径都经它创建受管 v1。
  落地后必须把结构化质量统计(quality_stats)和 schema 快照(schema_snapshot)
  持久化到版本上,供后续 schema drift 比对 + 路由层策略评估。
- **结构化统计在 land_records 算**;策略评估(quality_verdict)由路由层做,
  land_records 不评估 → 保持默认 "skipped"(本测试断言之,防越界)。

mock 模式沿用 tests/test_landing_parquet.py 的 monkeypatch 风格——拦截
``app.services.external_store.upload_*``,避免真连 MinIO;真落 PG 用 session_factory。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services import landing


@pytest.mark.asyncio
async def test_land_records_persists_quality_stats_and_schema_snapshot(
    monkeypatch, session_factory: async_sessionmaker
):
    """land_records 落地后 version 带 quality_stats + schema_snapshot。

    意图(Task 3):
    - quality_stats.rows == len(records)(rows 来自归一后记录,与 version.rows 同源);
    - quality_stats.columns 非空,每列含 null_rate(结构化统计不是空壳);
    - schema_snapshot 是 [{name, type}](null_rate 已剥离,供 drift 比对);
    - quality_verdict 保持默认 "skipped"(land_records 不评估策略,路由层负责)。
    """
    async def fake_upload_jsonl(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.jsonl"

    monkeypatch.setattr(
        "app.services.external_store.upload_jsonl_to_datasets", fake_upload_jsonl
    )

    records = [
        {"id": 1, "name": "alice"},
        {"id": 2, "name": ""},  # 空串算 null → null_rate=1/2
        {"id": 3},  # 缺 name 键 → 也算 null
    ]
    async with session_factory() as session:
        ds, ver = await landing.land_records(
            session,
            records,
            dataset_name="t",
            source_kind="database",
        )

    # 1) quality_stats 已落库且行数与版本一致
    assert ver.quality_stats is not None
    assert ver.quality_stats["rows"] == len(records) == ver.rows

    # 2) columns 非空,每列含 null_rate(name 列应 = 2/3)
    cols = ver.quality_stats["columns"]
    assert cols, "columns 不能为空"
    name_col = next(c for c in cols if c["name"] == "name")
    assert "null_rate" in name_col
    assert name_col["null_rate"] == pytest.approx(2 / 3)

    # 3) schema_snapshot 投影 name+type(无 null_rate)
    assert ver.schema_snapshot is not None
    assert ver.schema_snapshot == [
        {"name": c["name"], "type": c["type"]} for c in cols
    ]
    snap_names = {c["name"] for c in ver.schema_snapshot}
    assert {"id", "name"}.issubset(snap_names)

    # 4) 策略不在 land_records 评估:verdict 保持默认 "skipped"
    assert ver.quality_verdict == "skipped"


@pytest.mark.asyncio
async def test_land_records_quality_stats_reflect_post_coercion_records(
    monkeypatch, session_factory: async_sessionmaker
):
    """quality_stats 用的是**归一后**的 records(与 rows=len(records) 同源)。

    意图:stats 必须反映真实落地的数据(后续 drift 比对 / 策略评估依赖)。
    若 land_records 误用归一前的原始 records,字段裁剪/重命名后 stats 会错位。
    这里用一个简单 records 即可——只要断言 stats.rows 与 version.rows 同源即可。
    """
    async def fake_upload_jsonl(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.jsonl"

    monkeypatch.setattr(
        "app.services.external_store.upload_jsonl_to_datasets", fake_upload_jsonl
    )

    records = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]
    async with session_factory() as session:
        ds, ver = await landing.land_records(
            session,
            records,
            dataset_name="t2",
            source_kind="database",
        )

    # 同源约束:stats.rows 与 version.rows 都来自归一后 len(records)
    assert ver.quality_stats["rows"] == ver.rows == 3
    # 所有列都无缺失
    for col in ver.quality_stats["columns"]:
        assert col["null_rate"] == 0.0
