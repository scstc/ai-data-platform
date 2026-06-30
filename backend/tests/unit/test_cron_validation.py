"""采集任务 cron 调度校验测试(切片 C / Task 3)。

覆盖 ``IngestTaskCreate`` / ``IngestTaskUpdate`` 入口的 cron 校验(纯 schema 层,
不连 DB / 不启动调度器):

- ``mode=once``:不要求 cron 字段,无校验(向后兼容存量任务)。
- ``mode=cron`` + 合法 5 字段 cron(如 ``0 2 * * *``):通过。
- ``mode=cron`` + 非法 cron(字段超范围 / 字段数不对 / 解析抛错):拒绝。
- ``mode=cron`` + 空 cron / 缺 cron:拒绝(必须有非空表达式)。

校验由 ``_validate_cron`` 走 ``APScheduler CronTrigger.from_crontab`` 解析,
解析抛 ValueError 即拒。Defends:用户在 UI 上建了 cron 任务,但 cron 表达式
根本不会触发(写错字段顺序 / 非法值)。

DEFERRED(留待 .60 PG 恢复后回归):路由 upsert/remove 对真 jobstore 的端到端
集成——当前 .60 测试库 ConnectionRefused,本测只覆盖纯 schema 校验。
"""

from __future__ import annotations

import pytest

from app.schemas.ingest_task import IngestTaskCreate, IngestTaskUpdate


def _base_create_payload(cron: str | None, mode: str = "cron") -> dict:
    """构造 IngestTaskCreate 入参,仅 schedule 部分变化。"""
    schedule: dict = {"mode": mode}
    if cron is not None:
        schedule["cron"] = cron
    return {
        "name": "t-cron",
        "datasourceId": "ds-1",
        "datasetId": "dset-1",
        "schedule": schedule,
    }


# ---------- mode=once:不要求/不校验 cron ----------


class TestOnceModeNoCronRequired:
    def test_once_without_cron_field_accepted(self) -> None:
        """mode=once 时不携带 cron 字段,合法(存量任务常态)。"""
        task = IngestTaskCreate.model_validate(_base_create_payload(None, mode="once"))
        assert task.schedule.mode == "once"
        assert task.schedule.cron is None

    def test_once_with_cron_field_accepted(self) -> None:
        """mode=once 即便携带 cron 字段也不校验(兼容旧数据透传)。"""
        task = IngestTaskCreate.model_validate(
            _base_create_payload("0 2 * * *", mode="once")
        )
        assert task.schedule.mode == "once"
        # cron 字段可以透传,但调度器只对 mode=cron 才消费
        assert task.schedule.cron == "0 2 * * *"

    def test_once_update_does_not_require_cron(self) -> None:
        """Update 到 mode=once 不要求 cron(可改回一次性任务)。"""
        upd = IngestTaskUpdate.model_validate({"schedule": {"mode": "once"}})
        assert upd.schedule is not None
        assert upd.schedule.mode == "once"


# ---------- mode=cron:合法 cron 通过 ----------


class TestCronModeValidExpression:
    @pytest.mark.parametrize(
        "expr",
        [
            "0 2 * * *",       # 每天 02:00
            "*/5 * * * *",     # 每 5 分钟
            "0 0 1 * *",       # 每月 1 号 00:00
            "0 0 * * 0",       # 每周日 00:00
            "30 4 1 1 *",      # 每年 1 月 1 号 04:30
        ],
    )
    def test_valid_5_field_cron_accepted(self, expr: str) -> None:
        """合法的 5 字段标准 crontab 表达式应通过。"""
        task = IngestTaskCreate.model_validate(_base_create_payload(expr))
        assert task.schedule.mode == "cron"
        assert task.schedule.cron == expr

    def test_update_to_cron_valid_accepted(self) -> None:
        """Update 到 mode=cron + 合法 cron,通过。"""
        upd = IngestTaskUpdate.model_validate(
            {"schedule": {"mode": "cron", "cron": "0 2 * * *"}}
        )
        assert upd.schedule is not None
        assert upd.schedule.mode == "cron"
        assert upd.schedule.cron == "0 2 * * *"


# ---------- mode=cron:非法 cron 拒绝 ----------


