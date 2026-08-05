"""Роут: POST /clinic/cash/payments — провести платёж по приёму (cash.operate)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.cash.application.use_cases.record_payment import RecordPaymentUseCase
from app.modules.cash.presentation.deps import get_record_payment_use_case
from app.modules.cash.presentation.schemas import PaymentItem, RecordPaymentRequest
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Приём не найден в этой клинике",
        "content": {
            "application/json": {
                "example": {"error": "appointment_not_found", "message": "Приём не найден"}
            }
        },
    },
    422: {
        "description": "Переплата, нет остатка или некорректная сумма",
        "content": {
            "application/json": {
                "example": {
                    "error": "overpayment",
                    "message": "Сумма больше остатка по приёму",
                    "outstanding": 500,
                }
            }
        },
    },
}


@router.post(
    "/clinic/cash/payments",
    response_model=PaymentItem,
    status_code=status.HTTP_201_CREATED,
    response_description="Проведённый платёж",
    summary="Провести платёж по приёму",
    responses=_ERRORS,
)
async def record_payment(
    body: RecordPaymentRequest,
    context: ClientContext = Depends(require_permission("cash.operate")),
    use_case: RecordPaymentUseCase = Depends(get_record_payment_use_case),
) -> PaymentItem:
    """Приход денег в кассу за конкретный приём.

    Сумма не может превышать остаток по приёму (``price_total − оплачено``);
    переплата → 422 ``overpayment`` с полем ``outstanding``. Кассир проставляется
    по членству вызывающего в этой клинике.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(
        context.clinic_id,
        context.account_id,
        body.appointment_id,
        body.amount_tiyin,
        body.method,
        body.comment,
    )
    return PaymentItem.from_record(record)
