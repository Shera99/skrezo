"""Роут: GET /clinic/subscription/packages — тариф клиники, пакеты и варианты перехода."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.payments.application.use_cases.plan_options import GetPlanOptionsUseCase
from app.modules.payments.presentation.deps import get_plan_options_use_case
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
    use_case: GetPlanOptionsUseCase = Depends(get_plan_options_use_case),
) -> TariffPackagesOut:
    """Тариф клиники для экрана «Подписка»: из чего складывается цена и куда можно перейти.

    Действующая версия тарифа для типа клиники: пакеты с отметкой текущего,
    доплата за место сверх последнего, у соло — фиксированная цена. Число мест и
    цена — по тому же правилу, что и оплата.

    Для смены тарифа у каждого пакета — `available` и `changeCostTiyin` (сколько
    спишется с баланса сейчас, если перейти), у тарифа — `canChange` и причина
    закрытой смены. Спецплан Skrezo (`customPlan`) заменяет пакеты: цена и места
    из него, переход закрыт. Мест больше последнего пакета кабинет не продаёт —
    для этого заявка (`POST /clinic/subscription/plan-request`, `openRequest`).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return TariffPackagesOut.from_options(await use_case.execute(context.clinic_id))
