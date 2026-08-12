"""Роут: GET /admin/clients/{id}/staff — сотрудники клиники (админ-сайд)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.clients.application.use_cases.list_staff import ListStaffUseCase
from app.modules.clients.presentation.deps import get_list_staff_use_case
from app.modules.clients.presentation.schemas import StaffMember
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиент не найден",
        "content": {"application/json": {"example": {"error": "client_not_found"}}},
    },
}


@router.get(
    "/admin/clients/{clinic_id}/staff",
    response_model=list[StaffMember],
    summary="Сотрудники клиники (админ-сайд)",
    response_description="Владелец и врачи клиники, каждый с флагом active",
    responses=_NOT_FOUND,
)
async def list_staff(
    clinic_id: uuid.UUID,
    active: bool | None = Query(
        default=None,
        description=(
            "Фильтр по статусу: `true` — работающие, `false` — исключённые, "
            "пусто — все (по умолчанию)"
        ),
    ),
    lang: Lang = Depends(get_request_lang),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListStaffUseCase = Depends(get_list_staff_use_case),
) -> list[StaffMember]:
    """Список сотрудников клиники: владелец (`role=admin`) и врачи (`role=doctor`).

    Исключённые из клиники остаются в выдаче с `active: false` — на их членстве
    висят приёмы и платежи, и админке нужно видеть, кто их вёл. Пагинации нет:
    число сотрудников ограничено местами тарифа.

    Сортировка та же, что в карточке клиента: владелец первым, дальше по дате
    добавления.
    """
    rows = await use_case.execute(clinic_id, lang=lang, active=active)
    return [StaffMember.from_row(r) for r in rows]
