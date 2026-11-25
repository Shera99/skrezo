"""Роут: GET /clinic/notifications/prefs — действующие настройки уведомлений."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import get_clinic_context
from app.modules.push.application.use_cases.get_prefs import GetPrefsUseCase
from app.modules.push.presentation.deps import get_prefs_use_case
from app.modules.push.presentation.schemas import PrefsResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "В токене не выбрана клиника",
        "content": {
            "application/json": {
                "example": {"error": "clinic_not_selected", "message": "Не выбрана клиника"}
            }
        },
    },
}


@router.get(
    "/clinic/notifications/prefs",
    response_model=PrefsResponse,
    summary="Настройки уведомлений",
    response_description="Действующие настройки своего членства",
    responses=_ERRORS,
)
async def get_prefs(
    context: ClientContext = Depends(get_clinic_context),
    use_case: GetPrefsUseCase = Depends(get_prefs_use_case),
) -> PrefsResponse:
    """Отключённые типы пушей и тихие часы своего членства.

    Источник истины для экрана настроек: без него приложение держало копию у
    себя, и тумблер на втором устройстве показывал неправду. Настройки свои —
    пара (аккаунт, клиника) из токена. Пока их ни разу не правили, возвращается
    дефолт: ничего не отключено, тихих часов нет.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    pref = await use_case.execute(context.account_id, context.clinic_id)
    if pref is None:
        return PrefsResponse(disabled=[])
    return PrefsResponse.from_pref(pref)
