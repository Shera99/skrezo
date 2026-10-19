"""Роуты шаблонов WhatsApp: список, синхронизация из Meta, подписи, предзаполнение, отправка."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.config import settings
from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.whatsapp.application.use_cases.prefill_template import PrefillTemplateUseCase
from app.modules.whatsapp.application.use_cases.send_template import SendTemplateUseCase
from app.modules.whatsapp.application.use_cases.set_template_labels import (
    SetTemplateLabelsUseCase,
)
from app.modules.whatsapp.application.use_cases.sync_templates import SyncTemplatesUseCase
from app.modules.whatsapp.domain.template_sources import VariableSource, source_label
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.modules.whatsapp.presentation.deps import (
    get_prefill_template_use_case,
    get_send_template_use_case,
    get_set_template_labels_use_case,
    get_sync_templates_use_case,
    get_whatsapp_repository,
)
from app.modules.whatsapp.presentation.schemas import (
    MessageOut,
    SendTemplateRequest,
    TemplateLabelsRequest,
    TemplateOut,
    TemplatePrefillResponse,
    TemplatesResponse,
    VariableSourceOut,
    VariableSourcesResponse,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_SYNC_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {"description": "WhatsApp не подключён к клинике"},
    422: {
        "description": "У привязки не указан WABA",
        "content": {"application/json": {"example": {"error": "waba_id_missing"}}},
    },
    502: {
        "description": "Graph API не ответил (или канал без кредов Meta)",
        "content": {"application/json": {"example": {"error": "wa_templates_unavailable"}}},
    },
}

_SEND_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Диалог или шаблон не найден"},
    409: {"description": "WhatsApp не подключён к клинике"},
    422: {
        "description": (
            "Шаблон не одобрен (`template_not_approved`), служебный шаблон Meta "
            "(`template_service`) либо число переменных не совпало "
            "(`template_variables_mismatch`)"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "template_variables_mismatch",
                    "message": "Шаблон ждёт 2 переменных, передано 0",
                    "expected": 2,
                }
            }
        },
    },
    502: {"description": "Graph API отказал или не ответил"},
}

_LABELS_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Общий шаблон платформы — его подписывает Skrezo",
        "content": {"application/json": {"example": {"error": "template_shared"}}},
    },
    404: {"description": "Шаблон не найден (`template_not_found`)"},
    422: {
        "description": (
            "Подписей или источников не столько, сколько переменных "
            "(`labels_count_mismatch` / `sources_count_mismatch`)"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "sources_count_mismatch",
                    "message": "Шаблон ждёт 2 источников, передано 1",
                    "expected": 2,
                }
            }
        },
    },
}


@router.get(
    "/clinic/whatsapp/templates",
    response_model=TemplatesResponse,
    summary="Шаблоны WhatsApp клиники",
    response_description="Свои шаблоны + общие платформы",
)
async def list_templates(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    lang: Lang = Depends(get_request_lang),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> TemplatesResponse:
    """Локальный каталог шаблонов — то, что интерфейс показывает вместо поля
    ввода при закрытом окне. Отправлять можно только `status: approved`;
    у остальных `unavailableReason` объясняет почему («На проверке в Meta с
    20 сентября»). `title` и `variableLabels` — понятные слова вместо
    технического имени и `{{1}}`: у общих шаблонов их задаёт Skrezo, у шаблонов
    клиники показывается имя из Meta. Служебные шаблоны Meta
    (`WA_SERVICE_TEMPLATE_NAMES`, тестовый `hello_world`) в каталоге не
    показываются."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await repository.list_templates(
        context.clinic_id, exclude_names=settings.wa_service_template_names
    )
    tz = await repository.clinic_tz(context.clinic_id)
    return TemplatesResponse(
        list=[TemplateOut.from_orm_template(t, lang=lang, tz=tz) for t in items]
    )


