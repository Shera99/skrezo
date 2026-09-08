"""Роут: GET /clinic/subscription — подписка своей клиники (billing.view)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.presentation.deps import get_ledger_service
from app.modules.subscriptions.application.use_cases.get_subscription import (
    GetSubscriptionUseCase,
)
from app.modules.subscriptions.presentation.deps import get_get_subscription_use_case
from app.modules.subscriptions.presentation.schemas import ClinicSubscriptionResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "У клиники нет подписки (битые данные провижининга)",
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


@router.get(
    "/clinic/subscription",
    response_model=ClinicSubscriptionResponse,
    summary="Подписка своей клиники",
    response_description="Статус подписки, срок действия, остаток дней и занятость мест",
    responses=_ERRORS,
)
async def get_subscription(
    context: ClientContext = Depends(require_permission("billing.view")),
    use_case: GetSubscriptionUseCase = Depends(get_get_subscription_use_case),
    ledger: LedgerService = Depends(get_ledger_service),
) -> ClinicSubscriptionResponse:
    """Показать состояние подписки клиники из токена.

    Единственный способ для клиники узнать, что триал кончается: соло-врачу
    обещаны 30 дней, а на 31-й день без этой ручки не происходит ничего —
    клиент не может показать ни срок, ни повод продлить.

    Клиника берётся из токена, чужую подписку по этой ручке не посмотреть.
    Право ``billing.view`` разумно оставлять доступным на триале (админка
    триал-функций): иначе именно те, кому ручка нужнее всего, получат 403.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(context.clinic_id)
    wallet = await ledger.wallet(context.clinic_id)
    return ClinicSubscriptionResponse.from_result(result).model_copy(
        update={"balance_tiyin": wallet.balance_tiyin, "autopay_enabled": wallet.autopay_enabled}
    )
