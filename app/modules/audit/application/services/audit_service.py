"""Сервис записи аудита.

Пишет запись и коммитит отдельной транзакцией (аудит — сайд-лог; вызывается из
роутов после успешной мутации).

Два контура пишут в один лог, но по-разному: команда Skrezo — без ``clinic_id``
(принадлежность видна по сущности ``clinic``), сотрудник клиники — всегда с ним.
Клиничный журнал отбирается именно по этой колонке, поэтому забыть её значит
сделать запись невидимой для клиники — отсюда отдельный метод
:meth:`record_clinic`, где её нельзя пропустить.
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
        """Действие команды Skrezo (платформенный журнал)."""
        await self._repo.add(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        await self._session.commit()

    async def record_clinic(
        self,
        *,
        actor_id: uuid.UUID,
        clinic_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Действие сотрудника клиники (журнал клиники).

        ``clinic_id`` обязателен: он и отбирает записи в журнал клиники, и
        держит изоляцию между клиниками.
        """
        await self._repo.add(
            actor_id=actor_id,
            clinic_id=clinic_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        await self._session.commit()
