import pytest

from app.services.landing import records_to_parquet_bytes, parquet_bytes_to_records


def test_parquet_head_for_text_key(tmp_path):
    # 工具:从 parquet 取前 N 行供 detect_text_key(替代 _read_jsonl_head)
    from app.services.engine import _read_head_records

    p = tmp_path / "data.parquet"
    p.write_bytes(records_to_parquet_bytes([{"title": "hello", "x": 1}]))
    head = _read_head_records(p, 10)
    assert head and "title" in head[0]
