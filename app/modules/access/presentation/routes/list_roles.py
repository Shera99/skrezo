"""Роут: GET /clinic/roles — роли клиники (право roles.manage)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.application.use_cases.manage_roles import ListRolesUseCase
from app.modules.access.presentation.deps import get_list_roles_use_case, require_permission
from app.modules.access.presentation.schemas import RoleItem, RolesResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/roles",
    response_model=RolesResponse,
    response_description="Роли клиники с правами и числом сотрудников",
    summary="Роли клиники",
)
async def list_roles(
    context: ClientContext = Depends(require_permission("roles.manage")),
    use_case: ListRolesUseCase = Depends(get_list_roles_use_case),
) -> RolesResponse:
    """Роли клиники: владелец и системные — первыми, затем кастомные.

    Флаги ``editablePermissions`` и ``deletable`` подсказывают UI, что можно
    делать с ролью: у владельца доступ всегда полный, системные роли не
    переименовываются и не удаляются, роль с сотрудниками удалить нельзя.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    rows = await use_case.execute(context.clinic_id)
    return RolesResponse(items=[RoleItem.from_row(r) for r in rows])
