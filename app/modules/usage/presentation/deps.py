"""Зависимости модуля учёта использования."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.usage.application.use_cases.get_usage import GetUsageUseCase
from app.modules.usage.infrastructure.repositories.usage_repository import UsageRepository


def get_usage_use_case(session: AsyncSession = Depends(get_session)) -> GetUsageUseCase:
    return GetUsageUseCase(UsageRepository(session))
