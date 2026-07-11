"""采集调度与增量 schema 校验测试(切片 C / Task 1)。

覆盖 Incremental 自身校验(库形 vs 文件形、混合拒绝、空拒绝)+ IngestTaskCreate
/Update/Read 对 incremental 字段的 camelCase 往返。trigger(模型层)/watermark
(模型层)只落模型/迁移,不通过 schema 暴露——本测只验 pydantic 层。
"""

from __future__ import annotations

import pytest

from app.schemas.ingest_task import (
    Incremental,
    IngestSchedule,
    IngestTaskCreate,
    IngestTaskRead,
    IngestTaskUpdate,
)


def _base_payload() -> dict:
    """合法的 IngestTaskCreate 入参(不含 incremental)。"""
    return {
        "name": "t-inc",
        "datasourceId": "ds-1",
        "lakeId": "lake-1",
        "schedule": {"mode": "once"},
    }


# ---------- Incremental 自身:合法形 ----------


def test_incremental_db_form_column_timestamp_ok():
    """库形:column + type=timestamp 合法。"""
    inc = Incremental(column="updated_at", type="timestamp")
    assert inc.column == "updated_at"
    assert inc.type == "timestamp"


def test_incremental_db_form_column_integer_ok():
    """库形:column + type=integer 也合法。"""
    inc = Incremental(column="id", type="integer")
    assert inc.column == "id"
    assert inc.type == "integer"


def test_incremental_file_form_mtime_ok():
    """文件形:by=mtime 合法。"""
    inc = Incremental(by="mtime")
    assert inc.by == "mtime"


def test_incremental_file_form_name_ok():
    """文件形:by=name 合法。"""
    inc = Incremental(by="name")
    assert inc.by == "name"


# ---------- Incremental 自身:非法形 ----------


def test_incremental_mixed_form_rejected():
    """混合 column + by 必须被拒(二选一)。"""
    with pytest.raises(ValueError):
        Incremental(column="updated_at", by="mtime")


def test_incremental_empty_form_rejected():
    """空形(只给 type / 只给 column 没 type / 全空)必须被拒。"""
    with pytest.raises(ValueError):
        Incremental()
    with pytest.raises(ValueError):
        Incremental(column="updated_at")
    with pytest.raises(ValueError):
        Incremental(type="timestamp")


# ---------- IngestTaskCreate/Update/Read 接入 ----------


def test_create_without_incremental_defaults_none():
    """不携带 incremental 也能创建(向后兼容存量任务)。"""
    task = IngestTaskCreate(**_base_payload())
    assert task.incremental is None


def test_create_with_incremental_db_form_camel_round_trip():
    """前端以 camelCase incremental 投递,pydantic 解析后字段可读。"""
    payload = {
        **_base_payload(),
        "incremental": {"column": "updated_at", "type": "timestamp"},
    }
    task = IngestTaskCreate.model_validate(payload)
    assert isinstance(task.incremental, Incremental)
    assert task.incremental.column == "updated_at"
    assert task.incremental.type == "timestamp"


def test_create_with_incremental_file_form_camel_round_trip():
    """文件形 incremental 也能 camelCase 解析。"""
    payload = {
        **_base_payload(),
        "incremental": {"by": "mtime"},
    }
    task = IngestTaskCreate.model_validate(payload)
    assert task.incremental is not None
    assert task.incremental.by == "mtime"


def test_create_incremental_dumps_back_to_camel():
    """序列化回 dict 时 alias 还原为 incremental。"""
    task = IngestTaskCreate(
        **_base_payload(),
        incremental=Incremental(column="updated_at", type="timestamp"),
    )
    dumped = task.model_dump(by_alias=True, exclude_none=False)
    assert "incremental" in dumped
    assert dumped["incremental"]["column"] == "updated_at"
    assert dumped["incremental"]["type"] == "timestamp"


def test_create_rejects_mixed_incremental():
    """混合形在 IngestTaskCreate 入口同样被拒。"""
    payload = {
        **_base_payload(),
        "incremental": {"column": "updated_at", "by": "mtime"},
    }
    with pytest.raises(ValueError):
        IngestTaskCreate.model_validate(payload)


def test_update_accepts_incremental():
    """IngestTaskUpdate 也接受可选 incremental。"""
    upd = IngestTaskUpdate(incremental=Incremental(column="id", type="integer"))
    assert upd.incremental is not None
    assert upd.incremental.column == "id"


def test_update_incremental_alias_parse():
    """Update 端也按 camelCase 解析(与 Create 一致)。"""
    upd = IngestTaskUpdate.model_validate(
        {"incremental": {"by": "name"}}
    )
    assert upd.incremental is not None
    assert upd.incremental.by == "name"


def test_read_schema_serializes_incremental_camel():
    """Read 模型携带 incremental,序列化按 camelCase 输出。"""
    read = IngestTaskRead(
        id="task-1",
        name="t",
        datasourceId="ds-1",
        datasourceName="src",
        schedule=IngestSchedule(mode="once"),
        status="pending",
        progress=0,
        createdAt="2026-06-26T00:00:00Z",
        incremental=Incremental(column="updated_at", type="timestamp"),
    )
    dumped = read.model_dump(by_alias=True)
    assert dumped["incremental"]["column"] == "updated_at"
    assert dumped["incremental"]["type"] == "timestamp"


def test_apscheduler_3x_importable():
    """确认 apscheduler 为 3.x(API 与 4.x 不同),为 Task 3 真跑铺路。"""
    import apscheduler

    assert apscheduler.__version__.startswith("3.")
    # 3.x 独有的 API 名(4.x 已重命名):导入即验证
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # noqa: F401
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: F401
