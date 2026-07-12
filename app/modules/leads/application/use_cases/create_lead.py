"""Use-case: приём лида «Больше одного» (публичный) с дедупом по телефону."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.leads.application.services.team_notification_service import (
    TeamNotificationService,
)
from app.modules.leads.infrastructure.repositories.lead_repository import LeadRepository
from app.shared.db.models.lead import Lead
from app.shared.domain.value_objects import normalize_phone


class CreateLeadUseCase:
    def __init__(
        self,
        session: AsyncSession,
        leads: LeadRepository,
        notifier: TeamNotificationService,
    ) -> None:
        self._session = session
        self._leads = leads
        self._notifier = notifier

    async def execute(
        self,
        *,
        full_name: str,
        phone_raw: str,
        clinic_name: str,
        doctors_count_range: str | None,
        comment: str | None,
    ) -> Lead:
        phone = normalize_phone(phone_raw)
        existing = await self._leads.get_by_phone(phone)
        if existing is not None:
            # Дедуп: один номер — одна заявка.
            raise ConflictError(
                "У нас уже есть заявка по этому номеру",
                code="lead_already_exists",
                extra={"createdAt": existing.created_at.isoformat()},
            )
        lead = await self._leads.create(
            full_name=full_name,
            phone=phone,
            clinic_name=clinic_name,
            doctors_count_range=doctors_count_range,
            comment=comment,
        )
        await self._session.commit()
        await self._notifier.notify_new_lead(lead)
        return lead
