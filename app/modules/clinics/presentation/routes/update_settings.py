"""Роут: PATCH /clinic/settings — правка настроек клиники (settings.edit)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clinics.application.use_cases.clinic_settings import (
    UpdateClinicSettingsUseCase,
)
from app.modules.clinics.presentation.deps import get_update_clinic_settings_use_case
from app.modules.clinics.presentation.schemas import (
    ClinicSettingsResponse,
    UpdateClinicSettingsRequest,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Клиника не найдена"},
    422: {
        "description": "Неизвестная таймзона, несуществующий город либо null в NOT NULL-поле",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_timezone",
                    "message": (
                        "Неизвестная таймзона: Asia/Bishkeck. Ожидается зона IANA, "
                        "например Asia/Bishkek"
                    ),
                }
            }
        },
    },
}


@router.patch(
    "/clinic/settings",
    response_model=ClinicSettingsResponse,
    summary="Изменить настройки своей клиники",
    response_description="Обновлённая карточка клиники",
    responses=_ERRORS,
)
async def update_settings(
    body: UpdateClinicSettingsRequest,
    context: ClientContext = Depends(require_permission("settings.edit")),
    lang: Lang = Depends(get_request_lang),
    use_case: UpdateClinicSettingsUseCase = Depends(get_update_clinic_settings_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicSettingsResponse:
    """Изменить название, город, контакты, язык или таймзону клиники.

    **Таймзона** — самое ответственное поле: в ней считаются границы дня,
    свободные слоты и периоды отчётов, поэтому неизвестная зона отвергается
    (422 ``invalid_timezone``), а не откатывается молча на дефолт.

    Тариф (``type``, ``seatsLimit``) и заметки саппорта отсюда не меняются —
    они за платформенной зоной Skrezo.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    view = await use_case.execute(
        context.clinic_id, body.model_dump(exclude_unset=True), lang=lang
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="settings.update",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return ClinicSettingsResponse.from_view(view)
