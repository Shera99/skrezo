"""Use-case: ручное создание клиники/соло из админки (платформенный контур)."""

from __future__ import annotations

from app.modules.clinics.application.services.admin_notification_service import (
    AdminNotificationService,
)
from app.modules.clinics.application.services.provisioning_service import (
    ProvisionCommand,
    ProvisioningService,
    ProvisionResult,
)


class CreateClinicUseCase:
    def __init__(
        self,
        provisioning: ProvisioningService,
        notifier: AdminNotificationService,
    ) -> None:
        self._provisioning = provisioning
        self._notifier = notifier

    async def execute(self, command: ProvisionCommand) -> ProvisionResult:
        result = await self._provisioning.provision(command)
        await self._notifier.notify_account_created(result.account, result.clinic)
        return result
