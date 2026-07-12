"""Роут: GET /admin/clients — список клиентов (фильтры + offset-пагинация)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.clients.application.use_cases.list_clients import ListClientsUseCase
from app.modules.clients.presentation.deps import get_list_clients_use_case
from app.modules.clients.presentation.schemas import ClientListItem, ClientsResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import ClinicType, Lang, SubscriptionStatus
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_PAGE_SIZE = 25


@router.get(
    "/admin/clients",
    response_model=ClientsResponse,
    summary="Список клиентов",
    response_description="Страница клиентов, текущая страница и общее число под фильтром",
)
async def list_clients(
    type_: ClinicType | None = Query(default=None, alias="type"),
    status: SubscriptionStatus | None = Query(default=None),
    query: str | None = Query(default=None, description="Поиск: клиника/имя/телефон"),
    cities: list[uuid.UUID] | None = Query(default=None, description="Мультивыбор городов"),
    tariffs: list[str] | None = Query(
        default=None, description="Мультивыбор тарифов (по tariffName)"
    ),
    page: int = Query(default=1, ge=1, description="Страница (1-based)"),
    lang: Lang = Depends(get_request_lang),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListClientsUseCase = Depends(get_list_clients_use_case),
) -> ClientsResponse:
    result = await use_case.execute(
        lang=lang,
        type_=type_,
        status=status,
        city_ids=cities,
        tariff_names=tariffs,
        query=query,
        page=page,
        page_size=_PAGE_SIZE,
    )
    return ClientsResponse(
        list=[ClientListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
