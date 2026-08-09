"""Use-case: сводка текущего дня клиники (утренний экран).

Собирается из тех же запросов, что и обычная сводка отчётов, — своих таблиц у
неё нет. Смысл отдельной ручки в двух вещах: «сегодня» определяет сервер (в
таймзоне клиники, а не браузера) и долг берётся общий, а не за сутки.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core.errors import NotFoundError
from app.modules.reports.domain.models import DayDashboard
from app.modules.reports.domain.period import resolve_period, resolve_tz
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


class GetDayDashboardUseCase:
    def __init__(self, reports: ReportsRepository) -> None:
        self._reports = reports

    async def execute(
        self, clinic_id: uuid.UUID, *, own_membership_id: uuid.UUID | None
    ) -> DayDashboard:
        """Приёмы по статусам и выручка за сегодня плюс общий долг клиники.

        ``own_membership_id`` (право ``reports.view_own``) сужает приёмы и деньги
        до тех, где вызывающий — лечащий врач: врач видит свой день, а не чужой.
        """
        context = await self._reports.clinic_context(clinic_id)
        if context is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        timezone, _ = context
        tz = resolve_tz(timezone)

        # Сутки клиники, а не UTC: в Бишкеке смена дня на шесть часов раньше, и
        # утренний экран не должен показывать вчерашние цифры.
        today = datetime.now(tz).date()
        bounds = resolve_period(None, today, today, tz)

        revenue = await self._reports.revenue(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        appointments = await self._reports.appointments_by_status(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        # Долг — за всё время и без верхней границы: приём, назначенный на
        # сегодня позже текущего часа, — такой же долг, как вчерашний.
        charged, paid, _, _ = await self._reports.charged_and_paid(
            clinic_id, None, None, own_membership_id
        )
        return DayDashboard(
            date=today,
            timezone=timezone,
            appointments=appointments,
            revenue_tiyin=revenue.net_tiyin,
            outstanding_tiyin=charged - paid,
        )
