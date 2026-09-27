"""Use-case: отчёт по врачам за период."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

from app.modules.reports.application.context import clinic_report_context
from app.modules.reports.domain.models import DoctorReportRow, DoctorsReport
from app.modules.reports.domain.period import ReportPeriod, resolve_tz
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository
from app.modules.schedule.application.services.availability_service import AvailabilityService


class GetDoctorsReportUseCase:
    def __init__(self, reports: ReportsRepository, availability: AvailabilityService) -> None:
        self._reports = reports
        self._availability = availability

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        period: ReportPeriod | None,
        date_from: date | None,
        date_to: date | None,
        own_membership_id: uuid.UUID | None,
    ) -> DoctorsReport:
        """Приёмы, неявки, деньги, долг и загрузка по каждому врачу.

        С правом ``reports.view_own`` в выдаче останется одна строка — сам
        вызывающий: разрезать чужие показатели врачу незачем.

        Загрузка — доля рабочего времени периода, занятая приёмами: минуты
        приёмов (без отменённых и неявок) к рабочим минутам по графику. Рабочие
        минуты считает расписание — той же арифметикой, что свободные слоты
        (часы клиники ∩ график врача − обед − отсутствия), а не копией её здесь.
        У периода без начала («за всё время») знаменателя нет — загрузка ``None``.
        """
        bounds, timezone, lang = await clinic_report_context(
            self._reports, clinic_id, period, date_from, date_to
        )
        rows = await self._reports.doctors(
            clinic_id, bounds.start, bounds.end, own_membership_id, lang=lang
        )
        if bounds.date_from is None or not rows:
            return DoctorsReport(timezone=timezone, rows=rows)
        working = await self._availability.working_minutes(
            clinic_id,
            [row.membership_id for row in rows],
            bounds.date_from,
            bounds.date_to,
            resolve_tz(timezone),
        )
        return DoctorsReport(
            timezone=timezone,
            rows=[_with_workload(row, working.get(row.membership_id, 0)) for row in rows],
        )


def _with_workload(row: DoctorReportRow, working_minutes: int) -> DoctorReportRow:
    """Дописать в строку врача рабочие минуты и загрузку в процентах."""
    percent = round(row.booked_minutes * 100 / working_minutes) if working_minutes else None
    return replace(row, working_minutes=working_minutes, workload_percent=percent)
