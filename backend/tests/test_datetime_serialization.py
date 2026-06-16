"""时间戳序列化契约:库内 naive UTC → 对外 JSON 带 ``Z`` 的 UTC ISO-8601。

为什么重要:时间列是 TIMESTAMP WITHOUT TIME ZONE,存的是 naive UTC
(应用侧 ``datetime.now(UTC)`` / PG ``now()``)。若输出不带时区标记,前端无法
判断这是 UTC,会按本地时区把原始数值原样显示,导致比北京时间慢 8 小时。
这些断言锁定「输出必须带 Z」,业务若回退成无时区输出会立即失败。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.audit import AuditLogRead
from app.schemas.job import JobRead


def test_naive_datetime_serialized_as_utc_z() -> None:
    log = AuditLogRead(
        id="aud-1",
        username="admin",
        action="dataset.delete",
        method="DELETE",
        path="/api/v1/datasets/x",
        target="x",
        status_code=200,
        created_at=datetime(2026, 6, 16, 3, 0, 0),  # naive,代表 UTC 03:00
    )
    dumped = log.model_dump(mode="json", by_alias=True)
    # 必须带 Z:前端据此识别为 UTC 并 +8 显示北京时间(11:00)
    assert dumped["createdAt"] == "2026-06-16T03:00:00Z"


def test_aware_datetime_normalized_to_utc_z() -> None:
    log = AuditLogRead(
        id="aud-2",
        username="admin",
        action="x.create",
        method="POST",
        path="/api/v1/x",
        target=None,
        status_code=201,
        created_at=datetime(2026, 6, 16, 3, 0, 0, tzinfo=UTC),
    )
    assert log.model_dump(mode="json", by_alias=True)["createdAt"] == (
        "2026-06-16T03:00:00Z"
    )


def test_optional_datetime_none_stays_none() -> None:
    job = JobRead(
        id="job-1",
        name="j",
        type="clean",
        state="pending",
        progress=0,
        created_at=datetime(2026, 6, 16, 3, 0, 0),
    )
    dumped = job.model_dump(mode="json", by_alias=True)
    assert dumped["createdAt"] == "2026-06-16T03:00:00Z"
    assert dumped["startedAt"] is None
    assert dumped["finishedAt"] is None
