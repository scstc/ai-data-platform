import datetime
from decimal import Decimal

import pytest

from app.services.landing import (
    ParquetCodecError,
    parquet_bytes_to_records,
    records_to_parquet_bytes,
)


def test_roundtrip_preserves_types():
    records = [
        {"id": 1, "amount": Decimal("100.50"), "d": datetime.date(2026, 1, 1)},
        {"id": 2, "amount": Decimal("200.00"), "d": datetime.date(2026, 1, 2)},
    ]
    blob = records_to_parquet_bytes(records)
    assert isinstance(blob, bytes) and len(blob) > 0
    back = parquet_bytes_to_records(blob)
    assert back[0]["id"] == 1            # 仍是 int,非 "1"
    assert back[1]["d"] == datetime.date(2026, 1, 2)


def test_empty_records_raise():
    with pytest.raises(ParquetCodecError):
        records_to_parquet_bytes([])


def test_heterogeneous_column_raises():
    # 同列类型冲突(int vs dict)pyarrow 无法推断 → 抛 ParquetCodecError
    with pytest.raises(ParquetCodecError):
        records_to_parquet_bytes([{"x": 1}, {"x": {"nested": True}}])


def test_limit_reads_prefix():
    records = [{"i": i} for i in range(10)]
    blob = records_to_parquet_bytes(records)
    assert len(parquet_bytes_to_records(blob, limit=3)) == 3
