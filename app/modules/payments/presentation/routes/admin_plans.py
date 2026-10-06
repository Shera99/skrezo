"""Роуты Skrezo: спецплан клиники и очередь заявок на условия сверх тарифов."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.application.use_cases.custom_plan import (
    RemoveCustomPlanUseCase,
    SetCustomPlanUseCase,
)
from app.modules.payments.application.use_cases.plan_requests import (
    ListPlanRequestsUseCase,
    ResolvePlanRequestUseCase,
)
from app.modules.payments.presentation.deps import (
    get_list_plan_requests_use_case,
    get_remove_custom_plan_use_case,
    get_resolve_plan_request_use_case,
    get_set_custom_plan_use_case,
)
from app.modules.payments.presentation.schemas import (
    AdminCustomPlanOut,
    AdminPlanRequestOut,
    AdminPlanRequestsResponse,
    CustomPlanRequest,
    ResolvePlanRequest,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


def _err(code: str, message: str, description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": {"error": code, "message": message}}},
    }


@router.put(
    "/admin/clients/{clinic_id}/custom-plan",
    response_model=AdminCustomPlanOut,
    summary="Поставить клинике спецплан",
    response_description="Спецплан после сохранения",
    responses={
        404: _err("client_not_found", "Клиент не найден", "Клиент не найден"),
        409: _err(
            "seats_in_use",
            "Сотрудников уже 12 — мест в плане должно быть не меньше",
            "Мест меньше, чем сотрудников (`seatsUsed`); соло (`custom_plan_clinic_only`)",
        ),
    },
)
async def set_custom_plan(
    clinic_id: uuid.UUID,
    body: CustomPlanRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: SetCustomPlanUseCase = Depends(get_set_custom_plan_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AdminCustomPlanOut:
    """Индивидуальные условия клиники: своё число мест и своя цена в месяц.

    Виден только этой клинике (`customPlan` в её тарифе) и заменяет пакеты во
    всём: оплата, списание с баланса, автопродление. Места — сразу (лимит
    сотрудников), цена — со следующей оплаты; доплату или возврат за текущий
    период, если договорились, проведите операцией журнала. Открытая заявка
    клиники закрывается как выполненная. Повторный PUT заменяет план.
    """
    plan = await use_case.execute(
        clinic_id,
        actor,
        title=body.title,
        seats=body.seats,
        price_tiyin=body.price_tiyin,
        note=body.note,
    )
    await audit.record(
        actor_id=actor,
        action="client.custom_plan_set",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"title": body.title, "seats": body.seats, "priceTiyin": body.price_tiyin},
    )
    return AdminCustomPlanOut.from_model(plan)


@router.delete(
    "/admin/clients/{clinic_id}/custom-plan",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Снять спецплан",
    response_description="Спецплан снят; цена снова по пакетам",
    responses={
        404: _err("custom_plan_not_found", "Спецплана у клиента нет", "Спецплана нет"),
    },
)
async def remove_custom_plan(
    clinic_id: uuid.UUID,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: RemoveCustomPlanUseCase = Depends(get_remove_custom_plan_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """Вернуть клинику к публичным пакетам: лимит мест прежний, цена — по пакетам."""
    plan = await use_case.execute(clinic_id)
    await audit.record(
        actor_id=actor,
        action="client.custom_plan_removed",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"title": plan.title, "seats": plan.seats, "priceTiyin": plan.price_tiyin},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/admin/plan-requests",
    response_model=AdminPlanRequestsResponse,
    summary="Заявки клиник на условия сверх тарифов",
    response_description="Открытые сверху, внутри — свежие первыми",
)
async def list_plan_requests(
    status_: Literal["new", "done", "declined"] | None = Query(
        default=None, alias="status", description="Фильтр по статусу; без него — все"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListPlanRequestsUseCase = Depends(get_list_plan_requests_use_case),
) -> AdminPlanRequestsResponse:
    """Очередь заявок «нужно больше мест»: кто, сколько мест, комментарий, статус."""
    rows, total = await use_case.execute(status=status_, page=page, page_size=page_size)
    return AdminPlanRequestsResponse(
        list=[AdminPlanRequestOut.from_row(r) for r in rows],
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.patch(
    "/admin/plan-requests/{request_id}",
    response_model=AdminPlanRequestOut,
    summary="Закрыть заявку клиники",
    response_description="Заявка после решения",
    responses={
        404: _err("plan_request_not_found", "Заявка не найдена", "Заявка не найдена"),
        409: _err("plan_request_closed", "Заявка уже закрыта", "Заявка уже закрыта"),
    },
)
async def resolve_plan_request(
    request_id: uuid.UUID,
    body: ResolvePlanRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ResolvePlanRequestUseCase = Depends(get_resolve_plan_request_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AdminPlanRequestOut:
    """`done` — договорились, `declined` — отказ. Спецплан закрывает заявку сам."""
    row = await use_case.execute(request_id, actor, status=body.status, note=body.note)
    await audit.record(
        actor_id=actor,
        action="plan_request.resolved",
        entity_type="clinic",
        entity_id=row.request.clinic_id,
        payload={"requestId": str(request_id), "status": body.status},
    )
    return AdminPlanRequestOut.from_row(row)
