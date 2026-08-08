"""Use-case: список прав каталога с флагом доступности на триале (админ-экран)."""

from __future__ import annotations

from app.modules.access.infrastructure.repositories.subscription_policy_repository import (
    SubscriptionPolicyRepository,
)
from app.shared.db.models.permission import Permission


class GetTrialFeaturesUseCase:
    def __init__(self, repository: SubscriptionPolicyRepository) -> None:
        self._repo = repository

    async def execute(self) -> list[Permission]:
        """Весь каталог прав (ключ, группа, описание, ``inTrial``) для настройки."""
        return await self._repo.all_permissions()
