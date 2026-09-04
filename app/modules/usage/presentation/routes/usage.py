"""Роуты учёта использования: по клинике за период и по платформе за месяц.

Только для команды Skrezo: клиника свои счётчики увидит вместе с пакетами
тарифа (Ф7), а прайс-лист провайдеров — наша внутренняя кухня.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query

from app.core.errors import ValidationAppError
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.usage.application.use_cases.get_usage import (
    GetUsageUseCase,
    month_bounds,
    rate_card_from_settings,
    totals_of,
)
from app.modules.usage.presentation.deps import get_usage_use_case
from app.modules.usage.presentation.schemas import (
    ClinicUsageDayOut,
    ClinicUsageResponse,
    PlatformClinicUsageOut,
    PlatformUsageResponse,
    RateCardOut,
    UsageTotalsOut,
)

router = APIRouter()


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


@router.get(
    "/admin/usage",
    response_model=PlatformUsageResponse,
    summary="Использование по всем клиникам за месяц",
    response_description="Счётчики и смета по каждой клинике, где было хоть что-то",
)
async def platform_usage(
    period: str | None = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Месяц `YYYY-MM`; пусто — текущий",
        examples=["2026-09"],
    ),
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetUsageUseCase = Depends(get_usage_use_case),
) -> PlatformUsageResponse:
    """Основа для цен и тарифных пакетов: сколько диалогов, токенов и платных
    сообщений WhatsApp съедает каждая клиника и во что это обходится по
    прайс-листу из настроек (нули в ставках — смета не считается)."""
    period = period or _current_period()
    clinics, totals, cost = await use_case.by_platform(period)
    return PlatformUsageResponse(
        period=period,
        clinics=[
            PlatformClinicUsageOut(
                clinic_id=item.clinic_id,
                clinic_name=item.clinic_name,
                totals=UsageTotalsOut.from_totals(item.totals),
                estimated_cost_tiyin=item.estimated_cost_tiyin,
            )
            for item in clinics
        ],
        totals=UsageTotalsOut.from_totals(totals),
        estimated_cost_tiyin=cost,
        rate_card=RateCardOut.from_rates(rate_card_from_settings()),
    )


@router.get(
    "/admin/clients/{clinic_id}/usage",
    response_model=ClinicUsageResponse,
    summary="Использование клиники по дням",
    response_description="Дневные счётчики за период, сумма и смета",
)
async def clinic_usage(
    clinic_id: uuid.UUID,
    date_from: date | None = Query(default=None, alias="dateFrom", description="Начало (UTC-день)"),
    date_to: date | None = Query(default=None, alias="dateTo", description="Конец включительно"),
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetUsageUseCase = Depends(get_usage_use_case),
) -> ClinicUsageResponse:
    """По умолчанию — текущий месяц. Дни без активности в списке отсутствуют."""
    if date_from is None or date_to is None:
        default_from, default_to = month_bounds(_current_period())
        date_from = date_from or default_from
        date_to = date_to or default_to
    if date_to < date_from:
        raise ValidationAppError("Конец периода раньше начала", code="invalid_period")
    rows, totals, cost = await use_case.by_clinic(clinic_id, date_from, date_to)
    return ClinicUsageResponse(
        clinic_id=clinic_id,
        date_from=date_from,
        date_to=date_to,
        days=[
            ClinicUsageDayOut(day=r.day, totals=UsageTotalsOut.from_totals(totals_of(r)))
            for r in rows
        ],
        totals=UsageTotalsOut.from_totals(totals),
        estimated_cost_tiyin=cost,
        rate_card=RateCardOut.from_rates(rate_card_from_settings()),
    )
