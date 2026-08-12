"""Роут: DELETE /admin/clients/{id}/doctors/{docId} — удалить врача (админ-сайд).

v1 — деактивация: членство остаётся снапшотом (на нём висят приёмы и платежи),
доступ к клинике закрывается сразу, аккаунт сохраняется. Владельца удалить нельзя.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.presentation.schemas import DoctorRemovedResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.staff.application.use_cases.remove_staff import RemoveStaffUseCase
from app.modules.staff.presentation.deps import get_remove_staff_use_case

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Владельца нельзя удалить (cannot_remove_owner)"},
    404: {"description": "Сотрудник не найден"},
}


@router.delete(
    "/admin/clients/{clinic_id}/doctors/{doctor_id}",
    response_model=DoctorRemovedResponse,
    summary="Удалить врача (админ-сайд)",
    response_description="Режим удаления и что стало с доступом",
    responses=_ERRORS,
)
async def remove_doctor(
    clinic_id: uuid.UUID,
    doctor_id: uuid.UUID,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: RemoveStaffUseCase = Depends(get_remove_staff_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> DoctorRemovedResponse:
    """Исключить врача из клиники.

    Членство деактивируется, а не удаляется: на нём висят приёмы и платежи.
    Доступ к клинике закрывается **сразу** — уже выданные токены получают
    `403 membership_revoked` на её эндпоинтах, даже если срок их жизни не вышел.

    Аккаунт врача при этом **сохраняется**: он глобальный, и тот же человек может
    работать в другой клинике. Вход по коду у него остаётся, но приводит к токену
    без доступа к этой клинике.
    """
    result = await use_case.execute(clinic_id, doctor_id)
    await audit.record(
        actor_id=actor,
        action="clinic_staff.removed",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"membership_id": str(doctor_id), "mode": result.mode},
    )
    return DoctorRemovedResponse(mode=result.mode)
