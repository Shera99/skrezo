"""Use-case: скидка на приём в пределах потолка роли."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationAppError,
)
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.domain.pricing import apply_discount
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.cash.application.services.cash_service import CashService
from app.shared.db.enums import AppointmentStatus


class ApplyDiscountUseCase:
    def __init__(
        self,
        session: AsyncSession,
        appointments: AppointmentRepository,
        cash: CashService,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._cash = cash
        self._audit = audit

    async def execute(
        self,
        clinic_id: uuid.UUID,
        appointment_id: uuid.UUID,
        percent: int,
        *,
        actor_id: uuid.UUID,
        max_percent: int,
    ) -> AppointmentDetail:
        """Поставить приёму скидку ``percent``% и пересчитать итог.

        Скидка — единственная операция, где сотрудник в одиночку уменьшает
        выручку, поэтому она подотчётна: ограничена потолком роли (403 сверх
        него) и попадает в аудит. Ноль снимает скидку.

        Итог к оплате = сумма позиций − скидка; долг кассы считается по нему,
        поэтому опустить итог ниже уже оплаченного нельзя (422) — сначала
        возврат. Завершённый приём — финансовый факт: скидку на него не дают
        (409), у отменённого нет чека (422).
        """
        if percent > max_percent:
            raise ForbiddenError(
                f"Потолок скидки вашей роли — {max_percent}%",
                code="discount_limit_exceeded",
                extra={"maxDiscountPercent": max_percent},
            )

        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        if appointment.status is AppointmentStatus.COMPLETED:
            raise ConflictError(
                "Приём завершён: скидку на него уже не дают",
                code="appointment_completed",
            )
        if appointment.status is AppointmentStatus.CANCELLED:
            raise ValidationAppError(
                "У отменённого приёма нет чека", code="appointment_cancelled"
            )

        subtotal = await self._appointments.items_total(appointment_id)
        discount, payable = apply_discount(subtotal, percent)

        billing = await self._cash.billing(clinic_id, appointment_id)
        if billing is not None and payable < billing.paid_tiyin:
            raise ValidationAppError(
                "Итог приёма со скидкой ниже уже оплаченного — сначала оформите возврат",
                code="total_below_paid",
                extra={"paidTiyin": billing.paid_tiyin},
            )

        previous = appointment.discount_percent
        await self._appointments.update(
            appointment,
            {
                "discount_percent": percent,
                "discount_tiyin": discount,
                "price_total_tiyin": payable,
            },
        )
        await self._session.commit()

        # Запись клиничная: её читает журнал клиники, а не поддержка Skrezo.
        # Клиника теперь колонкой, а не полем payload — по ней идёт отбор.
        await self._audit.record_clinic(
            actor_id=actor_id,
            clinic_id=clinic_id,
            action="appointment.discount",
            entity_type="appointment",
            entity_id=appointment_id,
            payload={"from": previous, "to": percent, "discountTiyin": discount},
        )

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            items=list(await self._appointments.items(appointment_id)),
        )
