"""版本号分配 + 并发冲突重试(任务队列整改 P0 地基)。

现状(engine.py / distillation.py / landing.py 等六处 runner):
    ``max_vno = select(max(version_no)).where(dataset_id=...)``
    ``new_vno = (max_vno or 0) + 1``
即"查最大值 + 1"后直接建 `DatasetVersion` 并 flush,中间无互斥。同一数据集
并发建版本(如两个任务同时对同一 dataset 产出新版本)时,后 flush 的一方会
在 `uq_dataset_version_no` 唯一约束上撞车,现状是让 IntegrityError 直接冒泡
给上层,任务失败。

本模块提供两个函数:

- `next_version_no(session, dataset_id)`:纯算号,单次查询,不加锁——
  真正的互斥仍是唯一约束本身,这里只是复用现状算法,不引入新语义。
- `with_version_conflict_retry(session, dataset_id, build, max_attempts=3)`:
  包一层"算号 → 调用方建对象 → flush"的重试。撞上
  `uq_dataset_version_no` 冲突时,在 SAVEPOINT 内回滚本次尝试新增的对象、
  重算版本号后重试;其余类型的 IntegrityError(以及重试耗尽后的最后一次
  冲突)原样抛出,不吞、不包装。

用法示例(六个 runner 低侵入接入,替换掉现状的两行算号代码)::

    async def _build(version_no: int) -> DatasetVersion:
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            ...,
        )
        session.add(version)
        return version

    version = await with_version_conflict_retry(session, dataset_id, _build)
    # 后续按需 session.add(member ...);最终统一 session.commit()

**`build` 的约束**:
1. 只负责构造 ORM 对象 + `session.add(...)`,**不要自行 flush/commit**——
   flush 由本函数统一发起以捕获冲突。
2. 每次调用都要新建对象实例,不要复用上一次调用创建的对象——冲突重试时
   上一次的对象已被从 session 中 `expunge`,复用会导致状态错乱。
3. 如果 `build` 内还会 `session.add` 其他关联行(如表成员),它们和
   version 对象一样会在冲突时被整体 SAVEPOINT 回滚 + expunge,重试时随
   `build` 重新构造,天然保持一致。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset_version import DatasetVersion

T = TypeVar("T")

# 版本号并发冲突的唯一约束名(见 DatasetVersion.__table_args__),
# 只对命中此约束的 IntegrityError 重试,其余一律原样抛出
_VERSION_CONFLICT_CONSTRAINT = "uq_dataset_version_no"


async def next_version_no(session: AsyncSession, dataset_id: str) -> int:
    """当前 dataset 的下一个版本号 = max(version_no) + 1(无版本时为 1)。

    仅算号,不加锁、不保证与其他并发事务互斥——真正的互斥来自
    `uq_dataset_version_no` 唯一约束,并发冲突由 `with_version_conflict_retry`
    兜底重试。
    """
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    return (max_vno or 0) + 1


def _is_version_conflict(exc: IntegrityError) -> bool:
    """判断 IntegrityError 是否命中版本号唯一约束(而非其他完整性错误)。"""
    return _VERSION_CONFLICT_CONSTRAINT in str(exc.orig)


async def with_version_conflict_retry(
    session: AsyncSession,
    dataset_id: str,
    build: Callable[[int], Awaitable[T]],
    *,
    max_attempts: int = 3,
) -> T:
    """算号 + 建对象 + flush,遇 `uq_dataset_version_no` 冲突自动重试。

    每次尝试都在 SAVEPOINT(`session.begin_nested`)内进行:`build(version_no)`
    构造对象并 `session.add`,本函数负责 `flush`。flush 命中版本号冲突时,
    SAVEPOINT 自动回滚本次尝试涉及的写入,再把本次新增的对象逐个从会话
    `expunge`(避免陈旧 pending 对象干扰下一轮 flush),重新算号后重试。

    非版本号冲突的 IntegrityError 直接抛出、不重试。重试耗尽后,原样抛出
    最后一次捕获的 IntegrityError(上层可沿用现状的 except 分支)。
    """
    last_error: IntegrityError | None = None
    for _ in range(max_attempts):
        version_no = await next_version_no(session, dataset_id)
        pending_before = set(session.new)
        try:
            async with session.begin_nested():
                result = await build(version_no)
                await session.flush()
            return result
        except IntegrityError as exc:
            if not _is_version_conflict(exc):
                raise
            last_error = exc
            for obj in set(session.new) - pending_before:
                session.expunge(obj)
            continue
    assert last_error is not None  # max_attempts >= 1 时必然在上面 raise 或 return
    raise last_error
