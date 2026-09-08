"""Роут: POST /admin/payments/{id}/refund — отметить возврат платежа."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.payments.presentation.deps import get_payment_repository
from app.modules.payments.presentation.schemas import RefundRequest
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.api.schemas import OkResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Платёж не найден"},
    409: {"description": "Вернуть можно только оплаченный платёж"},
}


@router.post(
    "/admin/payments/{payment_id}/refund",
    response_model=OkResponse,
    summary="Отметить возврат",
    responses=_ERRORS,
)
async def refund_payment(
    payment_id: uuid.UUID,
    body: RefundRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    repository: PlatformPaymentRepository = Depends(get_payment_repository),
    session: AsyncSession = Depends(get_session),
    audit: AuditService = Depends(get_audit_service),
) -> OkResponse:
    """Деньги возвращаются в кабинете провайдера; здесь — учёт и причина.
    Доступ клиники не закрывается автоматически: решение о блокировке —
    отдельное (`/admin/clients/{id}/subscription/block`)."""
    payment = await repository.get(payment_id)
    if payment is None:
        raise NotFoundError("Платёж не найден", code="payment_not_found")
    if payment.status != "paid":
        raise ConflictError("Вернуть можно только оплаченный платёж", code="payment_not_paid")
    payment.status = "refunded"
    payment.failure_description = f"возврат: {body.reason}"
    await session.commit()
    await audit.record(
        actor_id=actor,
        action="payment.refund",
        entity_type="platform_payment",
        entity_id=payment.id,
        payload={"reason": body.reason, "amountTiyin": payment.amount_tiyin},
    )
    return OkResponse()
