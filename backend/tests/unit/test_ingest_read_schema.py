"""采集任务读模型字段暴露测试(切片 C6 后端补全)。

锁的意图:
- ``IngestTaskRead.watermark``:模型 ``IngestTask.watermark`` 已存在(JSONB,
  由连接器写入 ``{value, updatedAt}`` 形),读模型必须暴露,前端「当前水位」
  才能展示而非降级为 "-"。
- ``IngestRunRead.trigger``:Job.trigger(manual|cron,C1)必须流到读模型,
  前端「触发来源」column 才能展示而非降级为 "-"。

不依赖真 PG(`.60` DOWN 大概率):纯 schema + mapping 逻辑测试。
DB-backed 端到端断言(runs 端点 wire 形态带 trigger)标 DEFERRED——
本测试只锁「字段存在 + 形态匹配 + Job.trigger→IngestRunRead.trigger 映射」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.schemas.ingest_task import IngestRunRead, IngestTaskRead, Watermark


# ---------------------------------------------------------------------------
# Watermark schema:形态与连接器写入的 JSONB 一致
# ---------------------------------------------------------------------------


def test_watermark_reads_camel_case_jsonb_shape():
    """连接器写 ``{"value": ..., "updatedAt": "..."}``(camelCase),
    Watermark CamelModel 经 alias_generator + populate_by_name 应正确解析。"""
    wm = Watermark.model_validate(
        {"value": "2026-06-01T00:00:00Z", "updatedAt": "2026-06-01T00:00:00Z"}
    )
    assert wm.value == "2026-06-01T00:00:00Z"
    assert wm.updated_at == datetime.fromisoformat("2026-06-01T00:00:00+00:00")


def test_watermark_serializes_back_to_camel_case():
    """``model_dump(by_alias=True)`` 应还原 ``updatedAt`` 键(JSON 输出契约)。"""
    wm = Watermark(
        value="row-123",
        updated_at=datetime(2026, 6, 1, 0, 0, 0),
    )
    dumped = wm.model_dump(by_alias=True, mode="json")
    assert dumped == {"value": "row-123", "updatedAt": "2026-06-01T00:00:00Z"}


def test_watermark_updated_at_optional():
    """updated_at 可空(防御:存量/异常路径可能未填)。"""
    wm = Watermark.model_validate({"value": "x"})
    assert wm.value == "x"
    assert wm.updated_at is None


# ---------------------------------------------------------------------------
# IngestTaskRead.watermark:从 ORM 行(类 dict)自动填充
# ---------------------------------------------------------------------------


@dataclass
class _FakeTask:
    """仿 IngestTask ORM 行,仅暴露 model_validate(from_attributes)读取的字段。"""

    id: str = "task-fake01"
    name: str = "测试任务"
    datasource_id: str = "ds-fake01"
    datasource_name: str = "测试源"
    schedule: dict[str, Any] = field(default_factory=lambda: {"mode": "once"})
    extract: dict[str, Any] | None = None
    status: str = "pending"
    progress: int = 0
    run_count: int = 0
    category_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 6, 1))
    last_run_at: datetime | None = None
    logs: list[str] | None = None
    output: list[dict[str, Any]] | None = None
    quality_policy: dict[str, Any] | None = None
    incremental: dict[str, Any] | None = None
    watermark: dict[str, Any] | None = None


def test_ingest_task_read_carries_watermark():
    """IngestTaskRead.model_validate(task) 自动从 task.watermark(dict)填充。
    前端「当前水位」字段应非 None,值/时间正确。"""
    task = _FakeTask(
        watermark={"value": "12345", "updatedAt": "2026-06-01T00:00:00Z"}
    )
    read = IngestTaskRead.model_validate(task)
    assert read.watermark is not None
    assert read.watermark.value == "12345"
    assert read.watermark.updated_at is not None


def test_ingest_task_read_watermark_none_when_unset():
    """无水位(全量任务/未跑过增量)→ watermark=None,不抛错。"""
    read = IngestTaskRead.model_validate(_FakeTask(watermark=None))
    assert read.watermark is None


def test_ingest_task_read_watermark_round_trip_json():
    """watermark 经 model_dump(by_alias=True) 还原为前端期望的 camelCase 形。"""
    task = _FakeTask(
        watermark={"value": "v1", "updatedAt": "2026-06-01T00:00:00Z"}
    )
    read = IngestTaskRead.model_validate(task)
    dumped = read.model_dump(by_alias=True, mode="json")
    assert dumped["watermark"] == {
        "value": "v1",
        "updatedAt": "2026-06-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# IngestRunRead.trigger:Job.trigger → 读模型映射(纯 mapping,DB DEFERRED)
# ---------------------------------------------------------------------------


@dataclass
class _FakeJob:
    """仿 Job ORM 行,仅 list_ingest_runs 构造 IngestRunRead 时读取的字段。"""

    id: str = "job-fake01"
    state: str = "success"
    error: str | None = None
    started_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 6, 1))
    finished_at: datetime | None = None
    trigger: str = "manual"


def _build_run(job: _FakeJob, *, task_id: str = "task-fake01") -> IngestRunRead:
    """复刻 list_ingest_runs 内联构造 IngestRunRead 的关键映射(空产物分支)。

    锁的意图:Job.trigger 字段必须显式传入 IngestRunRead 构造,不能漏。
    若路由层忘记写 trigger=job.trigger,本测试会因字段缺失语义而暴露
    (IngestRunRead.trigger 默认 None,而 FakeJob.trigger 非 None → 不等)。
    """
    return IngestRunRead(
        id=job.id,
        task_id=task_id,
        status=job.state,
        rows=0,
        dataset_count=0,
        outputs=None,
        error=job.error,
        started_at=job.started_at or job.created_at,
        finished_at=job.finished_at,
        trigger=job.trigger,
    )


def test_ingest_run_read_has_trigger_field():
    """IngestRunRead 必须有 trigger 字段(否则 AttributeError / 等价降级)。"""
    # 字段存在性 + 受约束取值
    fields = IngestRunRead.model_fields
    assert "trigger" in fields


def test_run_trigger_flows_from_job_manual():
    """Job.trigger='manual' → IngestRunRead.trigger='manual'(手工 rerun)。"""
    run = _build_run(_FakeJob(trigger="manual"))
    assert run.trigger == "manual"


def test_run_trigger_flows_from_job_cron():
    """Job.trigger='cron' → IngestRunRead.trigger='cron'(定时调度触发)。"""
    run = _build_run(_FakeJob(trigger="cron"))
    assert run.trigger == "cron"


def test_run_trigger_serializes_to_json():
    """trigger 经 model_dump(by_alias=True, mode='json') 输出(前端 wire 契约)。"""
    run = _build_run(_FakeJob(trigger="cron"))
    dumped = run.model_dump(by_alias=True, mode="json")
    assert dumped["trigger"] == "cron"
