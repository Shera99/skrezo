"""Разрешение контекста отчёта: таймзона/язык клиники и границы периода."""

from __future__ import annotations

import uuid
from datetime import date

from app.core.errors import NotFoundError
from app.modules.reports.domain.period import (
    PeriodBounds,
    ReportPeriod,
    resolve_period,
    resolve_tz,
)
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository
from app.shared.db.enums import Lang


async def clinic_report_context(
    reports: ReportsRepository,
    clinic_id: uuid.UUID,
    period: ReportPeriod | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[PeriodBounds, str, Lang]:
    """Границы периода (в таймзоне клиники), её таймзона и язык.

    Период считается в таймзоне клиники: «последние 7 дней» — это её сутки,
    а не UTC-сутки.
    """
    context = await reports.clinic_context(clinic_id)
    if context is None:
        raise NotFoundError("Клиника не найдена", code="clinic_not_found")
    timezone, lang = context
    bounds = resolve_period(period, date_from, date_to, resolve_tz(timezone))
    return bounds, timezone, lang
