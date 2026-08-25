"""Роут: PUT /clinic/notifications/prefs — настройки уведомлений своего членства."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import get_clinic_context
from app.modules.push.application.use_cases.update_prefs import UpdatePrefsUseCase
from app.modules.push.presentation.deps import get_update_prefs_use_case
from app.modules.push.presentation.schemas import PrefsResponse, UpdatePrefsRequest
from app.shared.auth import ClientContext

router = APIRouter()


@router.put(
    "/clinic/notifications/prefs",
    response_model=PrefsResponse,
    summary="Изменить настройки уведомлений",
    response_description="Действующие настройки после правки",
)
async def update_prefs(
    body: UpdatePrefsRequest,
    context: ClientContext = Depends(get_clinic_context),
    use_case: UpdatePrefsUseCase = Depends(get_update_prefs_use_case),
) -> PrefsResponse:
    """Отключить типы пушей и/или задать тихие часы для своего членства.

    Настройки свои: привязаны к паре (аккаунт, клиника) из токена, а не к
    произвольному id в теле запроса. Строка префов заводится в БД при первой
    правке — до неё действует дефолт (ничего не отключено, тихих часов нет).
    Группу `subscription.*` этим эндпоинтом не отключить: фильтр применяется
    на отправке пуша, а не здесь.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    pref = await use_case.execute(
        context.account_id, context.clinic_id, body.model_dump(exclude_unset=True)
    )
    return PrefsResponse.from_pref(pref)