@router.post(
    "/clinic/whatsapp/templates/sync",
    response_model=TemplatesResponse,
    summary="Синхронизировать шаблоны из кабинета Meta",
    response_description="Каталог после сверки",
    responses=_SYNC_ERRORS,
)
async def sync_templates(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    lang: Lang = Depends(get_request_lang),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    use_case: SyncTemplatesUseCase = Depends(get_sync_templates_use_case),
) -> TemplatesResponse:
    """Полная сверка с кабинетом Meta по WABA привязки: новые появляются,
    удалённые пропадают, статусы обновляются (момент смены статуса
    запоминается — из него складывается `unavailableReason`). Общие шаблоны
    платформы сверка не трогает. Дёргать после правок шаблонов в кабинете —
    расписания нет.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await use_case.execute(context.clinic_id)
    tz = await repository.clinic_tz(context.clinic_id)
    return TemplatesResponse(
        list=[TemplateOut.from_orm_template(t, lang=lang, tz=tz) for t in items]
    )


@router.post(
    "/clinic/conversations/{conversation_id}/templates",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageOut,
    summary="Отправить шаблон в диалог",
    response_description="Отправленное сообщение (тело — превью с подстановкой)",
    responses=_SEND_ERRORS,
)
async def send_template(
    conversation_id: uuid.UUID,
    body: SendTemplateRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: SendTemplateUseCase = Depends(get_send_template_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> MessageOut:
    """Единственный способ написать пациенту в **закрытое** 24-часовое окно.

    Шаблон обязан быть `approved`, переменные — по числу плейсхолдеров тела.
    В открытое окно шаблон тоже отправится — запрета нет.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    message = await use_case.execute(
        context.clinic_id, conversation_id, body.template_id, body.variables
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="conversation.template",
        entity_type="conversation",
        entity_id=conversation_id,
        payload={"templateId": str(body.template_id), "messageId": message.id},
    )
    return MessageOut.from_orm_message(message)


@router.get(
    "/clinic/whatsapp/templates/variable-sources",
    response_model=VariableSourcesResponse,
    summary="Источники значений переменных шаблона",
    response_description="Пункты выбора с подписями на языке запроса",
)
async def list_variable_sources(
    _: ClientContext = Depends(require_permission("conversations.manage")),
    lang: Lang = Depends(get_request_lang),
) -> VariableSourcesResponse:
    """Откуда кабинет может брать значение переменной шаблона: имя пациента,
    данные клиники, ближайшая запись, долг или `manual` (вводит сотрудник).
    Выбор сохраняется ручкой `PATCH /clinic/whatsapp/templates/{templateId}`."""
    return VariableSourcesResponse(
        list=[VariableSourceOut(key=s, label=source_label(s, lang)) for s in VariableSource]
    )


@router.patch(
    "/clinic/whatsapp/templates/{template_id}",
    response_model=TemplateOut,
    summary="Название, подписи и источники переменных своего шаблона",
    response_description="Шаблон после правки",
    responses=_LABELS_ERRORS,
)
async def set_template_labels(
    template_id: uuid.UUID,
    body: TemplateLabelsRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    lang: Lang = Depends(get_request_lang),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    use_case: SetTemplateLabelsUseCase = Depends(get_set_template_labels_use_case),
) -> TemplateOut:
    """Клиника объясняет свой шаблон: понятное название, подписи переменных и
    откуда брать значение каждой (`variableSources`) — тогда кабинет заполняет
    их сам (`GET …/templates/{templateId}/prefill`). Списков ровно по числу
    переменных; поле не передано — не меняется; `title: null` возвращает имя
    из Meta. Синхронизация с Meta подписи и источники не стирает. Общий шаблон
    платформы — 403 `template_shared`."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    template = await use_case.execute(
        template_id,
        title=body.title,
        variable_labels=body.variable_labels,
        variable_sources=body.variable_sources,
        clinic_id=context.clinic_id,
    )
    tz = await repository.clinic_tz(context.clinic_id)
    return TemplateOut.from_orm_template(template, lang=lang, tz=tz)


@router.get(
    "/clinic/conversations/{conversation_id}/templates/{template_id}/prefill",
    response_model=TemplatePrefillResponse,
    summary="Значения переменных шаблона для диалога",
    response_description="Переменные с подписями и найденными значениями",
    responses={404: {"description": "Диалог или шаблон не найден"}},
)
async def prefill_template(
    conversation_id: uuid.UUID,
    template_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    lang: Lang = Depends(get_request_lang),
    use_case: PrefillTemplateUseCase = Depends(get_prefill_template_use_case),
) -> TemplatePrefillResponse:
    """Предзаполнение формы отправки шаблона: по источнику каждой переменной
    бэк берёт значение из карточки пациента этого диалога, данных клиники,
    ближайшей будущей записи (дата `ДД.ММ.ГГГГ`, время, врач) и долга (сом).
    Не нашлось или источник `manual` — `value: null`, поле заполняет
    сотрудник. Значения можно поправить: отправка (`POST …/templates`)
    по-прежнему берёт их из запроса."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    prefill = await use_case.execute(context.clinic_id, conversation_id, template_id, lang=lang)
    return TemplatePrefillResponse.from_prefill(prefill)
