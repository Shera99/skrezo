"""Репозиторий телеметрии доставки: записи попыток, показ, сводка, уборка."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.inbox_item import InboxItem
from app.shared.db.models.notification_delivery import NotificationDelivery


@dataclass(frozen=True, slots=True)
class TelemetryRow:
    """Сводка доставляемости по (платформа, провайдер)."""

    platform: str | None
    provider: str | None
    sent: int
    failed: int
    displayed: int


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        inbox_item_id: int,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        platform: str,
        provider: str,
        sent_at: datetime | None,
        error: str | None = None,
    ) -> None:
        """Записать попытку доставки пуша (успех — ``sent_at``, отказ — ``error``)."""
        self._session.add(
            NotificationDelivery(
                inbox_item_id=inbox_item_id,
                clinic_id=clinic_id,
                account_id=account_id,
                channel="push",
                platform=platform,
                provider=provider,
                sent_at=sent_at,
                error=error,
            )
        )

    async def mark_displayed(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, inbox_item_ids: Sequence[int]
    ) -> int:
        """Проставить показ СВОИМ записям; идемпотентно, чужие id — молча мимо."""
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
            update(NotificationDelivery)
            .where(
                NotificationDelivery.inbox_item_id.in_(own),
                NotificationDelivery.displayed_at.is_(None),
            )
            .values(displayed_at=datetime.now(UTC))
            .returning(NotificationDelivery.id)
        )
        return len(list(result.scalars()))

    async def summary(self, days: int) -> list[TelemetryRow]:
        """Доставляемость за период по платформам и провайдерам."""
        since = datetime.now(UTC) - timedelta(days=days)
        rows = (
            await self._session.execute(
                select(
                    NotificationDelivery.platform,
                    NotificationDelivery.provider,
                    func.count().filter(NotificationDelivery.sent_at.is_not(None)),
                    func.count().filter(NotificationDelivery.error.is_not(None)),
                    func.count().filter(NotificationDelivery.displayed_at.is_not(None)),
                )
                .where(NotificationDelivery.created_at >= since)
                .group_by(NotificationDelivery.platform, NotificationDelivery.provider)
                .order_by(NotificationDelivery.platform, NotificationDelivery.provider)
            )
        ).all()
        return [
            TelemetryRow(
                platform=r[0], provider=r[1], sent=r[2], failed=r[3], displayed=r[4]
            )
            for r in rows
        ]

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Уборка телеметрии по возрасту (хранение — 3 месяца, §14)."""
        result = await self._session.execute(
            delete(NotificationDelivery)
            .where(NotificationDelivery.created_at < cutoff)
            .returning(NotificationDelivery.id)
        )
        return len(list(result.scalars()))
