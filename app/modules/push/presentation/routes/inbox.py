"""Роуты инбокса: лента, счётчик непрочитанного, отметки прочтения.

Инбокс — источник истины «что произошло»; пуш и сокет на него лишь указывают.
Право не требуется: лента и без того СВОЯ (снимок на аккаунт+клинику), чужие
записи не видны и не отмечаются в принципе — фильтр в репозитории, не в UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import get_clinic_context
from app.modules.push.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.push.infrastructure.repositories.inbox_repository import InboxRepository
from app.modules.push.infrastructure.repositories.push_job_repository import (
    PushJobRepository,
)
from app.modules.push.presentation.schemas import (
    InboxDisplayedRequest,
    InboxDisplayedResponse,
    InboxItemOut,
    InboxReadResponse,
    InboxResponse,
)
from app.shared.auth import ClientContext
from app.shared.cabinet_links import event_url
from app.shared.realtime import publish_realtime

router = APIRouter()


def get_inbox_repository(session: AsyncSession = Depends(get_session)) -> InboxRepository:
    return InboxRepository(session)


_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Запись не найдена (или принадлежит другому сотруднику)",
        "content": {
            "application/json": {"example": {"error": "inbox_item_not_found"}}
        },
    },
}


@router.get(
    "/clinic/inbox",
    response_model=InboxResponse,
    summary="Инбокс уведомлений",
    response_description="Страница ленты + счётчик непрочитанного",
)
async def list_inbox(
    unread: bool = Query(default=False, description="Только непрочитанные"),
    before_id: int | None = Query(
        default=None, alias="beforeId", description="Курсор «старше этого id»"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    context: ClientContext = Depends(get_clinic_context),
    inbox: InboxRepository = Depends(get_inbox_repository),
) -> InboxResponse:
    """Своя лента событий клиники: новые сверху, `unreadCount` — бейдж.

    Прочитанность живёт на сервере: отметил на планшете — бейдж упадёт и на
    телефоне (клиент дотягивает по `unreadCount`, с Ф4 — мгновенно по сокету).
    У записи есть `url` — раздел кабинета для перехода (переписка, расписание,
    карточка пациента, оплата); приложение по-прежнему может строить переход
    само по `type` и `entityId`.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await inbox.list_page(
        context.account_id,
        context.clinic_id,
        unread_only=unread,
        before_id=before_id,
        limit=limit,
    )
    count = await inbox.unread_count(context.account_id, context.clinic_id)
    patients = await inbox.patient_ids_by_payment(
        [i.entity_id for i in items if i.entity_type == "payment" and i.entity_id is not None]
    )
    return InboxResponse(
        items=[
            InboxItemOut.from_orm_item(
                i,
                url=event_url(
                    i.type,
                    i.entity_type,
                    i.entity_id,
                    patient_id=patients.get(i.entity_id) if i.entity_id is not None else None,
                ),
            )
            for i in items
        ],
        unread_count=count,
    )


@router.post(
    "/clinic/inbox/{item_id}/read",
    response_model=InboxReadResponse,
    summary="Отметить запись прочитанной",
    response_description="Счётчик непрочитанного после отметки",
    responses=_NOT_FOUND,
)
async def mark_read(
    item_id: int,
    context: ClientContext = Depends(get_clinic_context),
    inbox: InboxRepository = Depends(get_inbox_repository),
    session: AsyncSession = Depends(get_session),
) -> InboxReadResponse:
    """Идемпотентно: повторная отметка уже прочитанной записи ничего не меняет."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    item = await inbox.mark_read(context.account_id, context.clinic_id, item_id)
    if item is None:
        raise NotFoundError("Запись не найдена", code="inbox_item_not_found")
    # Синхронизация между устройствами (Д5): прочитал на планшете — сигнал
    # уронит бейдж на телефоне. Публикация в транзакции — уйдёт на коммите.
    await publish_realtime(session, context.clinic_id, "inbox.read", {"inboxId": item_id})
    await session.commit()
    count = await inbox.unread_count(context.account_id, context.clinic_id)
    return InboxReadResponse(unread_count=count)


@router.post(
    "/clinic/inbox/read-all",
    response_model=InboxReadResponse,
    summary="Отметить всё прочитанным",
    response_description="Счётчик после отметки (всегда 0)",
)
async def mark_all_read(
    context: ClientContext = Depends(get_clinic_context),
    inbox: InboxRepository = Depends(get_inbox_repository),
    session: AsyncSession = Depends(get_session),
) -> InboxReadResponse:
    """«Пока вас не было: …» — одна кнопка вместо тапа по каждой записи."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    await inbox.mark_all_read(context.account_id, context.clinic_id)
    # inboxId: null — «перечитай инбокс целиком», точечного id у read-all нет.
    await publish_realtime(session, context.clinic_id, "inbox.read", {"inboxId": None})
    await session.commit()
    return InboxReadResponse(unread_count=0)


@router.post(
    "/clinic/inbox/displayed",
    response_model=InboxDisplayedResponse,
    summary="Подтвердить показ записей (отменяет отложенные пуши)",
    response_description="Сколько отложенных пушей отменено",
)
async def confirm_displayed(
    body: InboxDisplayedRequest,
    context: ClientContext = Depends(get_clinic_context),
    session: AsyncSession = Depends(get_session),
) -> InboxDisplayedResponse:
    """Приложение подтверждает, что записи ОТРИСОВАНЫ на переднем плане.

    Это и есть отмена normal-пуша (ТЗ-СУ §3): человек уже увидел событие
    живьём — толкать его пушем через 10 секунд незачем. Подтверждать можно
    пачкой (экран отрисовал ленту) и повторно (идемпотентно); чужие id молча
    пропускаются. Уже отправленный пуш не отзывается — это невозможно ни на
    одной платформе.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    jobs = PushJobRepository(session)
    canceled = await jobs.cancel_for_items(context.account_id, context.clinic_id, body.ids)
    # Телеметрия: показ отправленных пушей — по нему считается доставляемость.
    await DeliveryRepository(session).mark_displayed(
        context.account_id, context.clinic_id, body.ids
    )
    await session.commit()
    return InboxDisplayedResponse(canceled=canceled)
