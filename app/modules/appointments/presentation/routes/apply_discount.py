"""Роут: PUT /clinic/appointments/{id}/discount — скидка на приём (cash.discount)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import get_discount_ceiling, require_permission
from app.modules.appointments.application.use_cases.apply_discount import ApplyDiscountUseCase
from app.modules.appointments.presentation.deps import get_apply_discount_use_case
from app.modules.appointments.presentation.schemas import (
    ApplyDiscountRequest,
    AppointmentDetailResponse,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Скидка больше потолка роли",
        "content": {
            "application/json": {
                "example": {
                    "error": "discount_limit_exceeded",
                    "message": "Потолок скидки вашей роли — 20%",
                    "maxDiscountPercent": 20,
                }
            }
        },
    },
    404: {"description": "Приём не найден"},
    409: {
        "description": "Приём завершён — скидку на него уже не дают",
        "content": {
            "application/json": {
                "example": {
                    "error": "appointment_completed",
                    "message": "Приём завершён: скидку на него уже не дают",
                }
            }
        },
    },
    422: {
        "description": "Отменённый приём либо итог со скидкой ниже оплаченного",
        "content": {
            "application/json": {
                "example": {
                    "error": "total_below_paid",
                    "message": (
                        "Итог приёма со скидкой ниже уже оплаченного — "
                        "сначала оформите возврат"
                    ),
                    "paidTiyin": 300000,
                }
            }
        },
    },
}


@router.put(
    "/clinic/appointments/{appointment_id}/discount",
    response_model=AppointmentDetailResponse,
    summary="Скидка на приём",
    response_description="Приём с пересчитанным итогом",
    responses=_ERRORS,
)
async def apply_discount(
    appointment_id: uuid.UUID,
    body: ApplyDiscountRequest,
    context: ClientContext = Depends(require_permission("cash.discount")),
    max_percent: int = Depends(get_discount_ceiling),
    use_case: ApplyDiscountUseCase = Depends(get_apply_discount_use_case),
) -> AppointmentDetailResponse:
    """Поставить приёму скидку процентом; `0` — снять.

    Скидка — единственная операция, где сотрудник в одиночку уменьшает выручку,
    поэтому она разведена на два уровня: право ``cash.discount`` разрешает её
    вообще, а потолок роли (`maxDiscountPercent`: владелец 100, администратор 20,
    врач 0) ограничивает размер. Сверх потолка — 403, попытка попадает в аудит
    только при успехе: сама операция логируется как `appointment.discount`.

    Итог к оплате = сумма позиций − скидка, долг кассы считается по нему. Опустить
    итог ниже уже оплаченного нельзя (422) — сначала возврат. Завершённый приём
    → 409, отменённый → 422.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(
        context.clinic_id,
        appointment_id,
        body.percent,
        actor_id=context.account_id,
        max_percent=max_percent,
    )
    return AppointmentDetailResponse.from_detail(detail)
