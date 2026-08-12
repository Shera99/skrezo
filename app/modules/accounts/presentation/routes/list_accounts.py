"""Роут: GET /admin/accounts — все аккаунты платформы (фильтры + пагинация)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.accounts.application.use_cases.list_accounts import ListAccountsUseCase
from app.modules.accounts.presentation.deps import get_list_accounts_use_case
from app.modules.accounts.presentation.schemas import AccountListItem, AccountsResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_PAGE_SIZE = 25


@router.get(
    "/admin/accounts",
    response_model=AccountsResponse,
    summary="Список аккаунтов платформы",
    response_description="Страница аккаунтов, текущая страница и общее число под фильтром",
)
async def list_accounts(
    query: str | None = Query(default=None, description="Поиск по ФИО и телефону"),
    clinic_id: uuid.UUID | None = Query(
        default=None,
        alias="clinicId",
        description="Только люди этой клиники — включая исключённых из неё",
    ),
    active: bool | None = Query(
        default=None,
        description=(
            "`true` — работает хотя бы в одной клинике; `false` — исключён "
            "(вместе с `clinicId` — исключён именно из неё); пусто — все"
        ),
    ),
    is_platform_admin: bool | None = Query(
        default=None,
        alias="isPlatformAdmin",
        description="`true` — только команда Skrezo, `false` — только клиентские аккаунты",
    ),
    page: int = Query(default=1, ge=1, description="Страница (1-based)"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListAccountsUseCase = Depends(get_list_accounts_use_case),
) -> AccountsResponse:
    """Все люди платформы одним списком: и команда Skrezo, и сотрудники клиник.

    Строка — аккаунт, а не членство: человек может работать в нескольких
    клиниках, и дублировать его на строку в каждой означало бы, что `totalCount`
    больше не отвечает на вопрос «сколько всего аккаунтов». Где он работает и
    откуда исключён — во вложенном `memberships`.

    Аккаунт сам по себе не удаляется: исключают из клиники, то есть членство.
    Поэтому «удалён» здесь — это `memberships[].active = false`, а
    `hasActiveMembership` сводит это в одну колонку списка.
    """
    result = await use_case.execute(
        query=query,
        clinic_id=clinic_id,
        active=active,
        is_platform_admin=is_platform_admin,
        page=page,
        page_size=_PAGE_SIZE,
    )
    return AccountsResponse(
        list=[AccountListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
