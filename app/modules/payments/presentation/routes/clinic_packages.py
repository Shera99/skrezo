"""Роут: GET /clinic/subscription/packages — тариф клиники с пакетами (billing.view)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.payments.application.services.pricing import SubscriptionPricing
from app.modules.payments.presentation.deps import get_pricing
from app.modules.payments.presentation.schemas import TariffPackagesOut
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Тариф для типа клиники ещё не заведён",
        "content": {
            "application/json": {
                "example": {
                    "error": "tariff_not_configured",
                    "message": "Тариф ещё не настроен — напишите в Skrezo",
                }
            }
        },
    },
}


@router.get(
    "/clinic/subscription/packages",
    response_model=TariffPackagesOut,
    summary="Тариф клиники: пакеты и текущая цена",
    response_description="Пакеты по возрастанию мест, доплата за место и текущий пакет",
    responses=_ERRORS,
)
async def subscription_packages(
    context: ClientContext = Depends(require_permission("billing.view")),
    pricing: SubscriptionPricing = Depends(get_pricing),
) -> TariffPackagesOut:
    """Из чего складывается цена подписки клиники — для экрана «Подписка».

    Раньше пакеты отдавала только админка (`/admin/tariffs`). Здесь — действующая
    версия тарифа для типа этой клиники, только на чтение: у клиники — пакеты с
    отметкой текущего и доплата за место сверх последнего, у соло — фиксированная
    цена. Число мест и цена — по тому же правилу, что и оплата.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return TariffPackagesOut.from_card(await pricing.card(context.clinic_id))
