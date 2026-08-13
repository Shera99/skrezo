"""Use-case: календарь дня («лента дня») — колонки врачей и карточки приёмов."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

from app.core.errors import NotFoundError
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.modules.schedule.domain.availability import (
    break_span,
    clinic_span,
    day_bounds,
    doctor_span,
    intersect,
)
from app.modules.schedule.domain.models import DayCalendar, DoctorColumn, TimeSpan
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class GetDayUseCase:
    def __init__(self, schedule: ScheduleRepository, availability: AvailabilityService) -> None:
        self._schedule = schedule
        self._availability = availability

    async def execute(
        self, clinic_id: uuid.UUID, day: date, doctor_membership_id: uuid.UUID | None
    ) -> DayCalendar:
        clinic = await self._schedule.get_clinic(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        tz = await self._availability.clinic_timezone(clinic_id)

        # Фильтр по врачу — только по членству ИЗ ЭТОЙ клиники (иначе IDOR).
        # Чужой и несуществующий врач неразличимы: 404 в обоих случаях.
        if doctor_membership_id is not None and not await self._schedule.is_doctor_in_clinic(
            clinic_id, doctor_membership_id
        ):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")

        weekday = day.weekday()
        hours = await self._availability.clinic_hours(clinic_id, weekday)
        window = clinic_span(hours, day, tz)

        doctors = await self._schedule.doctors(clinic_id, clinic.lang)
        if doctor_membership_id is not None:
            doctors = [d for d in doctors if d.membership_id == doctor_membership_id]

        bounds = day_bounds(day, tz)
        # Из БД timestamptz приходит в UTC — раскладываем карточки в таймзоне
        # клиники, чтобы сетка и карточки были в одном представлении.
        appointments = [
            replace(
                block,
                start_at=block.start_at.astimezone(tz),
                end_at=block.end_at.astimezone(tz),
            )
            for block in await self._schedule.appointments_of_day(
                clinic_id, bounds, doctor_membership_id=doctor_membership_id
            )
        ]
        # Графики и отсутствия всех врачей — пакетно, без запроса на колонку.
        membership_ids = [d.membership_id for d in doctors]
        individual = await self._schedule.doctor_hours_bulk(clinic_id, weekday)
        time_off = await self._schedule.time_off(clinic_id, membership_ids, bounds)

        counts: dict[uuid.UUID, int] = {}
        for appointment in appointments:
            if appointment.doctor_membership_id is not None:
                counts[appointment.doctor_membership_id] = (
                    counts.get(appointment.doctor_membership_id, 0) + 1
                )

        columns = [
            self._column(
                doctor_id=doctor.membership_id,
                full_name=doctor.full_name,
                specialization_name=doctor.specialization_name,
                clinic_window=window,
                individual_span=(
                    doctor_span(individual[doctor.membership_id], day, tz)
                    if doctor.membership_id in individual
                    else None
                ),
                has_individual=doctor.membership_id in individual,
                time_off=time_off.get(doctor.membership_id, []),
                appointments_count=counts.get(doctor.membership_id, 0),
            )
            for doctor in doctors
        ]

        return DayCalendar(
            day=day,
            timezone=clinic.timezone,
            opens_at=window.start if window is not None else None,
            closes_at=window.end if window is not None else None,
            is_working=window is not None,
            break_span=break_span(hours, day, tz) if window is not None else None,
            doctors=columns,
            appointments=appointments,
        )

    def _column(
        self,
        *,
        doctor_id: uuid.UUID,
        full_name: str,
        specialization_name: str | None,
        clinic_window: TimeSpan | None,
        individual_span: TimeSpan | None,
        has_individual: bool,
        time_off: list[TimeSpan],
        appointments_count: int,
    ) -> DoctorColumn:
        """Собрать колонку врача: рабочее окно = часы клиники ∩ личный график."""
        window = clinic_window
        if has_individual:
            window = (
                intersect(clinic_window, individual_span)
                if clinic_window is not None and individual_span is not None
                else None
            )
        # Отсутствие, накрывающее всё окно, делает врача нерабочим на этот день.
        if window is not None and any(off.contains(window) for off in time_off):
            window = None
        return DoctorColumn(
            membership_id=doctor_id,
            full_name=full_name,
            specialization_name=specialization_name,
            appointments_count=appointments_count,
            works_from=window.start if window is not None else None,
            works_to=window.end if window is not None else None,
            is_working=window is not None,
            # Отсутствия уже загружены пакетно выше — отдаём их как есть, чтобы
            # клиенту не пришлось запрашивать time-off на каждого врача колонки.
            time_off=tuple(sorted(time_off, key=lambda off: off.start)),
        )
