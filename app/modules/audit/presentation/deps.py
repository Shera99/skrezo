"""Зависимости модуля аудита."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.application.use_cases.list_audit import ListAuditUseCase
from app.modules.audit.infrastructure.repositories.audit_repository import AuditRepository


def get_audit_repository(session: AsyncSession = Depends(get_session)) -> AuditRepository:
    return AuditRepository(session)


def get_audit_service(
    session: AsyncSession = Depends(get_session),
    audit: AuditRepository = Depends(get_audit_repository),
) -> AuditService:
    return AuditService(session, audit)


def get_list_audit_use_case(
    audit: AuditRepository = Depends(get_audit_repository),
) -> ListAuditUseCase:
    return ListAuditUseCase(audit)
