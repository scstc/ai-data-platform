"""站内通知服务:终态埋点写入(emit)+ 查询 / 标记已读。

设计要点(见 docs/superpowers/specs/2026-06-28-notification-center-design.md):
- ``emit`` 复用 choke point 当前 session,通知行与任务终态在**同一事务**内提交
  (不自行 commit),避免「任务已落终态但通知丢失」的不一致。
- ``emit`` 内任何异常只 loud log、**不向上抛**——通知是旁路,绝不能让一个已成功
  的任务因通知失败被判失败。
- 查询 / 标记类函数强制按 ``recipient`` 过滤,接收者由 API 层钉死为当前用户,
  实现「不可见 / 不可改他人通知」的强隔离。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, _new_notification_id

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """库内时间列统一 naive UTC(与 job_runner._now 一致)。"""
    return datetime.now(UTC).replace(tzinfo=None)


def emit(
    session: AsyncSession,
    *,
    recipient: str,
    level: str,
    source_type: str,
    source_id: str,
    title: str,
    body: str | None = None,
) -> None:
    """构造一行通知 ``add`` 到传入 session(不 commit,随 choke point 事务一起提交)。

    任何异常 loud log 后吞掉:通知失败不得污染任务终态(本期「fail loud」体现在
    日志,而非让无关任务连带失败)。
    """
    try:
        session.add(
            Notification(
                id=_new_notification_id(),
                recipient=recipient,
                level=level,
                source_type=source_type,
                source_id=source_id,
                title=title,
                body=body,
                read=False,
            )
        )
    except Exception:  # noqa: BLE001 — 通知是旁路,绝不向调用者抛
        logger.exception(
            "写入站内通知失败(已忽略):recipient=%s source_type=%s source_id=%s",
            recipient,
            source_type,
            source_id,
        )


async def list_for(
    session: AsyncSession,
    recipient: str,
    *,
    only_unread: bool,
    page: int,
    page_size: int,
) -> tuple[list[Notification], int]:
    """列出某接收者的通知(按创建时间倒序),返回 (当前页, 总数)。"""
    conds = [Notification.recipient == recipient]
    if only_unread:
        conds.append(Notification.read.is_(False))

    total = (
        await session.scalar(
            select(func.count()).select_from(Notification).where(*conds)
        )
    ) or 0
    rows = (
        await session.scalars(
            select(Notification)
            .where(*conds)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return list(rows), total


async def unread_count(session: AsyncSession, recipient: str) -> int:
    """某接收者的未读通知数。"""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient == recipient)
            .where(Notification.read.is_(False))
        )
    ) or 0


async def mark_read(
    session: AsyncSession, recipient: str, notification_id: str
) -> bool:
    """把某条通知标已读(只能标自己的;幂等)。返回是否命中该接收者的通知行。

    幂等:已读再标不重置 read_at(WHERE read=false 锁住首次标记的时间)。
    返回 True 表示该通知属于该 recipient(无论本次是否真正改了行),供 API 区分
    404(他人 / 不存在)与成功。
    """
    notif = await session.get(Notification, notification_id)
    if notif is None or notif.recipient != recipient:
        return False
    if not notif.read:
        notif.read = True
        notif.read_at = _now()
    await session.commit()
    return True


async def mark_all_read(session: AsyncSession, recipient: str) -> int:
    """把某接收者的全部未读通知标已读,返回更新条数(只影响该 recipient)。"""
    result = await session.execute(
        update(Notification)
        .where(Notification.recipient == recipient)
        .where(Notification.read.is_(False))
        .values(read=True, read_at=_now())
    )
    await session.commit()
    return result.rowcount or 0
