"""Роуты учёта использования: по клинике за период и по платформе за месяц.

Только для команды Skrezo: клиника свои счётчики увидит вместе с пакетами
тарифа (Ф7), а прайс-лист провайдеров — наша внутренняя кухня.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query

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

# Ошибки доступа общие для обеих ручек: смотреть учёт может только команда Skrezo.
_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Нет access-токена (`unauthorized`) или он невалиден (`invalid_token`)",
        "content": {
            "application/json": {
                "examples": {
                    "no_token": {
                        "summary": "Нет заголовка Authorization",
                        "value": {"error": "unauthorized", "message": "Требуется авторизация"},
                    },
                    "invalid_token": {
                        "summary": "Токен невалиден или истёк",
                        "value": {"error": "invalid_token", "message": "Невалидный токен"},
                    },
                }
            }
        },
    },
    403: {
        "description": "Токен не администратора Skrezo",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_platform_admin",
                    "message": "Требуется доступ администратора Skrezo",
                }
            }
        },
    },
}

_PLATFORM_ERRORS: dict[int | str, dict[str, Any]] = {
    **_AUTH_ERRORS,
    422: {
        "description": "Месяц не в формате `YYYY-MM`",
        "content": {
            "application/json": {
                "example": {
                    "error": "validation_error",
                    "message": "Тело запроса не прошло валидацию",
                    "details": [
                        {
                            "type": "string_pattern_mismatch",
                            "loc": ["query", "period"],
                            "msg": "String should match pattern '^\\d{4}-(0[1-9]|1[0-2])$'",
                        }
                    ],
                }
            }
        },
    },
}

_CLINIC_ERRORS: dict[int | str, dict[str, Any]] = {
    **_AUTH_ERRORS,
    422: {
        "description": "Конец периода раньше начала (`invalid_period`) или дата не по формату",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_period": {
                        "summary": "dateTo раньше dateFrom",
                        "value": {
                            "error": "invalid_period",
                            "message": "Конец периода раньше начала",
                        },
                    },
                    "validation_error": {
                        "summary": "Дата не в формате YYYY-MM-DD",
                        "value": {
                            "error": "validation_error",
                            "message": "Тело запроса не прошло валидацию",
                            "details": [
                                {
                                    "type": "date_from_datetime_parsing",
                                    "loc": ["query", "dateFrom"],
                                    "msg": "Input should be a valid date",
                                }
                            ],
                        },
                    },
                }
            }
        },
    },
}


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


@router.get(
    "/admin/usage",
    response_model=PlatformUsageResponse,
    summary="Использование по всем клиникам за месяц",
    response_description="Счётчики и смета по каждой клинике, где было хоть что-то",
    responses=_PLATFORM_ERRORS,
)
async def platform_usage(
    period: str | None = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Месяц `YYYY-MM`; пусто — текущий месяц по UTC",
        examples=["2026-09"],
    ),
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetUsageUseCase = Depends(get_usage_use_case),
) -> PlatformUsageResponse:
    """Основа для цен и тарифных пакетов: сколько диалогов, токенов и платных
    сообщений WhatsApp съедает каждая клиника и во что это обходится.

    Смета считается по прайс-листу из настроек сервера (`rateCard` в ответе); если
    ставки нулевые, смета равна 0. В список попадают только клиники, у которых за
    месяц была хоть какая-то активность. Месяц берётся по UTC.

    Только для команды Skrezo: клиникам этот отчёт не показывается.
    """
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
    responses=_CLINIC_ERRORS,
)
async def clinic_usage(
    clinic_id: uuid.UUID = Path(description="Клиника из списка клиентов"),
    date_from: date | None = Query(
        default=None,
        alias="dateFrom",
        description="Начало периода, UTC-день; пусто — первое число текущего месяца",
        examples=["2026-09-01"],
    ),
    date_to: date | None = Query(
        default=None,
        alias="dateTo",
        description="Конец периода включительно; пусто — последний день текущего месяца",
        examples=["2026-09-30"],
    ),
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetUsageUseCase = Depends(get_usage_use_case),
) -> ClinicUsageResponse:
    """Счётчики одной клиники по дням и сумма за период, со сметой.

    По умолчанию — текущий месяц по UTC; можно задать только одну границу, вторая
    возьмётся из текущего месяца. Дни без активности в списке отсутствуют.
    Для неизвестной клиники ответ — пустой список и нули, не 404.

    Только для команды Skrezo: клиникам этот отчёт не показывается.
    """
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
