"""Роуты кабинета: смена пакета клиникой и заявка на условия сверх тарифов (billing.manage)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.application.use_cases.change_package import ChangePackageUseCase
from app.modules.payments.application.use_cases.plan_options import GetPlanOptionsUseCase
from app.modules.payments.application.use_cases.plan_requests import SubmitPlanRequestUseCase
from app.modules.payments.presentation.deps import (
    get_change_package_use_case,
    get_plan_options_use_case,
    get_submit_plan_request_use_case,
)
from app.modules.payments.presentation.schemas import (
    ChangePackageRequest,
    PackageChangeOut,
    PlanRequestCreate,
    PlanRequestOut,
    TariffPackagesOut,
)
from app.shared.auth import ClientContext

router = APIRouter()


def _err(code: str, message: str, description: str, **extra: Any) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": {"error": code, "message": message, **extra}}},
    }


@router.put(
    "/clinic/subscription/package",
    response_model=PackageChangeOut,
    summary="Перейти на другой пакет тарифа",
    response_description="Итог перехода и тариф после него",
    responses={
        402: _err(
            "insufficient_balance",
            "На балансе не хватает на доплату за переход — пополните баланс",
            "Не хватает на доплату за остаток периода; ничего не изменилось",
            balanceTiyin=200000,
            amountTiyin=600000,
            shortfallTiyin=400000,
        ),
        409: _err(
            "seats_in_use",
            "В пакете «Старт» мест 3, а сотрудников 4 — сначала отключите лишних",
            "Сотрудников больше мест пакета (`seatsUsed`, `upTo`); уже на пакете "
            "(`package_already_current`); спецплан (`custom_plan_active`); соло "
            "(`plan_change_not_available`); подписка закрыта Skrezo "
            "(`subscription_blocked`, `subscription_cancelled`)",
            seatsUsed=4,
            upTo=3,
        ),
        422: _err(
            "package_not_found",
            "Такого пакета нет в действующем тарифе",
            "Нет пакета с таким `upTo`",
        ),
    },
)
async def change_package(
    body: ChangePackageRequest,
    context: ClientContext = Depends(require_permission("billing.manage")),
    use_case: ChangePackageUseCase = Depends(get_change_package_use_case),
    options: GetPlanOptionsUseCase = Depends(get_plan_options_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PackageChangeOut:
    """Сменить пакет самостоятельно — только между публичными пакетами тарифа.

    **Дороже** — сразу: лимит мест растёт, разница цен за остаток оплаченного
    периода списывается с баланса (месяц — 30 дней). Денег не хватает — 402 с
    суммами, ничего не меняется: пополните баланс и повторите. На триале и без
    оплаченного периода — без доплаты. **Дешевле** — сразу и без возврата, если
    сотрудников не больше мест нового пакета. Новая цена — со следующей оплаты
    и автопродления. Больше мест, чем в последнем пакете, — через заявку.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    change = await use_case.execute(context.clinic_id, context.account_id, body.up_to)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="subscription.package_changed",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={
            "from": change.from_package,
            "to": change.to_package,
            "chargedTiyin": change.charged_tiyin,
        },
    )
    plan = TariffPackagesOut.from_options(await options.execute(context.clinic_id))
    return PackageChangeOut.build(change, plan)


@router.post(
    "/clinic/subscription/plan-request",
    response_model=PlanRequestOut,
    summary="Заявка на условия сверх тарифов",
    response_description="Открытая заявка клиники",
)
async def request_plan(
    body: PlanRequestCreate,
    context: ClientContext = Depends(require_permission("billing.manage")),
    use_case: SubmitPlanRequestUseCase = Depends(get_submit_plan_request_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PlanRequestOut:
    """Нужно больше мест, чем в самом большом пакете, или другие условия.

    Команда Skrezo получает сообщение в чат и заявку в очереди админки, итог
    разговора — спецплан клиники. Открытая заявка одна: повторная отправка
    обновляет её. Закрывается решением Skrezo или сама, когда ставят спецплан.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    request = await use_case.execute(
        context.clinic_id, context.account_id, seats=body.seats, comment=body.comment
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="subscription.plan_requested",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={"seats": body.seats},
    )
    return PlanRequestOut.from_model(request)
