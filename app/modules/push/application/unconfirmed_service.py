"""Пуш «приём не подтверждён» — скан расписания незадолго до начала приёма.

За ``VISIT_UNCONFIRMED_PUSH_MINUTES_BEFORE`` минут (час) до начала приём,
который всё ещё ``scheduled``, поднимается событием ``appointment.unconfirmed``
врачу приёма и администраторам: позвонить и уточнить, придёт ли пациент.

От напоминаний пациенту скан не зависит. Раньше событие рождалось только из
напоминания без ответа — клиника без WhatsApp, с выключенным умением или с
недоставленным шаблоном не узнавала о неподтверждённом приёме вовсе.

Сервис знает статус в момент отправки, поэтому он точнее локального
уведомления приложения: у того статус на момент планирования.

Не поднимаются:

* запись, созданная позже чем за ``VISIT_UNCONFIRMED_FRESH_BOOKING_MINUTES``
  до начала: пациента только что записали у стойки или по телефону, спрашивать
  его «придёте ли» через несколько минут незачем;
* приём, о котором уже сообщали. Отметка — строка ``push_event`` с ключом из
  приёма и его времени: повторный тик её не дублирует (первичный ключ держит и
  гонку двух воркеров), а перенос на другое время даёт новый ключ — и новый
  пуш к новому сроку.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.push.application.outbox import emit_event
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.push_event import PushEvent

logger = structlog.get_logger(__name__)

#: Тип события каталога (``EVENT_CATALOG``) и тип строки идемпотентности.
EVENT_TYPE = "appointment.unconfirmed"
#: Потолок одного прохода: приёмов «в ближайший час» по всем клиникам заведомо
#: меньше, а остаток подберёт следующий тик через пять минут.
_BATCH = 500


class UnconfirmedVisitService:
    """Эмитит ``appointment.unconfirmed`` по приёмам, оставшимся ``scheduled``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self, now: datetime | None = None) -> int:
        """Поднять неподтверждённые приёмы ближайшего часа; вернуть число событий.

        ``VISIT_UNCONFIRMED_PUSH_MINUTES_BEFORE=0`` выключает скан.
        """
        lead = settings.visit_unconfirmed_push_minutes_before
        if lead <= 0:
            return 0
        now = now or datetime.now(UTC)
        stmt = (
            select(Appointment.id, Appointment.clinic_id, Appointment.scheduled_at)
            .where(
                Appointment.status == AppointmentStatus.SCHEDULED,
                Appointment.scheduled_at > now,
                Appointment.scheduled_at <= now + timedelta(minutes=lead),
            )
            .order_by(Appointment.scheduled_at)
            .limit(_BATCH)
        )
        fresh = settings.visit_unconfirmed_fresh_booking_minutes
        if fresh > 0:
            stmt = stmt.where(
                Appointment.created_at <= Appointment.scheduled_at - timedelta(minutes=fresh)
            )
        emitted = 0
        for appointment_id, clinic_id, scheduled_at in (await self._session.execute(stmt)).all():
            # Время — часть ключа: перенесённый приём неподтверждён уже к новому
            # сроку, и о нём нужно сказать ещё раз.
            dedup = f"unconfirmed:{appointment_id}:{scheduled_at.astimezone(UTC):%Y%m%dT%H%M}"
            inserted = (
                await self._session.execute(
                    pg_insert(PushEvent)
                    .values(
                        id=dedup,
                        type=EVENT_TYPE,
                        clinic_id=clinic_id,
                        dedup_key=f"appointment:{appointment_id}",
                    )
                    .on_conflict_do_nothing(index_elements=[PushEvent.id])
                    .returning(PushEvent.id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                continue  # об этом приёме на это время уже сообщали
            await emit_event(
                self._session,
                clinic_id=clinic_id,
                type_=EVENT_TYPE,
                entity_type="appointment",
                entity_id=appointment_id,
            )
            emitted += 1
        await self._session.commit()
        if emitted:
            logger.info("unconfirmed_visits_notified", count=emitted)
        return emitted
