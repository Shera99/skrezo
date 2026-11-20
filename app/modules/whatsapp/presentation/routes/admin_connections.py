"""Роут Skrezo: GET /admin/whatsapp/connections — подключения WhatsApp всех клиник.

Клиники, подключившие номер сами через окно Meta, в кабинете Meta видны только
как чужие WABA, которыми поделились с Skrezo. Здесь команда видит их вместе с
привязанными вручную: какая клиника, какой номер, как подключён и работает ли.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.whatsapp.application.use_cases.list_connections import (
    ListWhatsAppConnectionsUseCase,
)
from app.modules.whatsapp.domain.connection import ConnectionState
from app.modules.whatsapp.presentation.deps import get_list_connections_use_case
from app.modules.whatsapp.presentation.schemas import (
    WhatsAppConnectionListItem,
    WhatsAppConnectionsResponse,
    WhatsAppConnectionsSummaryOut,
)

router = APIRouter()

_PAGE_SIZE = 25

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Нет токена или он невалиден",
        "content": {
            "application/json": {
                "example": {"error": "invalid_token", "message": "Невалидный токен"}
            }
        },
    },
    403: {
        "description": "Токен не администратора Skrezo",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_platform_admin",
                    "message": "Требуется доступ администратора Skrezo",
                }
            }
        },
    },
    422: {
        "description": "Неизвестное значение фильтра или страница меньше 1",
        "content": {
            "application/json": {
                "example": {
                    "error": "validation_error",
                    "message": "Тело запроса не прошло валидацию",
                }
            }
        },
    },
}


@router.get(
    "/admin/whatsapp/connections",
    response_model=WhatsAppConnectionsResponse,
    summary="Подключения WhatsApp клиник",
    response_description="Страница подключений и числа для вкладок",
    responses=_ERRORS,
)
async def admin_list_connections(
    connected_via: Literal["manual", "embedded_signup"] | None = Query(
        default=None,
        alias="connectedVia",
        description="`embedded_signup` — подключили сами; `manual` — привязала команда",
    ),
    state: ConnectionState | None = Query(
        default=None,
        description="`working` / `needs_reconnect` / `disconnected`",
    ),
    query: str | None = Query(
        default=None,
        max_length=100,
        description="Поиск: клиника, номер (можно цифрами без «+»), имя бизнеса у Meta",
    ),
    page: int = Query(default=1, ge=1, description="Страница (1-based), по 25 строк"),
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListWhatsAppConnectionsUseCase = Depends(get_list_connections_use_case),
) -> WhatsAppConnectionsResponse:
    """Номера WhatsApp всех клиник — и подключённые сами, и привязанные командой.

    Свежие подключения сверху. `state` показывает, работает ли номер у нас:
    `needs_reconnect` — Meta больше не пускает к номеру (клиника отозвала
    доступ, перерегистрировала номер на другом телефоне, удалила аккаунт),
    причина и текст — в `needsReconnect`. Отключённые номера (`disconnected`)
    остаются в списке: строка и история переписки сохраняются.

    `summary` — числа по всем подключениям для вкладок, фильтры на них не
    влияют. Секреты (токены клиник, PIN) не отдаются никогда.
    """
    result = await use_case.execute(
        connected_via=connected_via,
        state=state,
        query=query,
        page=page,
        page_size=_PAGE_SIZE,
    )
    s = result.summary
    return WhatsAppConnectionsResponse(
        list=[WhatsAppConnectionListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
        summary=WhatsAppConnectionsSummaryOut(
            total=s.total,
            working=s.working,
            needs_reconnect=s.needs_reconnect,
            disconnected=s.disconnected,
            embedded_signup=s.embedded_signup,
            manual=s.manual,
        ),
    )
