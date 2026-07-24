"""Use-case: профиль текущего клиента (аккаунт, активная клиника, права)."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import UnauthorizedError
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.application.services.trial_policy import TrialPolicy
from app.modules.access.domain.permissions import ALL_PERMISSION_KEYS
from app.modules.identity.domain.models import MembershipContext
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.shared.auth import ClientContext
from app.shared.db.models.account import Account


@dataclass(slots=True)
class MeResult:
    account: Account
    active: MembershipContext | None
    permissions: list[str]
    memberships: list[MembershipContext]
    is_platform_admin: bool


class GetMeUseCase:
    def __init__(
        self,
        accounts: AccountRepository,
        memberships: MembershipRepository,
        resolver: PermissionResolver,
        trial: TrialPolicy,
    ) -> None:
        self._accounts = accounts
        self._memberships = memberships
        self._resolver = resolver
        self._trial = trial

    async def execute(self, context: ClientContext) -> MeResult:
        account = await self._accounts.get_by_id(context.account_id)
        if account is None:
            raise UnauthorizedError("Аккаунт не найден", code="invalid_token")

        memberships = await self._memberships.list_active_context(account.id)
        active: MembershipContext | None = None
        permissions: list[str] = []
        if context.role_id is not None:
            active = next(
                (
                    m
                    for m in memberships
                    if m.role_id == context.role_id and m.clinic_id == context.clinic_id
                ),
                None,
            )
            resolved = await self._resolver.resolve(context.role_id)
            if resolved is not None:
                permissions = (
                    sorted(ALL_PERMISSION_KEYS)
                    if resolved.is_owner
                    else sorted(resolved.permissions)
                )
                # На триале клиент видит только доступные функции — как и enforce'ит guard.
                permissions = await self._trial.filter_permitted(context.clinic_id, permissions)
        return MeResult(account, active, permissions, memberships, account.is_platform_admin)
