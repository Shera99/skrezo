"""Сервис записи аудита.

Пишет запись и коммитит отдельной транзакцией (аудит — сайд-лог; вызывается из
роутов платформенной зоны после успешной мутации).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.infrastructure.repositories.audit_repository import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession, repository: AuditRepository) -> None:
        self._session = session
        self._repo = repository

    async def record(
        self,
        *,
        actor_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._repo.add(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        await self._session.commit()
