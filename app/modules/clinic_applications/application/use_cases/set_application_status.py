"""Use-case: смена статуса заявки (рассмотрен / на рассмотрении / отклонён)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.shared.db.enums import ClinicApplicationStatus


class SetApplicationStatusUseCase:
    def __init__(
        self, session: AsyncSession, applications: ClinicApplicationRepository
    ) -> None:
        self._session = session
        self._applications = applications

    async def execute(
        self,
        application_id: uuid.UUID,
        status: ClinicApplicationStatus,
        actor_id: uuid.UUID,
    ) -> ApplicationRow:
        """Поставить статус и запомнить, кто из команды Skrezo это сделал.

        Переходы свободные (в любой статус из любого): решение по заявке может
        пересматриваться, а история изменений пишется в аудит платформы.
        """
        updated = await self._applications.set_status(application_id, status, actor_id)
        if not updated:
            raise NotFoundError("Заявка не найдена", code="application_not_found")
        await self._session.commit()

        row = await self._applications.get_row(application_id)
        assert row is not None  # только что обновили
        return row
