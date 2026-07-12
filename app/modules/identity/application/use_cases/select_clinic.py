"""Use-case: выбор активной клиники (для мультиклиничных аккаунтов)."""

from __future__ import annotations

import uuid

from app.core.errors import ForbiddenError
from app.modules.identity.application.services.client_token_service import (
    ClientTokenService,
    IssuedClientTokens,
)
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)


class SelectClinicUseCase:
    def __init__(self, memberships: MembershipRepository, tokens: ClientTokenService) -> None:
        self._memberships = memberships
        self._tokens = tokens

    async def execute(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, *, platform_admin: bool = False
    ) -> IssuedClientTokens:
        membership = await self._memberships.get_active_context(account_id, clinic_id)
        if membership is None:
            raise ForbiddenError("Нет доступа к этой клинике", code="membership_not_found")
        return await self._tokens.issue(
            str(account_id),
            str(clinic_id),
            str(membership.role_id),
            platform_admin=platform_admin,
        )
