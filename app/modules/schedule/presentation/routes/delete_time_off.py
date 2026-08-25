"""Роут: DELETE /clinic/schedule/doctors/{id}/time-off/{id} — снять отсутствие (manage_all/own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.push.application.emitter import PushEmitter
from app.modules.push.presentation.deps import get_push_emitter
from app.modules.schedule.application.use_cases.delete_time_off import DeleteTimeOffUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_manage,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_delete_time_off_use_case
from app.modules.schedule.presentation.schemas import TimeOffDeletedResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право manage_own: попытка снять отсутствие чужому врачу",
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
        "description": "Отсутствие не найдено (чужая клиника/врач или неверный id)",
        "content": {
            "application/json": {
                "example": {
                    "error": "time_off_not_found",
                    "message": "Отсутствие не найдено",
                }
            }
        },
    },
}


@router.delete(
    "/clinic/schedule/doctors/{membership_id}/time-off/{time_off_id}",
    response_model=TimeOffDeletedResponse,
    response_description="Отсутствие удалено",
    summary="Снять отсутствие врача",
    responses=_ERRORS,
)
async def delete_time_off(
    membership_id: uuid.UUID,
    time_off_id: uuid.UUID,
    scope: ScheduleScope = Depends(require_schedule_manage),
    use_case: DeleteTimeOffUseCase = Depends(get_delete_time_off_use_case),
    audit: AuditService = Depends(get_audit_service),
    emitter: PushEmitter = Depends(get_push_emitter),
) -> TimeOffDeletedResponse:
    """Удалить отсутствие врача по id.

    Удаление скоупится клиникой и врачом из пути — чужую запись не снять (404).
    С правом ``schedule.manage_all`` можно снимать отсутствия любого врача, с
    правом ``schedule.manage_own`` — только свои (чужой ``membershipId`` → 403).
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    await use_case.execute(scope.clinic_id, membership_id, time_off_id)
    await audit.record_clinic(
        actor_id=scope.context.account_id,
        clinic_id=scope.clinic_id,
        action="schedule.time_off_remove",
        entity_type="membership",
        entity_id=membership_id,
        payload={"timeOffId": str(time_off_id)},
    )
    # Отсутствие снято — тихо просим приложения перечитать день (sync.day).
    await emitter.emit_sync_day(scope.clinic_id)
    return TimeOffDeletedResponse(id=time_off_id)
