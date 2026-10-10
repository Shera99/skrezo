"""Use-case: список заявок клиник для админки Skrezo."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.clinic_applications.domain.models import ApplicationsPage
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.shared.db.enums import ClinicApplicationStatus


@dataclass(frozen=True, slots=True)
class ApplicationsResult:
    """Страница заявок вместе со счётчиками по статусам (бейджи в админке)."""

    page: ApplicationsPage
    status_counts: dict[ClinicApplicationStatus, int]


class ListClinicApplicationsUseCase:
    def __init__(self, applications: ClinicApplicationRepository) -> None:
        self._applications = applications

    async def execute(
        self,
        *,
        status: ClinicApplicationStatus | None,
        phone_verified: bool | None = None,
        page: int,
        page_size: int,
    ) -> ApplicationsResult:
        """Заявки (свежие сверху) с фильтрами по статусу и подтверждению телефона.

        Счётчики по статусам учитывают ``phone_verified`` (но не ``status``),
        поэтому бейджи совпадают со списком, отфильтрованным по двери.
        """
        items, total = await self._applications.list_page(
            status=status,
            phone_verified=phone_verified,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        counts = await self._applications.status_counts(phone_verified=phone_verified)
        return ApplicationsResult(
            page=ApplicationsPage(
                items=items, total_count=total, current_page=page, page_size=page_size
            ),
            status_counts=counts,
        )
