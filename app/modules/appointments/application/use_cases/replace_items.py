"""Use-case: заменить позиции приёма (чек) и пересчитать итог."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.items import (
    ensure_items_valid,
    resolve_service_prices,
)
from app.modules.appointments.domain.models import AppointmentDetail, ItemInput
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.cash.application.services.cash_service import CashService
from app.shared.db.enums import AppointmentStatus


class ReplaceItemsUseCase:
    def __init__(
        self, session: AsyncSession, appointments: AppointmentRepository, cash: CashService
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        appointment_id: uuid.UUID,
        items: list[ItemInput],
        *,
        force: bool = False,
    ) -> AppointmentDetail:
        """Положить новый список позиций приёма вместо прежнего.

        Работы становятся известны по ходу приёма, а не при записи, поэтому чек
        правится отдельно от статуса и даты. Правило простое: пока приём идёт —
        чек живой, как только он закрыт или отменён — это финансовый факт, и
        меняют его возвратом, а не переписыванием (409 / 422).

        Итог считается заново из позиций. Если он оказался **ниже уже
        оплаченного**, правка отвергается: иначе в кассе появился бы приём с
        отрицательным долгом, которого не видно ни в долгах, ни в возвратах.
        """
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        if appointment.status is AppointmentStatus.COMPLETED:
            raise ConflictError(
                "Приём завершён: чек меняют возвратом, а не правкой позиций",
                code="appointment_completed",
            )
        if appointment.status is AppointmentStatus.CANCELLED:
            raise ValidationAppError(
                "У отменённого приёма нет чека", code="appointment_cancelled"
            )

        # Врач у приёма уже назначен — против него и проверяем состав: положить
        # «Имплантацию» в чек терапевта нельзя так же, как записать его на неё.
        await ensure_items_valid(
            clinic_id,
            items,
            self._appointments,
            doctor_id=appointment.doctor_membership_id,
            force=force,
        )
        prices = await resolve_service_prices(items, self._appointments)
        await self._appointments.replace_items(appointment, items, prices)

        billing = await self._cash.billing(clinic_id, appointment_id)
        if billing is not None and appointment.price_total_tiyin < billing.paid_tiyin:
            # Откат: чек ниже оплаченного оставил бы кассу с «отрицательным долгом».
            await self._session.rollback()
            raise ValidationAppError(
                "Итог приёма ниже уже оплаченного — сначала оформите возврат",
                code="total_below_paid",
                extra={"paidTiyin": billing.paid_tiyin},
            )
        await self._session.commit()

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            items=list(await self._appointments.items(appointment_id)),
        )
