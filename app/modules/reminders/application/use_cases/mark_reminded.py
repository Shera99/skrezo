"""Use-case: отметить, что пациенту напомнили."""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.reminders.domain.models import ReminderKind, ReminderMarkRecord
from app.modules.reminders.infrastructure.repositories.reminder_repository import (
    ReminderRepository,
)
from app.modules.reports.domain.period import resolve_tz


class MarkRemindedUseCase:
    def __init__(self, session: AsyncSession, reminders: ReminderRepository) -> None:
        self._session = session
        self._reminders = reminders

    async def execute(
        self,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        patient_id: uuid.UUID,
        kind: ReminderKind,
        appointment_id: uuid.UUID | None,
        comment: str | None,
    ) -> ReminderMarkRecord:
        """Записать отметку от имени членства вызывающего в этой клинике.

        Пациент должен быть пациентом клиники (есть её приём), приём-источник —
        приёмом этого пациента в этой клинике: иначе журнал одной клиники
        ссылался бы на данные другой (IDOR). Чужое → 404, как «не существует».
        Момент отметки отдаётся в таймзоне клиники — как и в журнале.
        """
        if not await self._reminders.clinic_treats(clinic_id, patient_id):
            raise NotFoundError("Пациент не найден в клинике", code="patient_not_found")
        if appointment_id is not None and not await self._reminders.appointment_of(
            clinic_id, patient_id, appointment_id
        ):
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        record = await self._reminders.create(
            clinic_id=clinic_id,
            patient_id=patient_id,
            kind=kind,
            appointment_id=appointment_id,
            comment=comment,
            marked_by_membership_id=await self._reminders.own_membership(account_id, clinic_id),
        )
        await self._session.commit()
        tz = resolve_tz(await self._reminders.clinic_timezone(clinic_id))
        return replace(record, marked_at=record.marked_at.astimezone(tz))
