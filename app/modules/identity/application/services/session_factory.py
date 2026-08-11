"""Сборка сессии (токены + контекст) для уже аутентифицированного аккаунта.

Общая часть входов по OTP — то есть всех, кроме команды Skrezo: вход сотрудника
клиники, вход соло-врача и его регистрация. Логика скоупа та же, что в
``LoginUseCase``: одно активное членство → сразу токен со скоупом клиники,
несколько → без скоупа (клиент выбирает клинику ``/auth/select-clinic``).
"""

from __future__ import annotations

from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.application.use_cases.login import LoginResult
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.shared.db.models.account import Account


async def issue_session(
    account: Account, memberships_repo: MembershipRepository, tokens: ClientTokenService
) -> LoginResult:
    """Выдать токены аккаунту и собрать ``LoginResult`` с его клиниками."""
    memberships = await memberships_repo.list_active_context(account.id)
    active = memberships[0] if len(memberships) == 1 else None
    issued = await tokens.issue(
        str(account.id),
        str(active.clinic_id) if active else None,
        str(active.role_id) if active else None,
        platform_admin=account.is_platform_admin,
    )
    return LoginResult(
        tokens=issued,
        account=account,
        memberships=memberships,
        active_clinic_id=active.clinic_id if active else None,
        must_change_password=account.must_change_password,
        is_platform_admin=account.is_platform_admin,
    )
