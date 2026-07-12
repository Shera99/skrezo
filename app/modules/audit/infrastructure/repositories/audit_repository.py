"""Репозиторий аудита: запись и чтение (фильтры + keyset-пагинация)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.domain.models import AuditEntry
from app.shared.db.models.account import Account
from app.shared.db.models.audit_log import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        actor_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        payload: dict[str, Any] | None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list(
        self,
        *,
        actor_id: uuid.UUID | None = None,
        clinic_id: uuid.UUID | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        cursor: tuple[datetime, uuid.UUID] | None = None,
        limit: int = 20,
    ) -> tuple[list[AuditEntry], tuple[datetime, uuid.UUID] | None]:
        stmt = select(
            AuditLog.id.label("id"),
            AuditLog.actor_id.label("actor_id"),
            Account.full_name.label("actor_login"),
            AuditLog.action.label("action"),
            AuditLog.entity_type.label("entity_type"),
            AuditLog.entity_id.label("entity_id"),
            AuditLog.payload.label("payload"),
            AuditLog.created_at.label("created_at"),
        ).outerjoin(Account, Account.id == AuditLog.actor_id)

        if actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == actor_id)
        if clinic_id is not None:
            stmt = stmt.where(AuditLog.entity_type == "clinic", AuditLog.entity_id == clinic_id)
        if from_dt is not None:
            stmt = stmt.where(AuditLog.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(AuditLog.created_at <= to_dt)
        if cursor is not None:
            stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < (cursor[0], cursor[1]))
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)

        rows = (await self._session.execute(stmt)).all()
        page = rows[:limit]
        items = [
            AuditEntry(
                id=r.id,
                actor_id=r.actor_id,
                actor_login=r.actor_login,
                action=r.action,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                payload=r.payload,
                created_at=r.created_at,
            )
            for r in page
        ]
        next_cursor: tuple[datetime, uuid.UUID] | None = None
        if len(rows) > limit and page:
            next_cursor = (page[-1].created_at, page[-1].id)
        return items, next_cursor
