"""Роут: POST /clinic/roles — создать кастомную роль (право roles.manage)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.application.use_cases.manage_roles import CreateRoleUseCase
from app.modules.access.presentation.deps import get_create_role_use_case, require_permission
from app.modules.access.presentation.schemas import CreateRoleRequest, RoleItem
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Роль с таким названием уже есть",
        "content": {
            "application/json": {
                "example": {
                    "error": "role_name_taken",
                    "message": "Роль с таким названием уже есть",
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


@router.post(
    "/clinic/roles",
    response_model=RoleItem,
    status_code=status.HTTP_201_CREATED,
    response_description="Созданная роль с набором прав",
    summary="Создать роль",
    responses=_ERRORS,
)
async def create_role(
    body: CreateRoleRequest,
    context: ClientContext = Depends(require_permission("roles.manage")),
    use_case: CreateRoleUseCase = Depends(get_create_role_use_case),
) -> RoleItem:
    """Своя роль клиники с произвольным набором прав из каталога.

    Название уникально в пределах клиники (без учёта регистра). Неизвестный ключ
    права отвергает запрос целиком — роль не создаётся.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    row = await use_case.execute(
        context.clinic_id, body.name, body.permissions, body.max_discount_percent
    )
    return RoleItem.from_row(row)
