"""Зависимости модуля дашборда."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.dashboard.application.use_cases.get_summary import GetSummaryUseCase
from app.modules.dashboard.infrastructure.repositories.dashboard_repository import (
    DashboardRepository,
)


def get_dashboard_repository(session: AsyncSession = Depends(get_session)) -> DashboardRepository:
    return DashboardRepository(session)


def get_summary_use_case(
    dashboard: DashboardRepository = Depends(get_dashboard_repository),
) -> GetSummaryUseCase:
    return GetSummaryUseCase(dashboard)
