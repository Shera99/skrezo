"""Use-case: смена статуса заявки (рассмотрен / на рассмотрении / отклонён)."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.shared.db.enums import ClinicApplicationStatus
from app.shared.db.models.clinic_application import PENDING_LEAD_INDEX_NAME


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

        Единственное исключение — возврат разобранного лида в ``pending``, когда по
        тому же номеру уже есть другой открытый лид: частичный уникальный индекс
        допускает один такой лид на номер, и админ получает ``409 open_lead_exists``
        вместо внутренней ошибки.
        """
        try:
            updated = await self._applications.set_status(application_id, status, actor_id)
        except IntegrityError as exc:
            await self._session.rollback()
            if PENDING_LEAD_INDEX_NAME not in str(exc.orig):
                raise
            open_lead_id = await self._applications.find_other_open_lead_id(application_id)
            if open_lead_id is None:
                raise
            raise ConflictError(
                "По этому номеру уже есть открытый лид — сначала разберите его",
                code="open_lead_exists",
                extra={"openApplicationId": str(open_lead_id)},
            ) from exc
        if not updated:
            raise NotFoundError("Заявка не найдена", code="application_not_found")
        await self._session.commit()

        row = await self._applications.get_row(application_id)
        assert row is not None  # только что обновили
        return row
