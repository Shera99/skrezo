"""Use-case: мастер первичной настройки пройден — ставим аккаунту `onboarded`.

Флаг живёт на **аккаунте**, а не на клинике: человек проходит мастер один раз, и
при входе с другого телефона приложение берёт его с сервера (`account.onboarded`
в `/auth/me` и ответе входа). Поэтому флаг общий для всех клиник человека.

Проверок заполненности (город, специализация, график, прайс) здесь **нет**
намеренно: приложение молча глотает ошибки этой ручки, и блокирующая проверка
вернула бы тот самый баг — мастер начинался бы заново после каждого входа.
Обратного сброса через клиентскую ручку тоже нет.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.shared.db.models.account import Account

logger = structlog.get_logger(__name__)


class CompleteOnboardingUseCase:
    def __init__(self, session: AsyncSession, accounts: AccountRepository) -> None:
        self._session = session
        self._accounts = accounts

    async def execute(self, account_id: uuid.UUID) -> Account:
        """Выставить `onboarded=true`; повторный вызов ничего не пишет (идемпотентно)."""
        account = await self._accounts.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Аккаунт не найден", code="account_not_found")

        if account.onboarded:
            return account

        account.onboarded = True
        await self._session.commit()
        logger.info("account_onboarded", account_id=str(account.id))
        return account
