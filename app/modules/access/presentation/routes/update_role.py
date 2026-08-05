"""Роут: PATCH /clinic/roles/{id} — правка роли (право roles.manage)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.application.use_cases.manage_roles import UpdateRoleUseCase
from app.modules.access.presentation.deps import get_update_role_use_case, require_permission
from app.modules.access.presentation.schemas import RoleItem, UpdateRoleRequest
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
        "description": "Роль владельца неизменяема, системную нельзя переименовать "
        "или название занято",
        "content": {
            "application/json": {
                "example": {
                    "error": "owner_role_immutable",
                    "message": "Роль владельца не редактируется — у неё всегда полный доступ",
                }
            }
        },
    },
    422: {
        "description": "Неизвестный ключ права",
        "content": {
            "application/json": {
                "example": {
                    "error": "permission_not_found",
                    "message": "Право не найдено: cash.bad",
                }
            }
        },
    },
}


@router.patch(
    "/clinic/roles/{role_id}",
    response_model=RoleItem,
    response_description="Обновлённая роль",
    summary="Изменить роль (название и/или права)",
    responses=_ERRORS,
)
async def update_role(
    role_id: uuid.UUID,
    body: UpdateRoleRequest,
    context: ClientContext = Depends(require_permission("roles.manage")),
    use_case: UpdateRoleUseCase = Depends(get_update_role_use_case),
) -> RoleItem:
    """Переименовать роль и/или заменить её набор прав.

    ``permissions`` заменяет набор **целиком** (не добавляет). Права системных
    ролей настраиваются пер-клиника — это штатный сценарий; переименовать их
    нельзя (название приходит из каталога по ``key``). Роль владельца не
    редактируется вовсе. Изменение применяется сразу — кеш прав сбрасывается.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    row = await use_case.execute(
        context.clinic_id,
        role_id,
        name=body.name,
        permission_keys=body.permissions,
        max_discount_percent=body.max_discount_percent,
    )
    return RoleItem.from_row(row)
