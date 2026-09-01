"""Репозиторий отложенных пушей: постановка, захват к отправке, отмена по показу."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.inbox_item import InboxItem
from app.shared.db.models.push_job import PushJob


class PushJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def schedule(self, inbox_item_ids: Sequence[int], send_after: datetime) -> int:
        """Поставить job на каждую НОВУЮ запись инбокса.

        Идемпотентность даром: повторная обработка события не вставляет строки
        инбокса (ON CONFLICT), значит и job'ов на них здесь не появится.
        """
        for item_id in inbox_item_ids:
            self._session.add(PushJob(inbox_item_id=item_id, send_after=send_after))
        if inbox_item_ids:
            await self._session.flush()
        return len(inbox_item_ids)

    async def claim_due(self, limit: int = 50) -> Sequence[tuple[PushJob, InboxItem]]:
        """Забрать созревшие job'ы, атомарно пометив их отправленными.

        ``FOR UPDATE SKIP LOCKED`` + немедленный ``sent_at`` — двойной отправки
        не бывает даже при двух воркерах: строку забирает ровно один, а
        подтверждение показа, пришедшее позже захвата, уже ничего не отменит
        (пуш фактически уходит).
        """
        now = datetime.now(UTC)
        rows = (
            await self._session.execute(
                select(PushJob, InboxItem)
                .join(InboxItem, InboxItem.id == PushJob.inbox_item_id)
                .where(
                    PushJob.sent_at.is_(None),
                    PushJob.canceled_at.is_(None),
                    PushJob.send_after <= now,
                )
                .order_by(PushJob.send_after)
                .limit(limit)
                .with_for_update(skip_locked=True, of=PushJob)
            )
        ).all()
        claimed: list[tuple[PushJob, InboxItem]] = []
        for job, item in rows:
            job.sent_at = now
            claimed.append((job, item))
        if claimed:
            await self._session.flush()
        return claimed

    async def cancel_for_items(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, inbox_item_ids: Sequence[int]
    ) -> int:
        """Отменить неотправленные job'ы СВОИХ записей; вернуть число отмен.

        Чужие id молча пропускаются (join по владельцу): подтверждение показа —
        массовая ручка, и валить весь батч из-за одного чужого id незачем.
        """
        if not inbox_item_ids:
            return 0
        own = (
            select(InboxItem.id)
            .where(
                InboxItem.account_id == account_id,
                InboxItem.clinic_id == clinic_id,
                InboxItem.id.in_(inbox_item_ids),
            )
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(PushJob)
            .where(
                PushJob.inbox_item_id.in_(own),
                PushJob.sent_at.is_(None),
                PushJob.canceled_at.is_(None),
            )
            .values(canceled_at=datetime.now(UTC))
            .returning(PushJob.id)
        )
        return len(list(result.scalars()))

    async def next_due_at(self) -> datetime | None:
        """Ближайший срок отправки — цикл диспетчера подбирает под него сон."""
        return (
            await self._session.execute(
                select(PushJob.send_after)
                .where(PushJob.sent_at.is_(None), PushJob.canceled_at.is_(None))
                .order_by(PushJob.send_after)
                .limit(1)
            )
        ).scalar_one_or_none()
