"""Роут: POST /clinic/demo/reset — собрать демо-день заново."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.demo.application.services.demo_reset_service import DemoResetService
from app.modules.demo.presentation.deps import get_demo_reset_service
from app.modules.demo.presentation.schemas import DemoResetResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Клиника не демонстрационная либо нет права `schedule.manage_all`",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_demo_clinic",
                    "message": "Сброс есть только у демо-клиники",
                }
            }
        },
    },
    422: {
        "description": "Собрать день не из чего: нет врача или услуги с ценой",
        "content": {
            "application/json": {
                "example": {
                    "error": "demo_not_ready",
                    "message": (
                        "Чтобы собрать день, клинике нужны хотя бы один врач и одна услуга с ценой"
                    ),
                    "doctors": 0,
                    "services": 4,
                }
            }
        },
    },
}


@router.post(
    "/clinic/demo/reset",
    response_model=DemoResetResponse,
    summary="Сбросить демо-клинику",
    response_description="Что удалено и что собрано",
    responses=_ERRORS,
)
async def reset_demo(
    context: ClientContext = Depends(require_permission("schedule.manage_all")),
    service: DemoResetService = Depends(get_demo_reset_service),
    audit: AuditService = Depends(get_audit_service),
) -> DemoResetResponse:
    """Убрать показанное и собрать сегодняшний день от текущего времени.

    Удаляются пациенты, записи, оплаты и переписки, появившиеся после включения
    признака демо (в том числе собранные прошлым сбросом). Данные до включения не
    трогаются. Карточка пациента, который лечится и в другой клинике, остаётся.

    Собирается: два завершённых приёма (оплаченный и с долгом), неявка час
    назад, пациент в кресле и три записи впереди, одна из них подтверждена, — на
    двух-трёх врачей клиники. Завтрашний день свободен. Тот же сброс сам идёт
    каждый день в 06:00 по времени клиники.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await service.reset(context.clinic_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="demo.reset",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={"appointments": result.purged.appointments, "patients": result.purged.patients},
    )
    return DemoResetResponse.from_result(result)
