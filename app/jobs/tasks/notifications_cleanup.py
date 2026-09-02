"""Ночная уборка контура уведомлений — сроки хранения из ТЗ-СУ §14.

* телеметрия ``notification_delivery`` — 3 месяца;
* ``event`` (только обработанные — упавшие остаются алертом) — 12 месяцев;
  ``inbox_item`` уходит вместе с событием каскадом, отдельной чистки не нужно;
* ``slot_hold`` — просроченные и не подтверждённые резервы слотов (Ф2):
  час после истечения им ни к чему, подтверждённые хранятся (след записи).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.push.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.push.infrastructure.repositories.event_repository import EventRepository
from app.shared.db.models import SlotHold

#: Сроки хранения (§14): телеметрия — 3 месяца, события с инбоксом — 12.
DELIVERY_RETENTION_DAYS = 90
EVENT_RETENTION_DAYS = 365
#: Просроченный резерв слота держим час — на случай разбора «кнопка не сработала».
HOLD_GRACE = timedelta(hours=1)


async def cleanup_notifications(ctx: dict[str, Any]) -> int:
    """Удалить отжившее; вернуть суммарное число удалённых строк."""
    async with JobRunRecorder("cleanup_notifications", attempt(ctx)) as run:
        now = datetime.now(UTC)
        async with session_factory() as session:
            deliveries = await DeliveryRepository(session).delete_older_than(
                now - timedelta(days=DELIVERY_RETENTION_DAYS)
            )
            events = await EventRepository(session).delete_older_than(
                now - timedelta(days=EVENT_RETENTION_DAYS)
            )
            holds_result = await session.execute(
                delete(SlotHold).where(
                    SlotHold.appointment_id.is_(None),
                    SlotHold.expires_at < now - HOLD_GRACE,
                )
            )
            holds = int(getattr(holds_result, "rowcount", 0) or 0)
            await session.commit()
        run.note(
            f"телеметрии: {deliveries}, событий (с инбоксом): {events}, резервов: {holds}"
        )
        return deliveries + events + holds
