"""Зависимости модуля лидов."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.modules.leads.application.services.team_notification_service import (
    TeamNotificationService,
)
from app.modules.leads.application.use_cases.create_lead import CreateLeadUseCase
from app.modules.leads.infrastructure.repositories.lead_repository import LeadRepository


def get_lead_repository(session: AsyncSession = Depends(get_session)) -> LeadRepository:
    return LeadRepository(session)


def get_team_notification_service() -> TeamNotificationService:
    return TeamNotificationService(settings.telegram_team_bot_token, settings.telegram_team_chat_id)


def get_create_lead_use_case(
    session: AsyncSession = Depends(get_session),
    leads: LeadRepository = Depends(get_lead_repository),
    notifier: TeamNotificationService = Depends(get_team_notification_service),
) -> CreateLeadUseCase:
    return CreateLeadUseCase(session, leads, notifier)
