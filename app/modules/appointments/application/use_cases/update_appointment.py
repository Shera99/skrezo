"""Use-case: обновить приём (статус/комментарий/врач/дата/услуга/длительность)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.appointments.application.services.booking import booking_guard
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.schedule.application.services.availability_service import (
    AvailabilityService,
    span_of,
)
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.appointment import Appointment

# Поля, при смене которых приём нужно перепроверить на окно и пересечения.
_BOOKING_FIELDS = frozenset({"scheduled_at", "duration_min", "doctor_membership_id", "service_id"})


def _affects_booking(appointment: Appointment, fields: dict[str, Any]) -> bool:
    """Нужно ли перепроверять занятость слота.

    Кроме смены геометрии приёма (время/длительность/врач/услуга) проверку обязано
    проходить и **снятие отмены**: пока приём был отменён, слот был свободен и мог
    достаться другому. Без этого случая единственной защитой остаётся констрейнт,
    а он отдаёт 500 вместо человекочитаемого 409.
    """
    if _BOOKING_FIELDS & fields.keys():
        return True
    new_status = fields.get("status", appointment.status)
    return (
        appointment.status == AppointmentStatus.CANCELLED
        and new_status != AppointmentStatus.CANCELLED
    )


class UpdateAppointmentUseCase:
    def __init__(
        self,
        session: AsyncSession,
        appointments: AppointmentRepository,
        availability: AvailabilityService,
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._availability = availability

    async def execute(
        self, clinic_id: uuid.UUID, appointment_id: uuid.UUID, fields: dict[str, Any]
    ) -> AppointmentDetail:
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")

        fields = dict(fields)
        await self._validate_refs(clinic_id, fields)
        await self._resolve_duration(clinic_id, fields)
        await self._ensure_bookable(clinic_id, appointment, fields)

        # Гвард накрывает и update() (внутри флашит UPDATE — там и срабатывает
        # констрейнт), и commit(): ловить только коммит недостаточно.
        async with booking_guard(self._session):
            await self._appointments.update(appointment, fields)
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

    async def _validate_refs(self, clinic_id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Врач и услуга обязаны быть из этой клиники — иначе IDOR."""
        doctor_id = fields.get("doctor_membership_id")
        if doctor_id is not None and not await self._availability.is_doctor(clinic_id, doctor_id):
            raise ValidationAppError("Врач не из этой клиники", code="invalid_doctor")
        service_id = fields.get("service_id")
        if service_id is not None and not await self._appointments.service_in_clinic(
            clinic_id, service_id
        ):
            raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

    async def _resolve_duration(self, clinic_id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Сменили услугу без явной длительности — берём длительность новой услуги.

        Снятие услуги (``serviceId: null``) длительность НЕ трогает: приём на 90
        минут не должен молча схлопываться до дефолтных 30 из-за того, что у него
        отвязали услугу. Нужна другая длительность — передайте ``durationMin``.
        """
        if fields.get("duration_min") is not None:
            return
        fields.pop("duration_min", None)
        if fields.get("service_id") is None:
            return
        fields["duration_min"] = await self._availability.resolve_duration(
            clinic_id, fields["service_id"], None
        )

    async def _ensure_bookable(
        self, clinic_id: uuid.UUID, appointment: Appointment, fields: dict[str, Any]
    ) -> None:
        """Перепроверить слот, если поменялось что-то влияющее на занятость."""
        if not _affects_booking(appointment, fields):
            return
        status = fields.get("status", appointment.status)
        doctor_id = fields.get("doctor_membership_id", appointment.doctor_membership_id)
        # Приём без врача или отменённый слот не занимает — как в EXCLUDE-констрейнте.
        if doctor_id is None or status == AppointmentStatus.CANCELLED:
            return
        scheduled_at = fields.get("scheduled_at", appointment.scheduled_at)
        duration_min = fields.get("duration_min", appointment.duration_min)
        if not isinstance(scheduled_at, datetime):
            return
        await self._availability.ensure_bookable(
            clinic_id,
            doctor_id,
            span_of(scheduled_at, duration_min),
            exclude_appointment_id=appointment.id,
        )
