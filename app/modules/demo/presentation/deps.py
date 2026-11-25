"""Зависимости демо-клиники."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.demo.application.services.demo_reset_service import (
    DemoResetService,
    build_demo_reset_service,
)
from app.modules.demo.application.use_cases.set_demo import SetDemoUseCase
from app.modules.demo.infrastructure.repositories.demo_repository import DemoRepository


def get_demo_reset_service(session: AsyncSession = Depends(get_session)) -> DemoResetService:
    return build_demo_reset_service(session)


def get_set_demo_use_case(session: AsyncSession = Depends(get_session)) -> SetDemoUseCase:
    return SetDemoUseCase(session, DemoRepository(session))
