"""预览采样纯函数测试(切片 A)。

DB/文件采样器需真实驱动/MinIO,这里只测纯解析与列推断逻辑。
"""

from app.services.preview import (
    PREVIEW_SAMPLE_ROWS,
    _infer_columns,
    _parse_csv_head,
    _parse_jsonl_head,
)


def test_sample_rows_is_50():
    assert PREVIEW_SAMPLE_ROWS == 50


def test_infer_columns_type_from_first_non_null():
    """列类型由首非空值推断(跨 PG/MySQL 统一,避免驱动类型 API 差异)。"""
    rows = [
        {"id": None, "name": "a", "ok": True},
        {"id": 1, "name": "b", "ok": False},
    ]
    cols = {c["name"]: c["type"] for c in _infer_columns(rows)}
    assert cols["id"] == "integer"   # 取首非空 → int
    assert cols["name"] == "text"
    assert cols["ok"] == "boolean"


def test_infer_columns_preserves_first_seen_order():
    rows = [{"b": 1, "a": 2}]
    names = [c["name"] for c in _infer_columns(rows)]
    assert names == ["b", "a"]


def test_infer_columns_empty():
    assert _infer_columns([]) == []


def test_parse_jsonl_head_limit():
    text = "\n".join('{"i": %d}' % i for i in range(100))
    rows, truncated = _parse_jsonl_head(text, 50)
    assert len(rows) == 50
    assert truncated is True
    assert rows[0] == {"i": 0}


def test_parse_csv_head():
    text = "id,name\n1,a\n2,b\n"
    rows, truncated = _parse_csv_head(text, 50)
    assert rows == [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
    assert truncated is False
