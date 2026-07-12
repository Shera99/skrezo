"""Use-case: вход сотрудника клиники по телефону и паролю.

Заменяет прежний OTP-вход. Проверяет пару ``phone`` + пароль; при единственной
клинике сразу выдаёт токен со скоупом клиники, при нескольких — без скоупа
(дальше клиент вызывает ``/auth/select-clinic``). Флаг ``must_change_password``
в результате сигналит клиенту, что пароль временный и требует смены.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import UnauthorizedError
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
        # Единый ответ на «нет аккаунта / пароль не задан / неверный пароль» —
        # чтобы не раскрывать, какие номера зарегистрированы.
        if (
            account is None
            or account.password_hash is None
            or not verify_password(password, account.password_hash)
        ):
            raise UnauthorizedError("Неверный телефон или пароль", code="invalid_credentials")

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
