"""采集质量策略 schema 校验测试(切片 B / Task 1)。

覆盖 QualityPolicy 自身校验 + IngestTaskCreate/Update/Read 对 qualityPolicy
字段的 camelCase 往返。质量字段本身(quality_stats/schema_snapshot/
quality_verdict)只落模型/迁移,不通过 schema 暴露——本测只验 pydantic 层。
"""

import pytest

from app.schemas.ingest_task import (
    IngestSchedule,
    IngestTaskCreate,
    IngestTaskRead,
    IngestTaskUpdate,
    QualityPolicy,
)


def _base_payload() -> dict:
    """合法的 IngestTaskCreate 入参(不含 qualityPolicy)。"""
    return {
        "name": "t-q",
        "datasourceId": "ds-1",
        "lakeId": "lake-1",
        "schedule": {"mode": "once"},
    }


def test_quality_policy_defaults():
    """未传任何字段时,maxNullRate=None、blockOnSchemaDrift=False。"""
    qp = QualityPolicy()
    assert qp.max_null_rate is None
    assert qp.block_on_schema_drift is False


def test_quality_policy_max_null_rate_bounds_ok():
    """边界 0 与 1 均合法(闭区间)。"""
    assert QualityPolicy(max_null_rate=0.0).max_null_rate == 0.0
    assert QualityPolicy(max_null_rate=1.0).max_null_rate == 1.0
    # 小数也合法
    assert QualityPolicy(max_null_rate=0.05).max_null_rate == 0.05


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0, -1.0])
def test_quality_policy_max_null_rate_out_of_range_rejected(bad: float):
    """越界 null 率必须被拒(校验器而非运行期)。"""
    with pytest.raises(ValueError):
        QualityPolicy(max_null_rate=bad)


def test_create_without_quality_policy_defaults_none():
    """不携带 qualityPolicy 也能创建(向后兼容存量任务)。"""
    task = IngestTaskCreate(**_base_payload())
    assert task.quality_policy is None


def test_create_with_quality_policy_camel_case_round_trip():
    """前端以 camelCase qualityPolicy 投递,pydantic 解析后字段可读。"""
    payload = {
        **_base_payload(),
        "qualityPolicy": {"maxNullRate": 0.1, "blockOnSchemaDrift": True},
    }
    task = IngestTaskCreate.model_validate(payload)
    assert isinstance(task.quality_policy, QualityPolicy)
    assert task.quality_policy.max_null_rate == 0.1
    assert task.quality_policy.block_on_schema_drift is True


def test_create_with_quality_policy_dumps_back_to_camel():
    """序列化回 dict 时 alias 还原为 qualityPolicy/maxNullRate。"""
    task = IngestTaskCreate(
        **_base_payload(),
        quality_policy=QualityPolicy(max_null_rate=0.2),
    )
    dumped = task.model_dump(by_alias=True, exclude_none=False)
    assert "qualityPolicy" in dumped
    assert dumped["qualityPolicy"]["maxNullRate"] == 0.2
    # blockOnSchemaDrift 默认 False 应出现(显式 False 非 None)
    assert dumped["qualityPolicy"]["blockOnSchemaDrift"] is False


def test_update_accepts_quality_policy():
    """IngestTaskUpdate 也接受可选 qualityPolicy。"""
    upd = IngestTaskUpdate(
        quality_policy=QualityPolicy(max_null_rate=0.3, block_on_schema_drift=True),
    )
    assert upd.quality_policy is not None
    assert upd.quality_policy.max_null_rate == 0.3


def test_update_quality_policy_alias_parse():
    """Update 端也按 camelCase 解析(与 Create 一致)。"""
    upd = IngestTaskUpdate.model_validate(
        {"qualityPolicy": {"maxNullRate": 0.4, "blockOnSchemaDrift": True}}
    )
    assert upd.quality_policy is not None
    assert upd.quality_policy.max_null_rate == 0.4
    assert upd.quality_policy.block_on_schema_drift is True


def test_read_schema_serializes_quality_policy_camel():
    """Read 模型携带 qualityPolicy,序列化按 camelCase 输出。"""
    read = IngestTaskRead(
        id="task-1",
        name="t",
        datasourceId="ds-1",
        datasourceName="src",
        schedule=IngestSchedule(mode="once"),
        status="pending",
        progress=0,
        createdAt="2026-06-26T00:00:00Z",
        qualityPolicy=QualityPolicy(max_null_rate=0.05),
    )
    dumped = read.model_dump(by_alias=True)
    assert dumped["qualityPolicy"]["maxNullRate"] == 0.05
    assert dumped["qualityPolicy"]["blockOnSchemaDrift"] is False
