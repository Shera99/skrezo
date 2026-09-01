"""Роут: DELETE /clinic/schedule/doctors/{id}/hours — вернуть врача на часы клиники."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.schedule.application.use_cases.clear_doctor_hours import ClearDoctorHoursUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_manage,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_clear_doctor_hours_use_case
from app.modules.schedule.presentation.schemas import DoctorHoursClearedResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право manage_own: попытка сбросить чужой график",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_own_schedule",
                    "message": "Доступно только своё расписание",
                }
            }
        },
    },
    404: {
        "description": "Врач не найден в этой клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "doctor_not_found",
                    "message": "Врач не найден в этой клинике",
                }
            }
        },
    },
}


@router.delete(
    "/clinic/schedule/doctors/{membership_id}/hours",
    response_model=DoctorHoursClearedResponse,
    response_description="График снят; был ли он до этого",
    summary="Снять индивидуальный график врача",
    responses=_ERRORS,
)
async def clear_doctor_hours(
    membership_id: uuid.UUID,
    scope: ScheduleScope = Depends(require_schedule_manage),
    use_case: ClearDoctorHoursUseCase = Depends(get_clear_doctor_hours_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> DoctorHoursClearedResponse:
    """Вернуть врача на часы клиники.

    ``PUT`` умеет только перезаписать все 7 дней, и «как у клиники» через него не
    выражается: сохранённая копия часов клиники не поедет за их изменением.
    Удаление строк — и есть «работает как клиника»: с этого момента окно врача
    считается от часов клиники и меняется вместе с ними.

    Отсутствия врача (``time-off``) при этом не трогаются — это про другое.
    Уже созданные приёмы не пересчитываются, как и при ``PUT``.

    Повторный вызов на враче без графика — не ошибка: `had: false` и 200.
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    had = await use_case.execute(scope.clinic_id, membership_id)
    await audit.record_clinic(
        actor_id=scope.context.account_id,
        clinic_id=scope.clinic_id,
        action="schedule.doctor_hours_cleared",
        entity_type="membership",
        entity_id=membership_id,
        payload={"had": had},
    )
    return DoctorHoursClearedResponse(had=had)
