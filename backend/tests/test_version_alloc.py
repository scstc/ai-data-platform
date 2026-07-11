"""version_alloc 单测:算号语义 + 冲突重试策略的意图覆盖。

现状(engine.py / distillation.py / landing.py 等六处 runner)是"查
max(version_no)+1 → 建 DatasetVersion → flush",中间无互斥,并发建版本时
后 flush 的一方在 uq_dataset_version_no 上撞车、任务直接失败。本模块要补
的正是这层重试兜底,所以测试的重点不是"SQL 算对了没有"这种表层行为,而是
"冲突时该不该重试 / 该重试几次 / 重试后有没有把半成品清理干净"这几条决策。

分两部分:
- 纯单元测试(FakeSession,不连库):覆盖 with_version_conflict_retry 本身
  的决策逻辑——只对版本号冲突重试、非该冲突原样抛出、重试耗尽抛最后一次
  错误、每次重试前把上一次新增对象清出会话。本轮已跑过,全绿。
- DB 级测试(TestNextVersionNoDb / TestConflictRetryDb,用 session_factory
  连真实 adp_test):覆盖 next_version_no 对真实 DatasetVersion 行算号是否
  正确,以及两个真实并发事务下唯一约束冲突触发重试后确实拿到不同版本号。
  按纪律本轮只写不跑(adp_test 是共享慢库,并行代理跑会打架),留给统一
  验证阶段执行。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset_version import DatasetVersion
from app.services import version_alloc

# ── 纯单元测试:FakeSession,不连库 ──────────────────────────────────


class _FakeOrig(Exception):
    """模拟 asyncpg 驱动抛出的原始异常,str() 里带约束名(冲突场景)。"""


def _integrity_error(constraint: str) -> IntegrityError:
    orig = _FakeOrig(
        f'duplicate key value violates unique constraint "{constraint}"'
    )
    return IntegrityError("INSERT ...", {}, orig)


class _NestedTx:
    """替身 session.begin_nested() 的 async 上下文管理器。

    真实 SAVEPOINT 的物理回滚由数据库完成,这里只关心异常正常传播出去
    (不吞),交给 with_version_conflict_retry 的 except 分支处理。
    """

    async def __aenter__(self) -> _NestedTx:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSession:
    """替身 session:只实现 with_version_conflict_retry 用到的接口面。"""

    def __init__(self, flush_outcomes: list[Exception | None]) -> None:
        # 每次 attempt 对应一个结果:None=flush 成功,Exception=flush 抛出
        self._flush_outcomes = list(flush_outcomes)
        self.new: set[object] = set()
        self.expunged: list[object] = []
        self.flush_calls = 0

    def begin_nested(self) -> _NestedTx:
        return _NestedTx()

    async def flush(self) -> None:
        self.flush_calls += 1
        outcome = self._flush_outcomes.pop(0)
        if outcome is not None:
            raise outcome

    def expunge(self, obj: object) -> None:
        self.new.discard(obj)
        self.expunged.append(obj)


@pytest.fixture
def _patch_next_version_no(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """把 next_version_no 换成简单计数器:单测只关心重试决策本身,
    不重复验证"算号 SQL 对不对"(那是下面 DB 级测试的职责)。

    **非 autouse**:仅纯单元重试测试显式请求。DB 级测试(TestNextVersionNoDb /
    TestConflictRetryDb)要验证真实 next_version_no 对真库算号,绝不能被这个假
    计数器替换掉——否则 next_version_no 恒返回计数器值,与真实版本号无关。
    """
    calls = {"n": 0}

    async def _fake_next_version_no(session: object, dataset_id: str) -> int:
        calls["n"] += 1
        return calls["n"]

    monkeypatch.setattr(version_alloc, "next_version_no", _fake_next_version_no)
    return calls


async def test_success_on_first_attempt_returns_build_result(
    _patch_next_version_no: dict[str, int],
) -> None:
    """无冲突场景:只 flush 一次就拿到 build 的返回值——重试机制不该
    对无冲突的正常路径产生任何副作用(不多 flush,不 expunge 任何对象)。
    """
    session = _FakeSession(flush_outcomes=[None])

    async def build(version_no: int) -> dict[str, int]:
        obj = object()
        session.new.add(obj)
        return {"version_no": version_no}

    result = await version_alloc.with_version_conflict_retry(session, "ds-1", build)

    assert result["version_no"] == 1
    assert session.flush_calls == 1
    assert session.expunged == []


async def test_retries_and_recovers_after_version_conflict(
    _patch_next_version_no: dict[str, int],
) -> None:
    """业务意图:第一次撞版本号冲突时不该让上层任务直接失败——应该重算
    版本号、把本次尝试留下的半成品对象清出会话、再试一次并成功返回。
    """
    session = _FakeSession(
        flush_outcomes=[_integrity_error("uq_dataset_version_no"), None]
    )
    built_objs: list[object] = []

    async def build(version_no: int) -> dict[str, int]:
        obj = object()
        session.new.add(obj)
        built_objs.append(obj)
        return {"version_no": version_no}

    result = await version_alloc.with_version_conflict_retry(session, "ds-1", build)

    assert result["version_no"] == 2  # 第二次尝试算出的号
    assert session.flush_calls == 2
    # 第一次尝试新增的半成品必须清出会话,否则会污染第二次 flush
    assert built_objs[0] in session.expunged
    assert built_objs[0] not in session.new
    assert built_objs[1] in session.new


async def test_non_version_conflict_integrity_error_raises_immediately(
    _patch_next_version_no: dict[str, int],
) -> None:
    """业务意图:重试只该救"版本号撞车"这一种冲突;其他完整性错误(如
    别的唯一约束)必须原样抛出、不重试——否则会掩盖真正的数据问题,
    还平白多花 3 次 flush 才失败,拖长故障排查时间。
    """
    other_error = _integrity_error("uq_some_other_constraint")
    session = _FakeSession(flush_outcomes=[other_error])

    async def build(version_no: int) -> int:
        session.new.add(object())
        return version_no

    with pytest.raises(IntegrityError) as exc_info:
        await version_alloc.with_version_conflict_retry(session, "ds-1", build)

    assert exc_info.value is other_error
    assert session.flush_calls == 1


async def test_exhausts_retries_and_raises_last_conflict(
    _patch_next_version_no: dict[str, int],
) -> None:
    """业务意图:持续冲突不能无限重试拖死 runner——耗尽 max_attempts 后
    必须把最后一次冲突原样抛出,让上层 job 明确失败,而不是静默卡住
    或被吞掉错误。
    """
    errors = [
        _integrity_error("uq_dataset_version_no"),
        _integrity_error("uq_dataset_version_no"),
        _integrity_error("uq_dataset_version_no"),
    ]
    last_error = errors[-1]
    session = _FakeSession(flush_outcomes=list(errors))

    async def build(version_no: int) -> int:
        session.new.add(object())
        return version_no

    with pytest.raises(IntegrityError) as exc_info:
        await version_alloc.with_version_conflict_retry(
            session, "ds-1", build, max_attempts=3
        )

    assert exc_info.value is last_error
    assert session.flush_calls == 3


# ── DB 级测试:真实 adp_test,按纪律本轮只写不跑 ──────────────────────


def _new_version_id() -> str:
    import secrets

    return f"dsv-{secrets.token_hex(3)}"


class TestNextVersionNoDb:
    """next_version_no 对真实 DatasetVersion 行算号是否正确。"""

    async def test_no_versions_returns_1(
        self, session_factory: async_sessionmaker
    ) -> None:
        """业务意图:全新数据集第一次建版本必须拿到 v1,不能因为表里有
        其他数据集的版本行而算错(next_version_no 必须按 dataset_id 隔离)。
        """
        async with session_factory() as session:
            other = DatasetVersion(
                id=_new_version_id(),
                dataset_id="ds-other",
                version_no=5,
                storage_uri="pending://ds-other/v5/",
                format="jsonl",
            )
            session.add(other)
            await session.commit()

            version_no = await version_alloc.next_version_no(session, "ds-new")
            assert version_no == 1

    async def test_returns_max_plus_one(
        self, session_factory: async_sessionmaker
    ) -> None:
        """业务意图:已有 v1/v2 的数据集,下一次必须算出 v3——即使 v2 是
        后插入的(不能按插入顺序/自增 id 猜,必须按 version_no 取最大值)。
        """
        async with session_factory() as session:
            for vno in (1, 2):
                session.add(
                    DatasetVersion(
                        id=_new_version_id(),
                        dataset_id="ds-a",
                        version_no=vno,
                        storage_uri=f"pending://ds-a/v{vno}/",
                        format="jsonl",
                    )
                )
            await session.commit()

            version_no = await version_alloc.next_version_no(session, "ds-a")
            assert version_no == 3


class TestConflictRetryDb:
    """两个真实并发事务下,唯一约束冲突触发重试后应各自拿到不同版本号。"""

    async def test_concurrent_writers_both_succeed_with_distinct_versions(
        self, session_factory: async_sessionmaker
    ) -> None:
        """业务意图:这是本模块要解决的真实故障场景——两个 runner 同时对
        同一 dataset 产出新版本,现状(六处裸写的 max+1)会让后 flush 的
        一方直接因 uq_dataset_version_no 报错失败;接入
        with_version_conflict_retry 后,两方都应该成功落库且版本号不同。
        用 asyncio.Event 强制排出时序:A 先 flush(未提交)占住 v1,
        B 在 A 提交前发起构建 → B 的 INSERT 会等 A 提交后才判冲突 → 重试
        拿到 v2。
        """
        dataset_id = "ds-concurrent"
        a_flushed = asyncio.Event()

        async def run_a() -> DatasetVersion:
            async with session_factory() as session:

                async def _build(vno: int) -> DatasetVersion:
                    ver = DatasetVersion(
                        id=_new_version_id(),
                        dataset_id=dataset_id,
                        version_no=vno,
                        storage_uri=f"pending://{dataset_id}/v{vno}/",
                        format="jsonl",
                    )
                    session.add(ver)
                    return ver

                result = await version_alloc.with_version_conflict_retry(
                    session, dataset_id, _build
                )
                await session.flush()
                a_flushed.set()
                # 故意晚一点提交,给 B 制造"看不到 A 的未提交行"的窗口
                await asyncio.sleep(0.2)
                await session.commit()
                return result

        async def run_b() -> DatasetVersion:
            await a_flushed.wait()
            async with session_factory() as session:

                async def _build(vno: int) -> DatasetVersion:
                    ver = DatasetVersion(
                        id=_new_version_id(),
                        dataset_id=dataset_id,
                        version_no=vno,
                        storage_uri=f"pending://{dataset_id}/v{vno}/",
                        format="jsonl",
                    )
                    session.add(ver)
                    return ver

                result = await version_alloc.with_version_conflict_retry(
                    session, dataset_id, _build
                )
                await session.commit()
                return result

        ver_a, ver_b = await asyncio.gather(run_a(), run_b())

        assert {ver_a.version_no, ver_b.version_no} == {1, 2}

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(DatasetVersion).where(
                    DatasetVersion.dataset_id == dataset_id
                )
            )
            assert count == 2