class TestCronModeInvalidExpression:
    def test_out_of_range_minute_rejected(self) -> None:
        """分钟超范围(99)必须被拒。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("99 * * * *"))

    def test_out_of_range_all_fields_rejected(self) -> None:
        """全字段超范围(99 99 * * *)必须被拒。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("99 99 * * *"))

    def test_wrong_field_count_too_few_rejected(self) -> None:
        """4 字段(少一个)必须被拒——标准 crontab 是 5 字段。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("0 2 * *"))

    def test_wrong_field_count_too_many_rejected(self) -> None:
        """6 字段(多一个)必须被拒——APScheduler from_crontab 不支持秒级。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("0 0 2 * * *"))

    def test_non_numeric_field_rejected(self) -> None:
        """非数字非通配的字段应被拒。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("abc 2 * * *"))

    def test_update_invalid_cron_rejected(self) -> None:
        """Update 端同样校验——防止把任务改成一个永不触发的 cron。"""
        with pytest.raises(ValueError):
            IngestTaskUpdate.model_validate(
                {"schedule": {"mode": "cron", "cron": "99 99 * * *"}}
            )


# ---------- mode=cron:空/缺 cron 拒绝 ----------


class TestCronModeEmptyExpression:
    def test_cron_mode_without_cron_field_rejected(self) -> None:
        """mode=cron 但缺 cron 字段,必须拒绝(否则建了永不触发的任务)。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload(None, mode="cron"))

    def test_cron_mode_empty_cron_string_rejected(self) -> None:
        """mode=cron + 空字符串 cron,必须拒绝。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("", mode="cron"))

    def test_cron_mode_whitespace_only_cron_rejected(self) -> None:
        """mode=cron + 全空白 cron,必须拒绝(等价空表达式)。"""
        with pytest.raises(ValueError):
            IngestTaskCreate.model_validate(_base_create_payload("   ", mode="cron"))


# ---------- 校验器对 IngestSchedule 自身无副作用 ----------


class TestIngestScheduleModeLiteralUnchanged:
    def test_schedule_still_accepts_both_modes(self) -> None:
        """IngestSchedule.mode 仍是 Literal['once','cron'](模型层兼容)。"""
        from app.schemas.ingest_task import IngestSchedule

        once = IngestSchedule(mode="once")
        cron = IngestSchedule(mode="cron", cron="0 2 * * *")
        assert once.mode == "once"
        assert cron.mode == "cron"


# ---------- 路由 best-effort: scheduler 缺席时不抛 ----------


class TestRouteBestEffortWithoutScheduler:
    """验证 _sync_cron_job / _unsync_cron_job 在 scheduler 缺席时静默跳过。

    DEFERRED:真实 jobstore upsert/remove 集成需 .60 PG 恢复后回归——本测只覆盖
    「routes 在 scheduler 未启用 / 未启动 / 抛错时不 crash」的契约。
    """

    def test_sync_cron_job_skips_when_scheduler_disabled(self, monkeypatch) -> None:
        """scheduler_enabled=False → 不调 get_scheduler,直接返回。"""
        from app.api.v1 import ingest_tasks as routes

        monkeypatch.setattr(routes.settings, "scheduler_enabled", False)
        # 即便传一个 cron 任务,也不应抛(call_count 不会被检查,只要不抛即可)
        routes._sync_cron_job(_FakeCronTask())  # 不抛即通过

    def test_sync_cron_job_skips_when_no_scheduler_instance(self, monkeypatch) -> None:
        """scheduler_enabled=True 但 get_scheduler()=None(启动失败/未启动)→ 跳过。"""
        from app.api.v1 import ingest_tasks as routes

        monkeypatch.setattr(routes.settings, "scheduler_enabled", True)

        def _none():
            return None

        monkeypatch.setattr(routes.scheduler_mod, "get_scheduler", _none)
        routes._sync_cron_job(_FakeCronTask())  # 不抛即通过

    def test_sync_cron_job_swallows_upsert_exception(self, monkeypatch) -> None:
        """scheduler 抛错时 best-effort 吞掉,不阻断主流程。"""
        from app.api.v1 import ingest_tasks as routes

        monkeypatch.setattr(routes.settings, "scheduler_enabled", True)

        class _Boom:
            pass

        def _boom_scheduler():
            return _Boom()

        def _boom_upsert(*_a, **_kw):
            raise RuntimeError("simulated jobstore unreachable")

        monkeypatch.setattr(routes.scheduler_mod, "get_scheduler", _boom_scheduler)
        monkeypatch.setattr(routes.scheduler_mod, "upsert_cron_job", _boom_upsert)
        # 不抛即通过——best-effort 契约
        routes._sync_cron_job(_FakeCronTask())

    def test_sync_cron_job_skips_once_mode_task_with_stale_cron(self, monkeypatch) -> None:
        """once 模式任务即便携带历史 cron 字段也不应建调度作业(回归)。"""
        from app.api.v1 import ingest_tasks as routes

        monkeypatch.setattr(routes.settings, "scheduler_enabled", True)

        class _Scheduler:
            pass

        calls: list[int] = []
        monkeypatch.setattr(routes.scheduler_mod, "get_scheduler", lambda: _Scheduler())
        monkeypatch.setattr(
            routes.scheduler_mod, "upsert_cron_job", lambda *a, **k: calls.append(1)
        )

        class _OnceTask:
            id = "task-once"
            schedule = {"mode": "once", "cron": "0 2 * * *"}

        routes._sync_cron_job(_OnceTask())
        assert calls == [], "once 模式任务不应触发 upsert_cron_job"

    def test_unsync_cron_job_swallows_remove_exception(self, monkeypatch) -> None:
        """remove 抛错时 best-effort 吞掉,delete 不应被拖累。"""
        from app.api.v1 import ingest_tasks as routes

        monkeypatch.setattr(routes.settings, "scheduler_enabled", True)

        class _Boom:
            pass

        def _boom_scheduler():
            return _Boom()

        def _boom_remove(*_a, **_kw):
            raise RuntimeError("simulated jobstore unreachable")

        monkeypatch.setattr(routes.scheduler_mod, "get_scheduler", _boom_scheduler)
        monkeypatch.setattr(routes.scheduler_mod, "remove_cron_job", _boom_remove)
        # 不抛即通过——delete 主流程不应被 jobstore 故障拖累
        routes._unsync_cron_job("task-any")


class _FakeCronTask:
    """够用的任务替身:_sync_cron_job 只读 task.id(用于 log);其他字段不读。"""

    id = "task-fake"
