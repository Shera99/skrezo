"""Роут: GET /clinic/services — прайс клиники (price.view)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.services.application.use_cases.list_services import ListServicesUseCase
from app.modules.services.presentation.deps import get_list_services_use_case
from app.modules.services.presentation.schemas import ServiceOut
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/services",
    response_model=list[ServiceOut],
    summary="Прайс клиники",
)
async def list_services(
    active_only: bool = Query(default=False, alias="activeOnly"),
    context: ClientContext = Depends(require_permission("price.view")),
    use_case: ListServicesUseCase = Depends(get_list_services_use_case),
) -> list[ServiceOut]:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    services = await use_case.execute(context.clinic_id, active_only=active_only)
    return [ServiceOut.from_orm_service(s, doctor_ids) for s, doctor_ids in services]
