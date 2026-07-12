"""Роут: POST /admin/payments/{id}/refund — ручная пометка возврата.

v1 — заглушка: журнала платежей нет, поэтому любой id → 404. С подключением
биллинга здесь будет создаваться запись «Возврат» со ссылкой на исходный платёж.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import NotFoundError
from app.modules.payments.presentation.schemas import RefundRequest
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.api.schemas import OkResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Платёж не найден (в v1 журнал пуст)"},
}


@router.post(
    "/admin/payments/{payment_id}/refund",
    response_model=OkResponse,
    summary="Пометить возврат (v1 — заглушка)",
    responses=_ERRORS,
)
async def refund_payment(
    payment_id: str,
    body: RefundRequest,
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
) -> OkResponse:
    # Журнала платежей пока нет — возврат провести не по чему.
    raise NotFoundError("Платёж не найден", code="payment_not_found")
