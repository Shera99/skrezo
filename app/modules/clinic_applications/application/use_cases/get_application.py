"""Use-case: карточка заявки клиники."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)


class GetClinicApplicationUseCase:
    def __init__(self, applications: ClinicApplicationRepository) -> None:
        self._applications = applications

    async def execute(self, application_id: uuid.UUID) -> ApplicationRow:
        row = await self._applications.get_row(application_id)
        if row is None:
            raise NotFoundError("Заявка не найдена", code="application_not_found")
        return row
