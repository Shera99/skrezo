"""Use-case: сотрудник правит свой профиль.

Меняются только имя и язык интерфейса. Телефон сюда намеренно не входит: он же
логин, и менять его себе сотрудник не должен — иначе работник уводит доступ к
аккаунту у клиники. Для этого есть `PUT /clinic/staff/{id}` у владельца и
менеджера.

Аккаунт **глобальный**: врач может работать в нескольких клиниках, и имя
поменяется во всех сразу. Это осознанно — человек один, и разные имена в разных
клиниках путали бы и расписание, и журнал действий.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.shared.db.models.account import Account
from app.shared.db.updates import apply_updates

#: Что сотруднику позволено менять в себе (allowlist, не «всё, что прислали»).
_UPDATABLE_FIELDS = frozenset({"full_name", "lang"})
#: Из них — NOT NULL в БД: явный ``null`` сюда не пройдёт.
_NOT_NULL_FIELDS = frozenset({"full_name", "lang"})


class UpdateProfileUseCase:
    def __init__(self, session: AsyncSession, accounts: AccountRepository) -> None:
        self._session = session
        self._accounts = accounts

    async def execute(self, account_id: uuid.UUID, fields: dict[str, Any]) -> Account:
        account = await self._accounts.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Аккаунт не найден", code="account_not_found")

        apply_updates(
            account,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="профиля",
        )
        await self._session.commit()
        return account
