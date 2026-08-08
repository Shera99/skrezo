"""Роут: PUT /clinic/appointments/{id}/items — заменить позиции чека (appointments.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.replace_items import ReplaceItemsUseCase
from app.modules.appointments.domain.models import ItemInput
from app.modules.appointments.presentation.deps import get_replace_items_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentDetailResponse,
    ReplaceItemsRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Приём не найден"},
    409: {
        "description": "Приём завершён — чек меняют возвратом",
        "content": {
            "application/json": {
                "example": {
                    "error": "appointment_completed",
                    "message": "Приём завершён: чек меняют возвратом, а не правкой позиций",
                }
            }
        },
    },
    422: {
        "description": (
            "Некорректный зуб/услуга, отменённый приём либо итог ниже оплаченного"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "total_below_paid",
                    "message": "Итог приёма ниже уже оплаченного — сначала оформите возврат",
                    "paidTiyin": 300000,
                }
            }
        },
    },
}


@router.put(
    "/clinic/appointments/{appointment_id}/items",
    response_model=AppointmentDetailResponse,
    summary="Заменить позиции приёма (чек)",
    response_description="Приём с новым списком позиций и пересчитанным итогом",
    responses=_ERRORS,
)
async def replace_items(
    appointment_id: uuid.UUID,
    body: ReplaceItemsRequest,
    context: ClientContext = Depends(require_permission("appointments.edit")),
    use_case: ReplaceItemsUseCase = Depends(get_replace_items_use_case),
) -> AppointmentDetailResponse:
    """Положить новый список позиций вместо прежнего и пересчитать итог приёма.

    Что именно сделали, известно по ходу приёма, а не при записи, поэтому чек
    правится отдельно от статуса и даты. Передаётся **полное** состояние чека
    (как в часах работы клиники и одонтограмме): пустой список очищает его.

    Завершённый приём — финансовый факт: его чек меняют возвратом (409
    `appointment_completed`), у отменённого чека нет (422). Если новый итог ниже
    уже оплаченного — 422 `total_below_paid`: сначала возврат, иначе в кассе
    появится приём с отрицательным долгом.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = [
        ItemInput(
            service_id=i.service_id,
            price_tiyin=i.price_tiyin,
            quantity=i.quantity,
            teeth=i.teeth,
            comment=i.comment,
            status=i.status,
        )
        for i in body.items
    ]
    detail = await use_case.execute(context.clinic_id, appointment_id, items)
    return AppointmentDetailResponse.from_detail(detail)
