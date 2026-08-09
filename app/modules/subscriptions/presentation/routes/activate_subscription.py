"""Роут: POST /admin/clients/{id}/subscription/activate — отметить оплату."""

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
    ActivateSubscriptionRequest,
    AdminSubscriptionResponse,
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
    422: {
        "description": "Дата окончания периода в прошлом",
        "content": {
            "application/json": {
                "example": {
                    "error": "paid_until_in_past",
                    "message": "Дата окончания периода должна быть в будущем",
                }
            }
        },
    },
}


@router.post(
    "/admin/clients/{clinic_id}/subscription/activate",
    response_model=AdminSubscriptionResponse,
    summary="Отметить оплату подписки клиники",
    response_description="Подписка после активации",
    responses=_ERRORS,
)
async def activate_subscription(
    clinic_id: uuid.UUID,
    body: ActivateSubscriptionRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ManageSubscriptionUseCase = Depends(get_manage_subscription_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AdminSubscriptionResponse:
    """Открыть клинике полный доступ до указанной даты.

    Платёжного провайдера пока нет: клиники платят переводом на счёт, и отметить
    это может только команда Skrezo. Суммы ручка не принимает — журнала платежей
    ещё не существует, и класть её было бы некуда.

    Доступ возвращается **сразу**: кеш состояния подписки сбрасывается, иначе
    клиент до полуминуты оставался бы в режиме чтения. Конец периода — та самая
    дата, по которой доступ закроется обратно, даже если воркер не работает.
    """
    subscription = await use_case.activate(
        clinic_id, paid_until=body.paid_until, period_start=body.period_start
    )
    await audit.record(
        actor_id=actor,
        action="subscription.activate",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"paidUntil": body.paid_until.isoformat()},
    )
    return AdminSubscriptionResponse.from_subscription(subscription)
