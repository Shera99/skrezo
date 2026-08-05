"""Роут: POST /clinic/cash/payments/{id}/refund — возврат по платежу (cash.operate)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.cash.application.use_cases.refund_payment import RefundPaymentUseCase
from app.modules.cash.presentation.deps import get_refund_payment_use_case
from app.modules.cash.presentation.schemas import PaymentItem, RefundPaymentRequest
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
        "description": "Платёж уже возвращён полностью — возвращать нечего",
        "content": {
            "application/json": {
                "example": {
                    "error": "already_refunded",
                    "message": "Платёж уже возвращён полностью",
                }
            }
        },
    },
    422: {
        "description": (
            "Сумма больше остатка по платежу либо операция не является платежом "
            "(возврат по возврату)"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "refund_exceeds_payment",
                    "message": "Сумма больше остатка по платежу",
                    "refundableTiyin": 50000,
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
    summary="Возврат по платежу (частичный и многократный)",
    responses=_ERRORS,
)
async def refund_payment(
    payment_id: uuid.UUID,
    body: RefundPaymentRequest | None = None,
    context: ClientContext = Depends(require_permission("cash.operate")),
    use_case: RefundPaymentUseCase = Depends(get_refund_payment_use_case),
) -> PaymentItem:
    """Вернуть часть платежа или его остаток.

    «Из трёх услуг переделали одну» — обычный случай, поэтому возврат бывает
    частичным и не единственным. Ограничение одно: сумма всех возвратов не
    больше самого платежа. Без тела (или без ``amountTiyin``) возвращается весь
    остаток — привычный полный возврат.

    Возвращать нечего (остаток 0) → 409 `already_refunded`; сумма больше остатка
    → 422 `refund_exceeds_payment` с полем `refundableTiyin`. Возврат увеличивает
    остаток по приёму обратно. Скоуп по клинике из токена закрывает возврат
    чужого платежа (404).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(
        context.clinic_id,
        context.account_id,
        payment_id,
        amount=body.amount_tiyin if body is not None else None,
        comment=body.comment if body is not None else None,
    )
    return PaymentItem.from_record(record)
