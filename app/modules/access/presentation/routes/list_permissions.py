"""Роут: GET /clinic/permissions — каталог прав для конструктора роли."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.access.application.use_cases.manage_roles import ListPermissionsUseCase
from app.modules.access.presentation.deps import (
    get_list_permissions_use_case,
    require_permission,
)
from app.modules.access.presentation.schemas import PermissionItem, PermissionsResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/permissions",
    response_model=PermissionsResponse,
    response_description="Все права каталога (ключ, группа, описание)",
    summary="Каталог прав",
)
async def list_permissions(
    _context: ClientContext = Depends(require_permission("roles.manage")),
    use_case: ListPermissionsUseCase = Depends(get_list_permissions_use_case),
) -> PermissionsResponse:
    """Права, из которых собирается роль — чекбоксы в UI.

    Группировать по ``groupKey`` (schedule, patients, cash, reports и т.д.).
    """
    permissions = await use_case.execute()
    return PermissionsResponse(items=[PermissionItem.from_permission(p) for p in permissions])
