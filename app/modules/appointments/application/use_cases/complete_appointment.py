"""Use-case: завершить приём и (опционально) провести оплату — одной транзакцией."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.closing import ensure_closable
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.cash.application.services.cash_service import CashService
from app.modules.push.application.outbox import emit_event
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
        *,
        no_charge: bool = False,
        no_charge_comment: str | None = None,
    ) -> AppointmentDetail:
        """Перевести приём в ``completed`` и, если передана оплата, провести её.

        Смена статуса и платёж — в одной транзакции: если оплата не проходит
        (переплата/нет остатка), завершение откатывается. Отменённый приём
        завершить нельзя (422), уже завершённый — 409 (нет двойного завершения).
        Приём без позиций закрывается только как бесплатный: ``no_charge`` с
        пояснением (см. ``services/closing``). Закрытие доводит незакрытые
        позиции чека до ``completed`` (отменённые не трогает) и обновляет
        одонтограмму по исходам услуг.
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
        if appointment.status is AppointmentStatus.DRAFT:
            raise ValidationAppError(
                "Черновик нельзя завершить: сначала назначьте дату приёма",
                code="cannot_complete_draft",
            )

        # Флаг «бесплатно» мог быть проставлен раньше (PATCH) — тогда его не
        # обязательно повторять в теле завершения.
        no_charge = no_charge or appointment.no_charge
        comment = no_charge_comment or appointment.no_charge_comment
        ensure_closable(
            has_items=await self._appointments.has_items(appointment_id),
            no_charge=no_charge,
            comment=comment,
        )

        fields: dict[str, Any] = {"status": AppointmentStatus.COMPLETED}
        if no_charge:
            fields["no_charge"] = True
            fields["no_charge_comment"] = comment
        await self._appointments.update(appointment, fields)
        # Приём закрыт — значит запланированное сделано. Отменённые позиции
        # остаются отменёнными: врач уже сказал, что их не делал.
        await self._appointments.mark_items_done(appointment_id)
        # Карта зубов догоняет историю: вылеченный зуб перестаёт числиться больным.
        await self._appointments.sync_odontogram(appointment_id)
        # События — в этой же транзакции (outbox): завершение — смена статуса,
        # оплата — отдельное событие кассы (адресаты у них разные).
        await emit_event(
            self._session,
            clinic_id=clinic_id,
            type_="appointment.status_changed",
            entity_type="appointment",
            entity_id=appointment_id,
        )
        if payment is not None:
            record = await self._cash.record_payment(
                clinic_id=clinic_id,
                account_id=account_id,
                appointment_id=appointment_id,
                amount=payment.amount,
                method=payment.method,
                comment=payment.comment,
            )
            await emit_event(
                self._session,
                clinic_id=clinic_id,
                type_="payment.created",
                entity_type="payment",
                entity_id=record.id,
                payload={
                    "appointmentId": str(appointment_id),
                    "patientId": str(record.patient_id),
                    "amountTiyin": record.amount_tiyin,
                },
            )
        await self._session.commit()

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        items = list(await self._appointments.items(appointment_id))
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            items=items,
        )
