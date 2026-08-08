"""Роут: GET /clinic/settings — настройки своей клиники (settings.view)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.clinics.application.use_cases.clinic_settings import GetClinicSettingsUseCase
from app.modules.clinics.presentation.deps import get_get_clinic_settings_use_case
from app.modules.clinics.presentation.schemas import ClinicSettingsResponse
from app.shared.auth import ClientContext
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Клиника не найдена"},
}


@router.get(
    "/clinic/settings",
    response_model=ClinicSettingsResponse,
    summary="Настройки своей клиники",
    response_description="Карточка клиники: название, город, контакты, язык и таймзона",
    responses=_ERRORS,
)
async def get_settings(
    context: ClientContext = Depends(require_permission("settings.view")),
    lang: Lang = Depends(get_request_lang),
    use_case: GetClinicSettingsUseCase = Depends(get_get_clinic_settings_use_case),
) -> ClinicSettingsResponse:
    """Показать настройки клиники из токена.

    Тариф (``type``, ``seatsLimit``) отдаётся только для чтения — им управляет
    Skrezo. Заметок саппорта здесь нет: это внутреннее поле платформенной зоны.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    view = await use_case.execute(context.clinic_id, lang=lang)
    return ClinicSettingsResponse.from_view(view)
