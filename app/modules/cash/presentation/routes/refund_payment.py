"""Роут: POST /clinic/cash/payments/{id}/refund — возврат по платежу (cash.operate)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.cash.application.use_cases.refund_payment import RefundPaymentUseCase
from app.modules.cash.presentation.deps import get_refund_payment_use_case
from app.modules.cash.presentation.schemas import PaymentItem
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Платёж не найден в этой клинике",
        "content": {
            "application/json": {
                "example": {"error": "payment_not_found", "message": "Платёж не найден"}
            }
        },
    },
    409: {
        "description": "По платежу уже есть возврат",
        "content": {
            "application/json": {
                "example": {
                    "error": "already_refunded",
                    "message": "По платежу уже есть возврат",
                }
            }
        },
    },
    422: {
        "description": "Операция не является платежом (возврат по возврату)",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_a_payment",
                    "message": "Возврат можно провести только по платежу",
                }
            }
        },
    },
}


@router.post(
    "/clinic/cash/payments/{payment_id}/refund",
    response_model=PaymentItem,
    status_code=status.HTTP_201_CREATED,
    response_description="Проведённый возврат",
    summary="Возврат по платежу",
    responses=_ERRORS,
)
async def refund_payment(
    payment_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("cash.operate")),
    use_case: RefundPaymentUseCase = Depends(get_refund_payment_use_case),
) -> PaymentItem:
    """Полный возврат по конкретному платежу.

    Возврат возможен только по платежу и только один раз (повторный → 409).
    Увеличивает остаток по приёму обратно. Скоуп по клинике из токена закрывает
    возврат чужого платежа (404).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(context.clinic_id, context.account_id, payment_id)
    return PaymentItem.from_record(record)
