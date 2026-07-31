"""Роут: DELETE /clinic/roles/{id} — удалить кастомную роль (право roles.manage)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.application.use_cases.manage_roles import DeleteRoleUseCase
from app.modules.access.presentation.deps import get_delete_role_use_case, require_permission
from app.modules.access.presentation.schemas import RoleDeletedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Роль не найдена в этой клинике",
        "content": {
            "application/json": {
                "example": {"error": "role_not_found", "message": "Роль не найдена"}
            }
        },
    },
    409: {
        "description": "Системная роль, роль владельца или на роли есть сотрудники",
        "content": {
            "application/json": {
                "example": {
                    "error": "role_has_members",
                    "message": "На роли есть сотрудники — сначала переведите их на другую роль",
                    "membersCount": 2,
                }
            }
        },
    },
}


@router.delete(
    "/clinic/roles/{role_id}",
    response_model=RoleDeletedResponse,
    response_description="Роль удалена",
    summary="Удалить роль",
    responses=_ERRORS,
)
async def delete_role(
    role_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("roles.manage")),
    use_case: DeleteRoleUseCase = Depends(get_delete_role_use_case),
) -> RoleDeletedResponse:
    """Удалить кастомную роль клиники.

    Системные роли и роль владельца не удаляются. Роль с активными сотрудниками
    тоже — сначала переведите их на другую роль (иначе членства осиротеют).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    await use_case.execute(context.clinic_id, role_id)
    return RoleDeletedResponse(id=role_id)
