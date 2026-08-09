"""Роут: POST /admin/clients/{id}/subscription/block — ручной блок Skrezo."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.presentation.deps import get_manage_subscription_use_case
from app.modules.subscriptions.presentation.schemas import (
    AdminSubscriptionResponse,
    BlockSubscriptionRequest,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиника или её подписка не найдены",
        "content": {
            "application/json": {
                "example": {
                    "error": "subscription_not_found",
                    "message": "Подписка клиники не найдена",
                }
            }
        },
    },
}


@router.post(
    "/admin/clients/{clinic_id}/subscription/block",
    response_model=AdminSubscriptionResponse,
    summary="Заблокировать подписку клиники",
    response_description="Подписка после блокировки",
    responses=_ERRORS,
)
async def block_subscription(
    clinic_id: uuid.UUID,
    body: BlockSubscriptionRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ManageSubscriptionUseCase = Depends(get_manage_subscription_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AdminSubscriptionResponse:
    """Закрыть клинике запись решением Skrezo (не из-за оплаты).

    Последствия те же, что у просрочки, — режим чтения; отличается причина, и по
    статусу `blocked` она видна. Снимается повторной активацией.
    """
    subscription = await use_case.block(clinic_id, body.reason)
    await audit.record(
        actor_id=actor,
        action="subscription.block",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"reason": body.reason} if body.reason else None,
    )
    return AdminSubscriptionResponse.from_subscription(subscription)
