"""Use-case: вход по телефону и паролю — только для команды Skrezo.

Паролем входят исключительно аккаунты с ``is_platform_admin`` (админ-панель
``/admin/*``). Все остальные — владельцы клиник, менеджеры, работники, соло-врачи
— входят по OTP (``/auth/otp/request`` → ``/auth/otp/verify``).

Ограничение проверяется здесь, а не на слое роутов: пароль у не-админа не должен
пускать в систему, даже если хеш каким-то образом остался в базе.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import verify_password
from app.modules.identity.application.services.client_token_service import (
    ClientTokenService,
    IssuedClientTokens,
)
from app.modules.identity.domain.models import MembershipContext
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.shared.db.models.account import Account
from app.shared.domain.value_objects import normalize_phone


@dataclass(slots=True)
class LoginResult:
    tokens: IssuedClientTokens
    account: Account
    memberships: list[MembershipContext]
    active_clinic_id: uuid.UUID | None
    must_change_password: bool
    is_platform_admin: bool


class LoginUseCase:
    def __init__(
        self,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        tokens: ClientTokenService,
    ) -> None:
        self._accounts = accounts
        self._memberships = memberships
        self._tokens = tokens

    async def execute(self, phone_raw: str, password: str) -> LoginResult:
        phone = normalize_phone(phone_raw)
        account = await self._accounts.get_by_phone(phone)
        # Единый ответ на «нет аккаунта / не админ / пароль не задан / неверный
        # пароль» — чтобы не раскрывать, какие номера зарегистрированы и какие из
        # них принадлежат команде Skrezo.
        if (
            account is None
            or not account.is_platform_admin
            or account.password_hash is None
            or not verify_password(password, account.password_hash)
        ):
            raise UnauthorizedError("Неверный телефон или пароль", code="invalid_credentials")
        # Отдельным ответом, а не «неверный пароль»: пароль человек ввёл верный,
        # и молчать о причине — значит слать его перебирать пароли впустую.
        if account.blocked_at is not None:
            raise ForbiddenError("Аккаунт заблокирован", code="account_blocked")

        memberships = await self._memberships.list_active_context(account.id)
        # Одно членство → сразу клиника-скоуп токен; несколько → выбор клиники.
        active = memberships[0] if len(memberships) == 1 else None
        tokens = await self._tokens.issue(
            str(account.id),
            str(active.clinic_id) if active else None,
            str(active.role_id) if active else None,
            platform_admin=account.is_platform_admin,
        )
        return LoginResult(
            tokens=tokens,
            account=account,
            memberships=memberships,
            active_clinic_id=active.clinic_id if active else None,
            must_change_password=account.must_change_password,
            is_platform_admin=account.is_platform_admin,
        )
