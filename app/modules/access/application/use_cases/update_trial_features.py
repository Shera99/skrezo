"""Use-case: настроить набор триал-функций (админка Skrezo)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.modules.access.application.services.trial_policy import TrialPolicy
from app.modules.access.infrastructure.repositories.trial_policy_repository import (
    TrialPolicyRepository,
)
from app.shared.db.models.permission import Permission


@dataclass(slots=True)
class TrialFeatureUpdate:
    key: str
    in_trial: bool


class UpdateTrialFeaturesUseCase:
    def __init__(
        self, session: AsyncSession, repository: TrialPolicyRepository, trial: TrialPolicy
    ) -> None:
        self._session = session
        self._repo = repository
        self._trial = trial

    async def execute(self, updates: list[TrialFeatureUpdate]) -> list[Permission]:
        """Переключить ``inTrial`` у переданных прав; вернуть обновлённый каталог.

        Неизвестный ключ права → 422 (защита от опечаток в настройке). После
        коммита сбрасывается кеш триал-прав — ограничение применяется сразу.
        """
        for update in updates:
            found = await self._repo.set_in_trial(update.key, update.in_trial)
            if not found:
                raise ValidationAppError(
                    f"Право не найдено: {update.key}", code="permission_not_found"
                )
        await self._session.commit()
        await self._trial.invalidate_keys()
        return await self._repo.all_permissions()
