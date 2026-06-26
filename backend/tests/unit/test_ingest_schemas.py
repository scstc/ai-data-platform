"""IngestExtract schema 校验测试(切片 A)。"""

import pytest
from app.schemas.ingest_task import IngestExtract


def test_extract_table_accepts_columns():
    """table 模式接受 columns。"""
    ext = IngestExtract(mode="table", tables=["t1"], columns=["id", "name"])
    assert ext.columns == ["id", "name"]


def test_extract_table_columns_optional():
    """columns 可缺省(存量任务向后兼容)。"""
    ext = IngestExtract(mode="table", tables=["t1"])
    assert ext.columns is None


def test_extract_sql_rejects_columns():
    """sql 模式不接受 columns(列由 SQL 决定)。"""
    with pytest.raises(ValueError, match="columns"):
        IngestExtract(mode="sql", sql="SELECT 1", columns=["id"])


def test_extract_path_rejects_columns():
    """path 模式不接受 columns。"""
    with pytest.raises(ValueError, match="columns"):
        IngestExtract(mode="path", paths=["a.jsonl"], columns=["id"])
