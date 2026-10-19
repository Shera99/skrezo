"""Роуты Skrezo: общие шаблоны WhatsApp платформы и их подписи для кабинетов клиник.

Meta отдаёт техническое имя и ``{{1}}``, ``{{2}}``; понятные слова для
интерфейса клиник пишет команда Skrezo (решение 5 от 26.09). Шаблоны самих
клиник здесь не правятся — у них кабинет показывает имя из Meta.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.whatsapp.application.use_cases.set_template_labels import (
    SetTemplateLabelsUseCase,
)
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.modules.whatsapp.presentation.deps import (
    get_set_template_labels_use_case,
    get_whatsapp_repository,
)
from app.modules.whatsapp.presentation.schemas import (
    TemplateLabelsRequest,
    TemplateOut,
    TemplatesResponse,
)
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Общий шаблон не найден (шаблоны клиник здесь не правятся)",
        "content": {
            "application/json": {
                "example": {"error": "template_not_found", "message": "Общий шаблон не найден"}
            }
        },
    },
    422: {
        "description": "Число подписей не совпало с числом переменных шаблона",
        "content": {
            "application/json": {
                "example": {
                    "error": "labels_count_mismatch",
                    "message": "Шаблон ждёт 2 подписей, передано 1",
                    "expected": 2,
                }
            }
        },
    },
}


@router.get(
    "/admin/whatsapp/templates",
    response_model=TemplatesResponse,
    summary="Общие шаблоны WhatsApp платформы",
    response_description="Каталог общих шаблонов с названиями и подписями переменных",
)
async def admin_list_templates(
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    lang: Lang = Depends(get_request_lang),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> TemplatesResponse:
    """Общие шаблоны платформы — те, что видят все клиники (`shared: true`).

    `title` и `variableLabels` — то, что кабинеты клиник покажут вместо
    технического имени и `{{1}}`; без заданных значений отдаются имя из Meta
    и сами плейсхолдеры.
    """
    items = await repository.common_templates()
    return TemplatesResponse(list=[TemplateOut.from_orm_template(t, lang=lang) for t in items])


@router.patch(
    "/admin/whatsapp/templates/{template_id}",
    response_model=TemplateOut,
    summary="Название и подписи переменных общего шаблона",
    response_description="Шаблон после правки",
    responses=_ERRORS,
)
async def admin_set_template_labels(
    template_id: uuid.UUID,
    body: TemplateLabelsRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    lang: Lang = Depends(get_request_lang),
    use_case: SetTemplateLabelsUseCase = Depends(get_set_template_labels_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> TemplateOut:
    """Задать понятное название, подписи и источники значений переменных общего шаблона.

    Подписей — ровно по числу переменных (`{{1}}`, `{{2}}` → две), иначе 422
    `labels_count_mismatch`. `title: null` возвращает имя из Meta; поле
    `variableLabels` не передано — подписи не меняются.
    """
    template = await use_case.execute(
        template_id,
        title=body.title,
        variable_labels=body.variable_labels,
        variable_sources=body.variable_sources,
    )
    await audit.record(
        actor_id=actor,
        action="whatsapp_template.labels",
        entity_type="whatsapp_template",
        entity_id=template.id,
        payload={
            "title": template.title,
            "variableLabels": list(template.variable_labels),
            "variableSources": list(template.variable_sources),
        },
    )
    return TemplateOut.from_orm_template(template, lang=lang)
