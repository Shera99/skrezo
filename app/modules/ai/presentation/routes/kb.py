"""Редактор базы знаний (Ф6): документы, версии, откат, сводка токенов."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.kb import KbService
from app.modules.ai.presentation.deps import get_kb_service
from app.modules.ai.presentation.schemas import (
    CreateKbDocumentRequest,
    KbDocumentDetail,
    KbDocumentOut,
    KbLangUsageOut,
    KbListResponse,
    KbRollbackRequest,
    KbUsageOut,
    KbVersionOut,
    UpdateKbDocumentRequest,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Документ не найден или принадлежит другой клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "kb_document_not_found",
                    "message": "Документ базы знаний не найден",
                }
            }
        },
    },
}
_SAVE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    422: {
        "description": "Цена в тексте (ей место в прайсе), категория или язык вне словаря, "
        "`null` в обязательном поле",
        "content": {
            "application/json": {
                "example": {
                    "error": "kb_price_forbidden",
                    "message": "Цены в базу знаний не кладём",
                }
            }
        },
    },
}


def _clinic(context: ClientContext) -> uuid.UUID:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return context.clinic_id


def _usage_out(service_usage: Any) -> KbUsageOut:
    return KbUsageOut(
        by_lang={
            lang: KbLangUsageOut(docs=u.docs, tokens_estimate=u.tokens_estimate)
            for lang, u in service_usage.by_lang.items()
        },
        limit_tokens=service_usage.limit_tokens,
    )


async def _detail(
    service: KbService, clinic_id: uuid.UUID, document_id: uuid.UUID
) -> KbDocumentDetail:
    document, versions = await service.get_with_versions(clinic_id, document_id)
    usage = await service.usage(clinic_id)
    base = KbDocumentOut.from_orm_doc(document, in_prompt=document.id in usage.in_prompt)
    return KbDocumentDetail(
        **base.model_dump(),
        versions=[
            KbVersionOut(
                version_no=v.version_no,
                title=v.title,
                category=v.category,
                body=v.body,
                lang=v.lang,
                is_active=v.is_active,
                action=v.action,
                note=v.note,
                actor_id=v.actor_id,
                created_at=v.created_at,
            )
            for v in versions
        ],
    )


@router.get(
    "/clinic/kb",
    response_model=KbListResponse,
    summary="База знаний клиники со сводкой токенов",
    response_description="Страница документов (недавно изменённые сверху) и сводка по языкам",
)
async def list_kb_documents(
    lang: str | None = Query(default=None, pattern="^(ru|ky)$"),
    category: str | None = Query(
        default=None, pattern="^(logistics|aftercare|faq|policy|payment)$"
    ),
    active: bool | None = Query(default=None, description="Только активные / только выключенные"),
    q: str | None = Query(default=None, max_length=100, description="Поиск по заголовку и тексту"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
) -> KbListResponse:
    """Что знает бот, кроме прайса и расписания. Сводка — оценка токенов активных
    документов по языкам против порога 30 000: до него база уходит в промпт целиком,
    у каждого документа — признак `inPrompt`."""
    clinic_id = _clinic(context)
    rows, total = await service._repository.list_page(
        clinic_id,
        lang=lang,
        category=category,
        active=active,
        query=(q or "").strip() or None,
        page=page,
        page_size=page_size,
    )
    usage = await service.usage(clinic_id)
    return KbListResponse(
        list=[KbDocumentOut.from_orm_doc(d, in_prompt=d.id in usage.in_prompt) for d in rows],
        usage=_usage_out(usage),
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.post(
    "/clinic/kb",
    status_code=status.HTTP_201_CREATED,
    response_model=KbDocumentDetail,
    summary="Новый документ базы знаний",
    response_description="Документ с версией 1",
    responses={422: _SAVE_ERRORS[422]},
)
async def create_kb_document(
    body: CreateKbDocumentRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
    audit: AuditService = Depends(get_audit_service),
) -> KbDocumentDetail:
    """Только редко меняющийся текст: как доехать, подготовка к приёму, что после
    удаления, правила отмены. Цены, график и врачей бот берёт из живой базы —
    документ с ценой не сохранится (422 `kb_price_forbidden`). Знание действует
    со следующего хода бота."""
    clinic_id = _clinic(context)
    document = await service.create(
        clinic_id,
        actor_id=context.account_id,
        title=body.title,
        category=body.category,
        body=body.body,
        lang=body.lang,
        is_active=body.is_active,
    )
    await service._session.commit()
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="kb.create",
        entity_type="kb_document",
        entity_id=document.id,
        payload={"title": document.title, "lang": document.lang},
    )
    return await _detail(service, clinic_id, document.id)


@router.get(
    "/clinic/kb/{document_id}",
    response_model=KbDocumentDetail,
    summary="Документ с историей версий",
    responses=_NOT_FOUND,
)
async def get_kb_document(
    document_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
) -> KbDocumentDetail:
    """Версии — новые сверху; последняя всегда равна текущему тексту."""
    return await _detail(service, _clinic(context), document_id)


@router.patch(
    "/clinic/kb/{document_id}",
    response_model=KbDocumentDetail,
    summary="Правка документа — новая версия",
    responses=_SAVE_ERRORS,
)
async def update_kb_document(
    document_id: uuid.UUID,
    body: UpdateKbDocumentRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
    audit: AuditService = Depends(get_audit_service),
) -> KbDocumentDetail:
    """Передаются только изменяемые поля. `isActive:false` выключает документ для
    бота, не удаляя его. Каждое сохранение — версия, к которой можно вернуться."""
    clinic_id = _clinic(context)
    document = await service.update(
        clinic_id,
        document_id,
        actor_id=context.account_id,
        fields=body.model_dump(exclude_unset=True),
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="kb.update",
        entity_type="kb_document",
        entity_id=document.id,
        payload={"versionNo": document.version_no, "fields": sorted(body.model_fields_set)},
    )
    return await _detail(service, clinic_id, document.id)


@router.post(
    "/clinic/kb/{document_id}/rollback",
    response_model=KbDocumentDetail,
    summary="Вернуться к версии",
    responses={
        404: {
            "description": "Документ не найден (`kb_document_not_found`) или версии с таким "
            "номером нет (`kb_version_not_found`)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "kb_version_not_found",
                        "message": "Версии с таким номером нет",
                    }
                }
            },
        },
    },
)
async def rollback_kb_document(
    document_id: uuid.UUID,
    body: KbRollbackRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
    audit: AuditService = Depends(get_audit_service),
) -> KbDocumentDetail:
    """Содержимое версии N становится текущим — как новая версия с пометкой
    «к версии N»; история не переписывается."""
    clinic_id = _clinic(context)
    document = await service.rollback(
        clinic_id, document_id, actor_id=context.account_id, version_no=body.version_no
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="kb.rollback",
        entity_type="kb_document",
        entity_id=document.id,
        payload={"toVersionNo": body.version_no, "versionNo": document.version_no},
    )
    return await _detail(service, clinic_id, document.id)


@router.delete(
    "/clinic/kb/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ безвозвратно",
    responses=_NOT_FOUND,
)
async def delete_kb_document(
    document_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KbService = Depends(get_kb_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """Вместе с историей версий. Чтобы бот перестал использовать документ, но текст
    остался — `PATCH {isActive:false}`."""
    clinic_id = _clinic(context)
    await service.delete(clinic_id, document_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="kb.delete",
        entity_type="kb_document",
        entity_id=document_id,
        payload=None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
