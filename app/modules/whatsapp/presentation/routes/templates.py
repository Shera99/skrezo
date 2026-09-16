"""Роуты шаблонов WhatsApp: список, синхронизация из Meta, отправка в диалог."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.config import settings
from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.whatsapp.application.use_cases.send_template import SendTemplateUseCase
from app.modules.whatsapp.application.use_cases.sync_templates import SyncTemplatesUseCase
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.modules.whatsapp.presentation.deps import (
    get_send_template_use_case,
    get_sync_templates_use_case,
    get_whatsapp_repository,
)
from app.modules.whatsapp.presentation.schemas import (
    MessageOut,
    SendTemplateRequest,
    TemplateOut,
    TemplatesResponse,
)
from app.shared.auth import ClientContext

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


@router.get(
    "/clinic/whatsapp/templates",
    response_model=TemplatesResponse,
    summary="Шаблоны WhatsApp клиники",
    response_description="Свои шаблоны + общие платформы",
)
async def list_templates(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> TemplatesResponse:
    """Локальный каталог шаблонов — то, что интерфейс показывает вместо поля
    ввода при закрытом окне. Отправлять можно только `status: approved`.
    Служебные шаблоны Meta (`WA_SERVICE_TEMPLATE_NAMES`, тестовый `hello_world`)
    в каталоге не показываются."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await repository.list_templates(
        context.clinic_id, exclude_names=settings.wa_service_template_names
    )
    return TemplatesResponse(list=[TemplateOut.from_orm_template(t) for t in items])


@router.post(
    "/clinic/whatsapp/templates/sync",
    response_model=TemplatesResponse,
    summary="Синхронизировать шаблоны из кабинета Meta",
    response_description="Каталог после сверки",
    responses=_SYNC_ERRORS,
)
async def sync_templates(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: SyncTemplatesUseCase = Depends(get_sync_templates_use_case),
) -> TemplatesResponse:
    """Полная сверка с кабинетом Meta по WABA привязки: новые появляются,
    удалённые пропадают, статусы обновляются. Общие шаблоны платформы сверка
    не трогает. Дёргать после правок шаблонов в кабинете — расписания нет.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await use_case.execute(context.clinic_id)
    return TemplatesResponse(list=[TemplateOut.from_orm_template(t) for t in items])


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
