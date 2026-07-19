"""Use-case: завершить приём и (опционально) провести оплату — одной транзакцией."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.cash.application.services.cash_service import CashService
from app.shared.db.enums import AppointmentStatus, PaymentMethod


@dataclass(frozen=True, slots=True)
class CompletePayment:
    """Оплата, проводимая вместе с завершением приёма."""

    amount: int
    method: PaymentMethod
    comment: str | None


class CompleteAppointmentUseCase:
    def __init__(
        self, session: AsyncSession, appointments: AppointmentRepository, cash: CashService
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        appointment_id: uuid.UUID,
        payment: CompletePayment | None,
    ) -> AppointmentDetail:
        """Перевести приём в ``completed`` и, если передана оплата, провести её.

        Смена статуса и платёж — в одной транзакции: если оплата не проходит
        (переплата/нет остатка), завершение откатывается. Отменённый приём
        завершить нельзя (422), уже завершённый — 409 (нет двойного завершения).
        """
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        if appointment.status is AppointmentStatus.COMPLETED:
            raise ConflictError("Приём уже завершён", code="already_completed")
        if appointment.status is AppointmentStatus.CANCELLED:
            raise ValidationAppError(
                "Отменённый приём нельзя завершить", code="cannot_complete_cancelled"
            )

        await self._appointments.update(appointment, {"status": AppointmentStatus.COMPLETED})
        if payment is not None:
            await self._cash.record_payment(
                clinic_id=clinic_id,
                account_id=account_id,
                appointment_id=appointment_id,
                amount=payment.amount,
                method=payment.method,
                comment=payment.comment,
            )
        await self._session.commit()

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        teeth = list(await self._appointments.teeth(appointment_id))
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            teeth=teeth,
        )
